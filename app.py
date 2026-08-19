"""Audiobook Translation Review Platform. Language-first navigation; MongoDB persistence;
pipeline runs synchronously in the request; results rendered dynamically from the episode doc.
"""
import logging
from functools import wraps

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, abort, Response

from core import db, storage
from core.exports import build_html_export_zip, build_xlsx_export
from core.jobs import start_pipeline
from core.jobs import is_running
from config.languages import SUPPORTED_LANGUAGES, get_language, has_tts_backend

app = Flask(__name__)
try:
    db.ensure_indexes()
except Exception as exc:
    # Don't crash the whole process on a transient DB hiccup at boot; retried
    # lazily on first real request via get_db()/episodes_collection().
    app.logger.warning("ensure_indexes() failed at startup: %s", exc)


def _row_class(row: dict) -> str:
    if row["speaker"].lower() == "narrator":
        return "flag-narrator"
    if row.get("review_flag") == "note":
        return "flag-note"
    return ""


app.jinja_env.filters["row_class"] = _row_class


@app.url_value_preprocessor
def _validate_comment_target(endpoint, values):
    if values and "target" in values and values["target"] not in db.COMMENT_TARGETS:
        abort(404)


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
        return render_template("upload.html", lang=lang, tts_available=has_tts_backend(lang_code))

    english_file = request.files.get("english_docx")
    translated_file = request.files.get("translated_docx")
    title = request.form.get("title", "").strip() or "Untitled Episode"

    if not english_file or not translated_file:
        return render_template("upload.html", lang=lang, error="Both files are required.",
                                tts_available=has_tts_backend(lang_code))

    episode_id = db.create_episode(title=title, target_lang=lang_code, target_lang_name=lang["name"])
    storage.save_bytes(episode_id, "uploads/english.docx", english_file.read())
    storage.save_bytes(episode_id, "uploads/translated.docx", translated_file.read())

    start_pipeline(episode_id)

    return redirect(url_for("episode_view", episode_id=episode_id))


RESULTS_VIEWABLE_STATUSES = {"tts", "done"}


