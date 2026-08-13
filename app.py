"""Audiobook Translation Review Platform. Language-first navigation; MongoDB persistence;
RQ background pipeline; results rendered dynamically from the episode doc.
"""
import os
import uuid

from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, send_file, abort
from werkzeug.utils import secure_filename

from core import db
from core.exports import build_html_export_zip, build_xlsx_export
from core.queue import enqueue_pipeline
from config.languages import SUPPORTED_LANGUAGES, get_language, has_tts_backend

UPLOAD_ROOT = os.environ.get("AUDIO_ROOT", os.path.join(os.path.dirname(__file__), "jobs"))

app = Flask(__name__)
db.ensure_indexes()


def _episode_dir(episode_id: str) -> str:
    path = os.path.join(UPLOAD_ROOT, episode_id)
    os.makedirs(path, exist_ok=True)
    return path


def _row_class(row: dict) -> str:
    if row["speaker"].lower() == "narrator":
        return "flag-narrator"
    if row.get("review_flag") == "note":
        return "flag-note"
    return ""


app.jinja_env.filters["row_class"] = _row_class


@app.route("/")
def landing():
    active_codes = set(db.list_target_languages())
    languages = [
        {**lang, "has_episodes": lang["code"] in active_codes}
        for lang in SUPPORTED_LANGUAGES
    ]
    return render_template("landing.html", languages=languages)


@app.route("/lang/<lang_code>")
def lang_dashboard(lang_code):
    lang = get_language(lang_code)
    if lang is None:
        abort(404)
    episodes = db.list_episodes_for_language(lang_code)
    for ep in episodes:
        ep["row_count"] = sum(len(c["rows"]) for c in ep["chapters"])
        ep["chapter_count"] = len(ep["chapters"])
    return render_template("lang_dashboard.html", lang=lang, episodes=episodes)


@app.route("/lang/<lang_code>/new", methods=["GET", "POST"])
def new_episode(lang_code):
    lang = get_language(lang_code)
    if lang is None:
        abort(404)

    if request.method == "GET":
        tts_available = has_tts_backend(lang_code, os.environ.get("TTS_BACKEND", "sherpa_onnx"))
        return render_template("upload.html", lang=lang, tts_available=tts_available)

    english_file = request.files.get("english_docx")
    translated_file = request.files.get("translated_docx")
    title = request.form.get("title", "").strip() or "Untitled Episode"

    if not english_file or not translated_file:
        return render_template("upload.html", lang=lang, error="Both files are required.",
                                tts_available=has_tts_backend(lang_code, os.environ.get("TTS_BACKEND", "sherpa_onnx")))

    episode_id = db.create_episode(title=title, target_lang=lang_code, target_lang_name=lang["name"])
    episode_dir = _episode_dir(episode_id)

    english_path = os.path.join(episode_dir, secure_filename(f"english_{uuid.uuid4().hex}.docx"))
    translated_path = os.path.join(episode_dir, secure_filename(f"translated_{uuid.uuid4().hex}.docx"))
    english_file.save(english_path)
    translated_file.save(translated_path)

    db.update_episode(episode_id, english_path=english_path, translated_path=translated_path)
    enqueue_pipeline(episode_id, english_path, translated_path)

    return redirect(url_for("episode_view", episode_id=episode_id))


@app.route("/episode/<episode_id>")
def episode_view(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    if episode["status"] != "done":
        return render_template("progress.html", episode=episode)
    verified, total = db.verification_counts(episode)
    return render_template("episode.html", episode=episode, standalone=False,
                            verified_rows=verified, total_rows=total)


@app.route("/episode/<episode_id>/status")
def episode_status(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    total_rows = sum(len(c["rows"]) for c in episode["chapters"])
    audio_done = sum(
        1 for c in episode["chapters"] for r in c["rows"] if r["audio_status"] == "done"
    )
    verified, _ = db.verification_counts(episode)
    return jsonify({
        "status": episode["status"],
        "error_message": episode.get("error_message"),
        "total_rows": total_rows,
        "audio_done": audio_done,
        "verified_rows": verified,
    })


@app.route("/episode/<episode_id>/retry", methods=["POST"])
def episode_retry(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    english_path = episode.get("english_path")
    translated_path = episode.get("translated_path")
    if not english_path or not translated_path:
        abort(400, "Original uploaded files are missing; cannot retry.")
    db.set_episode_status(episode_id, "uploaded", error_message=None)
    enqueue_pipeline(episode_id, english_path, translated_path)
    return redirect(url_for("episode_view", episode_id=episode_id))


@app.route("/episode/<episode_id>/audio/<path:filename>")
def episode_audio(episode_id, filename):
    audio_dir = os.path.join(_episode_dir(episode_id), "audio")
    return send_from_directory(audio_dir, filename)


@app.route("/episode/<episode_id>/row/<int:sr_no>", methods=["POST"])
def update_row(episode_id, sr_no):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    fields = {}
    if "review_comment" in request.form:
        fields["review_comment"] = request.form["review_comment"]
    if "review_flag" in request.form:
        flag = request.form["review_flag"]
        if flag not in ("ok", "note"):
            abort(400, "review_flag must be 'ok' or 'note'")
        fields["review_flag"] = flag
    if "human_verified" in request.form:
        fields["human_verified"] = request.form["human_verified"] == "true"
    if fields:
        db.update_row(episode_id, sr_no, **fields)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        updated = db.get_episode(episode_id)
        verified, total = db.verification_counts(updated)
        return jsonify({"ok": True, "verified_rows": verified, "total_rows": total})
    return redirect(url_for("episode_view", episode_id=episode_id))


@app.route("/episode/<episode_id>/export/html")
def export_html(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    if episode["status"] != "done":
        abort(400, "Episode is not finished processing yet.")
    rendered = render_template("episode.html", episode=episode, standalone=True)
    buffer = build_html_export_zip(episode_id, rendered)
    return send_file(buffer, mimetype="application/zip", as_attachment=True,
                      download_name=f"{episode['title']}_{episode['target_lang']}.zip")


@app.route("/episode/<episode_id>/export/xlsx")
def export_xlsx(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    if episode["status"] != "done":
        abort(400, "Episode is not finished processing yet.")
    buffer = build_xlsx_export(episode)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{episode['title']}_{episode['target_lang']}.xlsx",
    )


if __name__ == "__main__":
    app.run(debug=True)