@app.route("/episode/<episode_id>")
def episode_view(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    if episode["status"] not in RESULTS_VIEWABLE_STATUSES:
        return render_template("progress.html", episode=episode)
    verified, total = db.verification_counts(episode_id)
    return render_template("episode.html", episode=episode, standalone=False,
                            verified_rows=verified, total_rows=total,
                            audio_in_progress=episode["status"] != "done")


ACTIVE_STATUSES = {"parsing", "translating_titles", "reviewing", "finding_difficult_words", "tts"}


@app.route("/episode/<episode_id>/status")
def episode_status(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    progress = db.progress_counts(episode_id, episode)
    verified = progress.pop("verified_rows")
    stalled = episode["status"] in ACTIVE_STATUSES and not is_running(episode_id)
    return jsonify({
        "status": episode["status"],
        "error_message": episode.get("error_message"),
        "verified_rows": verified,
        "stalled": stalled,
        **progress,
    })


@app.route("/episode/<episode_id>/audio-status")
def episode_audio_status(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    return jsonify({
        "status": episode["status"],
        "rows": [
            {**r, "audio_url": url_for("episode_audio", episode_id=episode_id, filename=r["audio_path"])
                              if r.get("audio_path") else None}
            for r in db.audio_statuses(episode_id)
        ],
    })


@app.route("/episode/<episode_id>/retry", methods=["POST"])
def episode_retry(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    db.set_episode_status(episode_id, "uploaded", error_message=None)
    start_pipeline(episode_id)
    return redirect(url_for("episode_view", episode_id=episode_id))


@app.route("/episode/<episode_id>/delete", methods=["POST"])
def episode_delete(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    storage.delete_episode_files(episode_id)
    db.delete_episode(episode_id)
    return redirect(url_for("lang_dashboard", lang_code=episode["target_lang"]))


@app.route("/episode/<episode_id>/audio/<path:filename>")
def episode_audio(episode_id, filename):
    data = storage.read_bytes(episode_id, f"audio/{filename}")
    return Response(data, mimetype="audio/mpeg")


@app.route("/episode/<episode_id>/row/<int:sr_no>", methods=["POST"])
def update_row(episode_id, sr_no):
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
        verified, total = db.verification_counts(episode_id)
        return jsonify({"ok": True, "verified_rows": verified, "total_rows": total})
    return redirect(url_for("episode_view", episode_id=episode_id))


def _row_not_found_as_404(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except ValueError:
            abort(404)
    return wrapped


@app.route("/episode/<episode_id>/row/<int:sr_no>/reviewer-text", methods=["POST"])
@_row_not_found_as_404
def update_reviewer_text(episode_id, sr_no):
    db.set_reviewer_text(episode_id, sr_no, request.form.get("text", ""))
    return _reviewer_text_response(episode_id, sr_no)


@app.route("/episode/<episode_id>/row/<int:sr_no>/reviewer-text/undo", methods=["POST"])
@_row_not_found_as_404
def undo_reviewer_text(episode_id, sr_no):
    db.move_reviewer_history(episode_id, sr_no, "undo")
    return _reviewer_text_response(episode_id, sr_no)


@app.route("/episode/<episode_id>/row/<int:sr_no>/reviewer-text/redo", methods=["POST"])
@_row_not_found_as_404
def redo_reviewer_text(episode_id, sr_no):
    db.move_reviewer_history(episode_id, sr_no, "redo")
    return _reviewer_text_response(episode_id, sr_no)


def _reviewer_text_response(episode_id, sr_no):
    row = db.get_row(episode_id, sr_no)
    return jsonify({
        "ok": True, "text": row["reviewer_text"],
        "can_undo": row["reviewer_history_index"] > 0,
        "can_redo": row["reviewer_history_index"] < len(row["reviewer_history"]) - 1,
    })


@app.route("/episode/<episode_id>/row/<int:sr_no>/complete", methods=["POST"])
def update_reviewer_complete(episode_id, sr_no):
    complete = request.form.get("complete") == "true"
    db.set_reviewer_complete(episode_id, sr_no, complete)
    return jsonify({"ok": True, "complete": complete})


def _parse_anchor(form) -> dict | None:
    quote = form.get("anchor_quote", "")
    start = form.get("anchor_start", "")
    end = form.get("anchor_end", "")
    if quote and start.isdigit() and end.isdigit() and int(start) < int(end):
        return {"quote": quote, "start": int(start), "end": int(end)}
    return None


@app.route("/episode/<episode_id>/row/<int:sr_no>/comments/<target>", methods=["POST"])
def add_comment(episode_id, sr_no, target):
    text = request.form.get("text", "").strip()
    if not text:
        abort(400, "Comment text is required.")
    author = request.form.get("author", "Reviewer").strip() or "Reviewer"
    comment = db.add_comment(episode_id, sr_no, target, text, author, anchor=_parse_anchor(request.form))
    return jsonify({"ok": True, "comment": _serialize_comment(comment)})


@app.route("/episode/<episode_id>/row/<int:sr_no>/comments/<target>/<comment_id>/reply", methods=["POST"])
def reply_comment(episode_id, sr_no, target, comment_id):
    text = request.form.get("text", "").strip()
    if not text:
        abort(400, "Reply text is required.")
    author = request.form.get("author", "Reviewer").strip() or "Reviewer"
    reply = db.add_comment_reply(episode_id, sr_no, target, comment_id, text, author)
    return jsonify({"ok": True, "reply": _serialize_comment(reply)})


@app.route("/episode/<episode_id>/row/<int:sr_no>/comments/<target>/<comment_id>/resolve", methods=["POST"])
def resolve_comment(episode_id, sr_no, target, comment_id):
    resolved = request.form.get("resolved") == "true"
    db.set_comment_resolved(episode_id, sr_no, target, comment_id, resolved)
    return jsonify({"ok": True, "resolved": resolved})


@app.route("/episode/<episode_id>/row/<int:sr_no>/comments/<target>/<comment_id>/delete", methods=["POST"])
def delete_comment(episode_id, sr_no, target, comment_id):
    db.delete_comment(episode_id, sr_no, target, comment_id)
    return jsonify({"ok": True})


@app.route("/episode/<episode_id>/row/<int:sr_no>/comments/<target>/<comment_id>/reply/<reply_id>/delete", methods=["POST"])
def delete_comment_reply(episode_id, sr_no, target, comment_id, reply_id):
    db.delete_comment_reply(episode_id, sr_no, target, comment_id, reply_id)
    return jsonify({"ok": True})


def _serialize_comment(comment: dict) -> dict:
    return {**comment, "created_at": comment["created_at"].isoformat()}


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
