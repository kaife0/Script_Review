"""Audiobook Translation Review Platform. Language-first navigation; MongoDB persistence;
pipeline runs synchronously in the request; results rendered dynamically from the episode doc.
"""
import hashlib
import logging
import os
import subprocess
import threading
import time
from functools import wraps

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, abort, Response
from flask_compress import Compress
from bson import ObjectId
from werkzeug.datastructures import ContentRange
from werkzeug.utils import secure_filename

from core import db, storage, native_review
from core.alignment import align_lines
from core.exports import build_docx_export, build_xlsx_export
from core.jobs import start_pipeline, start_audio_repair
from core.jobs import is_running
from core.pipeline import episode_missing_audio, synthesize_row_audio, synthesize_title_audio
from core.llm_client import get_llm_client
from core.llm_review import translate_rows
from core.tts import transcode_to_mp3
from config.languages import SUPPORTED_LANGUAGES, get_language, has_tts_backend

MAX_REVIEWER_AUDIO_BYTES = 15 * 1024 * 1024
AUDIO_CHUNK_BYTES = 2 * 1024 * 1024
UPLOAD_PART_BYTES = 16 * 1024 * 1024
STALE_UPLOAD_SECONDS = 3 * 60 * 60

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000
Compress(app)
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


def _title_comments(obj: dict, target: str) -> list:
    """Safe accessor for title_comments on episodes/chapters that predate this field
    (e.g. episodes seeded directly in tests, or episodes never re-run through the pipeline)."""
    return obj.get("title_comments", {}).get(target, [])


app.jinja_env.globals["title_comments"] = _title_comments


def static_url(filename: str) -> str:
    mtime = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
    return url_for("static", filename=filename, v=mtime)


def audio_url(episode_id: str, audio_path: str | None, version=None) -> str | None:
    if not audio_path:
        return None
    if not version:
        return url_for("episode_audio", episode_id=episode_id, filename=audio_path)
    v = hashlib.md5(str(version).encode()).hexdigest()[:10]
    return url_for("episode_audio", episode_id=episode_id, filename=audio_path, v=v)


def row_audio_url(episode_id: str, row: dict) -> str | None:
    return audio_url(episode_id, row.get("audio_path"),
                     row.get("audio_version") or row.get("audio_generated_from_text"))


def title_audio_url(episode_id: str, obj: dict) -> str | None:
    return audio_url(episode_id, obj.get("title_audio_path"),
                     obj.get("title_audio_version") or obj.get("title_audio_generated_from_text"))


app.jinja_env.globals.update(static_url=static_url, audio_url=audio_url,
                             row_audio_url=row_audio_url, title_audio_url=title_audio_url)


_TITLE_COMMENT_ENDPOINTS = {
    "add_episode_title_comment", "reply_episode_title_comment", "resolve_episode_title_comment",
    "delete_episode_title_comment", "delete_episode_title_comment_reply",
    "add_chapter_title_comment", "reply_chapter_title_comment", "resolve_chapter_title_comment",
    "delete_chapter_title_comment", "delete_chapter_title_comment_reply",
}


@app.url_value_preprocessor
def _validate_comment_target(endpoint, values):
    if values and "target" in values:
        allowed = db.TITLE_COMMENT_TARGETS if endpoint in _TITLE_COMMENT_ENDPOINTS else db.COMMENT_TARGETS
        if values["target"] not in allowed:
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
    episodes = db.list_episode_summaries(lang_code)
    for ep in episodes:
        verified, total = db.count_verified(ep)
        ep["row_count"] = total
        ep["chapter_count"] = len(ep["chapters"])
        ep["review_percent"] = round(verified / total * 100) if total else 0
    shared = native_review.shared_tracks(ep.get("english_key") for ep in episodes)
    for ep in episodes:
        ep["final_audio_status"] = native_review.final_audio_status(ep, shared.get(ep.get("english_key")))
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

    if not english_file or not english_file.filename:
        return render_template("upload.html", lang=lang, error="The English master script is required.",
                                tts_available=has_tts_backend(lang_code))

    episode_id = db.create_episode(title=title, target_lang=lang_code, target_lang_name=lang["name"])
    storage.save_bytes(episode_id, "uploads/english.docx", english_file.read())
    if translated_file and translated_file.filename:
        storage.save_bytes(episode_id, "uploads/translated.docx", translated_file.read())

    start_pipeline(episode_id)

    return redirect(url_for("episode_view", episode_id=episode_id))


RESULTS_VIEWABLE_STATUSES = {"tts", db.STATUS_TTS_DONE, db.STATUS_REVIEWED}


@app.route("/episode/<episode_id>")
def episode_view(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    if episode["status"] not in RESULTS_VIEWABLE_STATUSES:
        return render_template("progress.html", episode=episode)
    # Self-heal: a finished episode with missing audio (killed worker, failed row) is repaired
    # in the background on open; the page then shows the usual "audio generating" polling UI.
    repairing = (episode["status"] in (db.STATUS_TTS_DONE, db.STATUS_REVIEWED)
                 and episode_missing_audio(episode) and start_audio_repair(episode_id))
    verified, total = db.count_verified(episode)
    return render_template("episode.html", episode=episode, standalone=False,
                            verified_rows=verified, total_rows=total,
                            audio_in_progress=repairing or episode["status"] == "tts")


ACTIVE_STATUSES = {"parsing", "translating_titles", "reviewing", "finding_difficult_words", "tts"}


def _resume_if_stalled(episode_id: str, episode: dict) -> bool:
    """An episode mid-pipeline with no live lease lost its worker (OOM kill, instance restart).
    Resume it -- the pipeline skips finished stages/rows -- instead of leaving it stuck forever.
    Returns True if it is (now) running."""
    if episode["status"] not in ACTIVE_STATUSES:
        return False
    if is_running(episode_id):
        return True
    return start_pipeline(episode_id)


def _stalled_sweeper(interval_seconds: int = 60) -> None:
    """Background safety net: every minute, resume any episode left mid-pipeline with no live
    lease (OOM kill, redeploy, instance restart), whether or not anyone has its page open."""
    while True:
        try:
            for episode_id in db.list_stalled_episode_ids(ACTIVE_STATUSES):
                if start_pipeline(episode_id):
                    app.logger.warning("sweeper: resumed stalled episode %s", episode_id)
        except Exception:
            app.logger.exception("sweeper: failed")
        time.sleep(interval_seconds)


def _upload_cleaner(interval_seconds: int = 3600) -> None:
    while True:
        try:
            if removed := storage.delete_stale("uploads/final/**", STALE_UPLOAD_SECONDS):
                app.logger.info("upload cleaner: removed %d abandoned upload parts", removed)
        except Exception:
            app.logger.exception("upload cleaner: failed")
        time.sleep(interval_seconds)


def start_stalled_sweeper() -> None:
    threading.Thread(target=_stalled_sweeper, daemon=True, name="stalled-sweeper").start()
    threading.Thread(target=_upload_cleaner, daemon=True, name="upload-cleaner").start()


@app.route("/episode/<episode_id>/status")
def episode_status(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    progress = db.progress_counts(episode_id, episode)
    verified = progress.pop("verified_rows")
    total = progress.get("total_rows", 0)
    stalled = episode["status"] in ACTIVE_STATUSES and not _resume_if_stalled(episode_id, episode)
    return jsonify({
        "status": episode["status"],
        "error_message": episode.get("error_message"),
        "verified_rows": verified,
        "review_percent": round((verified / total) * 100) if total else 0,
        "stalled": stalled,
        **progress,
    })


@app.route("/episode/<episode_id>/audio-status")
def episode_audio_status(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    stalled = episode["status"] in ACTIVE_STATUSES and not _resume_if_stalled(episode_id, episode)
    return jsonify({
        "status": episode["status"],
        "stalled": stalled,
        "rows": [
            {**r,
             "audio_url": row_audio_url(episode_id, r),
             "reviewer_audio_url": audio_url(episode_id, r.get("reviewer_audio_path"), r.get("reviewer_audio_version"))}
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


_audio_sizes: dict[tuple, int] = {}


def _audio_size(episode_id: str, path: str, version: str | None) -> int:
    key = (episode_id, path, version)
    if version and key in _audio_sizes:
        return _audio_sizes[key]
    total = storage.size(episode_id, path)
    if version:
        if len(_audio_sizes) > 5000:
            _audio_sizes.clear()
        _audio_sizes[key] = total
    return total


@app.route("/episode/<episode_id>/audio/<path:filename>")
def episode_audio(episode_id, filename):
    return _serve_audio(episode_id, filename)


@app.route("/english/<key>/audio/<path:filename>")
def shared_english_audio(key, filename):
    return _serve_audio(native_review.shared_namespace(secure_filename(key)), filename)


def _serve_audio(namespace, filename):
    path = f"audio/{filename}"
    try:
        total = _audio_size(namespace, path, request.args.get("v"))
    except FileNotFoundError:
        abort(404)
    byte_range = request.range.range_for_length(total) if request.range else None
    start, stop = byte_range or (0, total)
    if byte_range:
        stop = min(stop, start + AUDIO_CHUNK_BYTES)
    resp = Response(storage.read_range(namespace, path, start, stop - 1),
                    206 if byte_range else 200, mimetype="audio/mpeg")
    resp.accept_ranges = "bytes"
    if byte_range:
        resp.content_range = ContentRange("bytes", start, stop, total)
    if "v" in request.args:
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return resp


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
    verifying = "human_verified" in request.form
    if verifying:
        fields["human_verified"] = request.form["human_verified"] == "true"
    if fields:
        db.update_row(episode_id, sr_no, **fields)
    new_status = db.sync_review_status(episode_id) if verifying else None
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        verified, total = db.verification_counts(episode_id)
        return jsonify({"ok": True, "verified_rows": verified, "total_rows": total,
                         "episode_status": new_status})
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


@app.route("/episode/<episode_id>/row/<int:sr_no>/regenerate-audio", methods=["POST"])
@_row_not_found_as_404
def regenerate_row_audio(episode_id, sr_no):
    row = db.get_row(episode_id, sr_no)
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    if not has_tts_backend(episode["target_lang"]):
        abort(400, "No TTS voice pack for this language")
    speakers = list(dict.fromkeys(
        r["speaker"] for c in episode["chapters"] for r in c["rows"]
    ))
    try:
        audio_path, audio_status = synthesize_row_audio(episode_id, episode["target_lang"], row, speakers)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "audio_path": audio_path, "audio_status": audio_status,
                     "audio_url": row_audio_url(episode_id, db.get_row(episode_id, sr_no)), "audio_stale": False})


@app.route("/episode/<episode_id>/row/<int:sr_no>/ai-fill", methods=["POST"])
@_row_not_found_as_404
def ai_fill_row(episode_id, sr_no):
    """Translate one row's English text via AI and fill it into `translated`/`reviewer_text`.
    For on-demand use when both docs were uploaded but this row's line failed to align
    (blank translated text) -- the automatic per-episode AI translation stage only runs
    when no translated doc was uploaded at all, so this route covers the other case."""
    row = db.get_row(episode_id, sr_no)
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    client = get_llm_client()
    translated = translate_rows(client, [row["english"]], episode["target_lang_name"])[0]
    db.update_row(episode_id, sr_no, translated=translated, reviewer_text=translated)
    return jsonify({"ok": True, "translated": translated, "reviewer_text": translated})


@app.route("/episode/<episode_id>/row/<int:sr_no>/reviewer-audio", methods=["POST"])
def upload_reviewer_audio(episode_id, sr_no):
    file = request.files.get("audio")
    if not file:
        abort(400, "Audio file is required.")
    raw_bytes = file.read()
    if not raw_bytes:
        abort(400, "Audio file is empty.")
    if len(raw_bytes) > MAX_REVIEWER_AUDIO_BYTES:
        abort(413, "Audio file is too large.")
    try:
        mp3_bytes = transcode_to_mp3(raw_bytes, file.filename or "upload")
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not process audio: {exc}"}), 422
    filename = f"row_{sr_no}_reviewer.mp3"
    storage.save_bytes(episode_id, f"audio/{filename}", mp3_bytes)
    db.set_reviewer_audio(episode_id, sr_no, audio_path=filename, audio_status="done")
    return jsonify({"ok": True, "audio_url": audio_url(episode_id, filename, db.get_row(episode_id, sr_no).get("reviewer_audio_version")),
                     "audio_source": "reviewer"})


@app.route("/episode/<episode_id>/row/<int:sr_no>/reviewer-audio/delete", methods=["POST"])
def delete_reviewer_audio(episode_id, sr_no):
    row = db.get_row(episode_id, sr_no)
    if row.get("reviewer_audio_path"):
        storage.delete_file(episode_id, f"audio/{row['reviewer_audio_path']}")
    db.clear_reviewer_audio(episode_id, sr_no)
    return jsonify({"ok": True})


@app.route("/episode/<episode_id>/row/<int:sr_no>/audio-source", methods=["POST"])
def set_row_audio_source(episode_id, sr_no):
    source = request.form.get("source")
    if source not in ("tts", "reviewer"):
        abort(400, "source must be 'tts' or 'reviewer'")
    db.set_audio_source(episode_id, sr_no, source)
    return jsonify({"ok": True, "audio_source": source})


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


# ---- Title reviewer-text / undo-redo / regenerate-audio / comments ----
# Two parallel route families (episode title, chapter title) sharing implementation
# helpers, mirroring the row routes above but addressed by a locator instead of sr_no.

def _title_reviewer_text_response(episode_id, locator):
    if locator == "episode":
        episode = db.get_episode(episode_id)
        text, history, index = episode["title_reviewer_text"], episode["title_reviewer_history"], episode["title_reviewer_history_index"]
    else:
        chapter_number = int(locator.split(":", 1)[1])
        chapter = db.get_chapter_title(episode_id, chapter_number)
        text, history, index = chapter["title_reviewer_text"], chapter["title_reviewer_history"], chapter["title_reviewer_history_index"]
    return jsonify({"ok": True, "text": text, "can_undo": index > 0, "can_redo": index < len(history) - 1})


@app.route("/episode/<episode_id>/title/reviewer-text", methods=["POST"])
def update_episode_title_reviewer_text(episode_id):
    db.set_episode_reviewer_text(episode_id, request.form.get("text", ""))
    return _title_reviewer_text_response(episode_id, "episode")


@app.route("/episode/<episode_id>/title/reviewer-text/undo", methods=["POST"])
def undo_episode_title_reviewer_text(episode_id):
    db.move_episode_reviewer_history(episode_id, "undo")
    return _title_reviewer_text_response(episode_id, "episode")


@app.route("/episode/<episode_id>/title/reviewer-text/redo", methods=["POST"])
def redo_episode_title_reviewer_text(episode_id):
    db.move_episode_reviewer_history(episode_id, "redo")
    return _title_reviewer_text_response(episode_id, "episode")


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/reviewer-text", methods=["POST"])
@_row_not_found_as_404
def update_chapter_title_reviewer_text(episode_id, chapter_number):
    db.set_chapter_reviewer_text(episode_id, chapter_number, request.form.get("text", ""))
    return _title_reviewer_text_response(episode_id, f"chapter:{chapter_number}")


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/reviewer-text/undo", methods=["POST"])
@_row_not_found_as_404
def undo_chapter_title_reviewer_text(episode_id, chapter_number):
    db.move_chapter_reviewer_history(episode_id, chapter_number, "undo")
    return _title_reviewer_text_response(episode_id, f"chapter:{chapter_number}")


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/reviewer-text/redo", methods=["POST"])
@_row_not_found_as_404
def redo_chapter_title_reviewer_text(episode_id, chapter_number):
    db.move_chapter_reviewer_history(episode_id, chapter_number, "redo")
    return _title_reviewer_text_response(episode_id, f"chapter:{chapter_number}")


def _regenerate_title_audio(episode_id, episode, text, save_audio):
    """(Re)synthesize one title's audio. Returns (json_dict, status_code). On success,
    json_dict["audio_path"] is set (or None for blank text) -- callers persist it themselves,
    since episode-title and chapter-title use different db.set_*_title_audio calls."""
    if not has_tts_backend(episode["target_lang"]):
        abort(400, "No TTS voice pack for this language")
    try:
        audio_bytes = synthesize_title_audio(episode["target_lang"], text)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500
    audio_path = save_audio(audio_bytes) if audio_bytes is not None else None
    return {"ok": True, "audio_path": audio_path, "audio_status": "done",
            "audio_url": audio_url(episode_id, audio_path, time.time_ns()), "audio_stale": False}, 200


@app.route("/episode/<episode_id>/title/regenerate-audio", methods=["POST"])
def regenerate_episode_title_audio(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    text = episode["title_reviewer_text"]

    def save_audio(audio_bytes):
        storage.save_bytes(episode_id, "audio/title.mp3", audio_bytes)
        return "title.mp3"

    data, status = _regenerate_title_audio(episode_id, episode, text, save_audio)
    if data["ok"]:
        db.set_episode_title_audio(episode_id, audio_path=data["audio_path"], audio_status="done",
                                    audio_generated_from_text=text)
    return jsonify(data), status


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/regenerate-audio", methods=["POST"])
@_row_not_found_as_404
def regenerate_chapter_title_audio(episode_id, chapter_number):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    chapter = db.get_chapter_title(episode_id, chapter_number)
    text = chapter["title_reviewer_text"]

    def save_audio(audio_bytes):
        filename = f"chapter_{chapter_number}_title.mp3"
        storage.save_bytes(episode_id, f"audio/{filename}", audio_bytes)
        return filename

    data, status = _regenerate_title_audio(episode_id, episode, text, save_audio)
    if data["ok"]:
        db.set_chapter_title_audio(episode_id, chapter_number, audio_path=data["audio_path"],
                                    audio_status="done", audio_generated_from_text=text)
    return jsonify(data), status


@app.route("/episode/<episode_id>/title/comments/<target>", methods=["POST"])
def add_episode_title_comment(episode_id, target):
    text = request.form.get("text", "").strip()
    if not text:
        abort(400, "Comment text is required.")
    author = request.form.get("author", "Reviewer").strip() or "Reviewer"
    comment = db.add_title_comment(episode_id, "episode", target, text, author, anchor=_parse_anchor(request.form))
    return jsonify({"ok": True, "comment": _serialize_comment(comment)})


@app.route("/episode/<episode_id>/title/comments/<target>/<comment_id>/reply", methods=["POST"])
def reply_episode_title_comment(episode_id, target, comment_id):
    text = request.form.get("text", "").strip()
    if not text:
        abort(400, "Reply text is required.")
    author = request.form.get("author", "Reviewer").strip() or "Reviewer"
    reply = db.add_title_comment_reply(episode_id, "episode", target, comment_id, text, author)
    return jsonify({"ok": True, "reply": _serialize_comment(reply)})


@app.route("/episode/<episode_id>/title/comments/<target>/<comment_id>/resolve", methods=["POST"])
def resolve_episode_title_comment(episode_id, target, comment_id):
    resolved = request.form.get("resolved") == "true"
    db.set_title_comment_resolved(episode_id, "episode", target, comment_id, resolved)
    return jsonify({"ok": True, "resolved": resolved})


@app.route("/episode/<episode_id>/title/comments/<target>/<comment_id>/delete", methods=["POST"])
def delete_episode_title_comment(episode_id, target, comment_id):
    db.delete_title_comment(episode_id, "episode", target, comment_id)
    return jsonify({"ok": True})


@app.route("/episode/<episode_id>/title/comments/<target>/<comment_id>/reply/<reply_id>/delete", methods=["POST"])
def delete_episode_title_comment_reply(episode_id, target, comment_id, reply_id):
    db.delete_title_comment_reply(episode_id, "episode", target, comment_id, reply_id)
    return jsonify({"ok": True})


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/comments/<target>", methods=["POST"])
def add_chapter_title_comment(episode_id, chapter_number, target):
    text = request.form.get("text", "").strip()
    if not text:
        abort(400, "Comment text is required.")
    author = request.form.get("author", "Reviewer").strip() or "Reviewer"
    locator = f"chapter:{chapter_number}"
    comment = db.add_title_comment(episode_id, locator, target, text, author, anchor=_parse_anchor(request.form))
    return jsonify({"ok": True, "comment": _serialize_comment(comment)})


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/comments/<target>/<comment_id>/reply", methods=["POST"])
def reply_chapter_title_comment(episode_id, chapter_number, target, comment_id):
    text = request.form.get("text", "").strip()
    if not text:
        abort(400, "Reply text is required.")
    author = request.form.get("author", "Reviewer").strip() or "Reviewer"
    reply = db.add_title_comment_reply(episode_id, f"chapter:{chapter_number}", target, comment_id, text, author)
    return jsonify({"ok": True, "reply": _serialize_comment(reply)})


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/comments/<target>/<comment_id>/resolve", methods=["POST"])
def resolve_chapter_title_comment(episode_id, chapter_number, target, comment_id):
    resolved = request.form.get("resolved") == "true"
    db.set_title_comment_resolved(episode_id, f"chapter:{chapter_number}", target, comment_id, resolved)
    return jsonify({"ok": True, "resolved": resolved})


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/comments/<target>/<comment_id>/delete", methods=["POST"])
def delete_chapter_title_comment(episode_id, chapter_number, target, comment_id):
    db.delete_title_comment(episode_id, f"chapter:{chapter_number}", target, comment_id)
    return jsonify({"ok": True})


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/title/comments/<target>/<comment_id>/reply/<reply_id>/delete", methods=["POST"])
def delete_chapter_title_comment_reply(episode_id, chapter_number, target, comment_id, reply_id):
    db.delete_title_comment_reply(episode_id, f"chapter:{chapter_number}", target, comment_id, reply_id)
    return jsonify({"ok": True})


def _serialize_comment(comment: dict) -> dict:
    return {**comment, "created_at": comment["created_at"].isoformat()}


@app.route("/episode/<episode_id>/export/docx")
def export_docx(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    if episode["status"] not in (db.STATUS_TTS_DONE, db.STATUS_REVIEWED):
        abort(400, "Episode is not finished processing yet.")
    buffer = build_docx_export(episode)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=f"{episode['title']}_{episode['target_lang']}.docx",
    )


@app.route("/episode/<episode_id>/export/xlsx")
def export_xlsx(episode_id):
    episode = db.get_episode(episode_id)
    if episode is None:
        abort(404)
    if episode["status"] not in (db.STATUS_TTS_DONE, db.STATUS_REVIEWED):
        abort(400, "Episode is not finished processing yet.")
    buffer = build_xlsx_export(episode)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{episode['title']}_{episode['target_lang']}.xlsx",
    )


def _episode_chapter(episode_id: str, chapter_number: int) -> tuple[dict, dict]:
    episode = db.get_episode(episode_id) or abort(404)
    chapter = next((c for c in episode["chapters"] if c["chapter_number"] == chapter_number), None) or abort(404)
    return episode, chapter


def _track_url(episode_id, english_key, chapter, key, track):
    v = int(track["uploaded_at"].timestamp())
    if native_review.is_shared(chapter, key):
        return url_for("shared_english_audio", key=english_key, filename=track["audio_path"], v=v)
    return url_for("episode_audio", episode_id=episode_id, filename=track["audio_path"], v=v)


@app.route("/episode/<episode_id>/native-review")
def native_review_page(episode_id):
    episode = db.get_episode(episode_id) or abort(404)
    english_key = native_review.english_key(episode)
    shared = native_review.shared_tracks([english_key]).get(english_key)
    chapters = [{
        "number": c["chapter_number"],
        "title": c["title"],
        "verified": bool(c.get("final_audio_verified")),
        "uploaded": native_review.chapter_uploaded(c, shared),
        "shared_en": native_review.is_shared(c, "en"),
        "lines": {r["sr_no"]: {"speaker": r["speaker"], "en": r["english"], "native": r["reviewer_text"]} for r in c["rows"]},
        "tracks": {key: {"cues": t["cues"], "comments": [_serialize_comment(cm) for cm in t["comments"]],
                         "url": _track_url(episode_id, english_key, c, key, t)}
                   for key, t in native_review.chapter_tracks(c, shared).items()},
    } for c in episode["chapters"]]
    linked = native_review.linked_episodes(english_key, episode_id) if english_key else []
    others = [e for e in db.list_link_candidates(episode["target_lang"]) if e.get("english_key") != english_key]
    return render_template("native_review.html", episode=episode, chapters=chapters, linked=linked, others=others,
                           final_status=native_review.final_audio_status(episode, shared))


@app.route("/episode/<episode_id>/english-link", methods=["POST"])
def link_english_audio(episode_id):
    episode = db.get_episode(episode_id) or abort(404)
    other_id = request.form.get("episode", "")
    other = db.get_episode(other_id) if ObjectId.is_valid(other_id) else None
    if other is None:
        abort(400)
    native_review.set_english_key(episode_id, native_review.english_key(other))
    return redirect(url_for("native_review_page", episode_id=episode["_id"]))


NATIVE_REVIEW_TRACK = "/episode/<episode_id>/chapter/<int:chapter_number>/native-review/<any(en, native):track>"


def _take_upload_parts(episode_id: str, upload_id, read: bool = True) -> bytes:
    folder = f"uploads/final/{upload_id}"
    parts = sorted(storage.list_files(episode_id, folder))
    raw = b"".join(storage.read_bytes(episode_id, f"{folder}/{p}") for p in parts) if read else b""
    for p in parts:
        storage.delete_file(episode_id, f"{folder}/{p}")
    return raw


@app.route(f"{NATIVE_REVIEW_TRACK}/parts/<uuid:upload_id>/abort", methods=["POST"])
def abort_native_review_upload(episode_id, chapter_number, track, upload_id):
    _take_upload_parts(episode_id, upload_id, read=False)
    return jsonify({"ok": True})


@app.route(f"{NATIVE_REVIEW_TRACK}/parts/<uuid:upload_id>/<int:index>", methods=["POST"])
def upload_native_review_part(episode_id, chapter_number, track, upload_id, index):
    data = request.get_data()
    if not data or len(data) > UPLOAD_PART_BYTES:
        abort(400)
    storage.save_bytes(episode_id, f"uploads/final/{upload_id}/{index:05d}", data)
    return jsonify({"ok": True})


@app.route(f"{NATIVE_REVIEW_TRACK}/parts/<uuid:upload_id>/finish", methods=["POST"])
def finish_native_review_upload(episode_id, chapter_number, track, upload_id):
    episode, chapter = _episode_chapter(episode_id, chapter_number)
    raw = _take_upload_parts(episode_id, upload_id)
    if not raw:
        abort(400)
    try:
        mp3 = transcode_to_mp3(raw, secure_filename(request.form.get("filename", "")) or "upload", bitrate="192k")
    except subprocess.CalledProcessError:
        return jsonify({"error": "Could not read this audio file."}), 422
    try:
        cues = align_lines(mp3, [(r["sr_no"], r[native_review.TEXT_FIELDS[track]]) for r in chapter["rows"]])
    except Exception as exc:
        app.logger.exception("caption sync failed")
        return jsonify({"error": f"Caption sync failed: {exc}"}), 502
    filename = f"final_ch{chapter_number}_{track}.mp3"
    namespace = (native_review.shared_namespace(native_review.english_key(episode))
                 if native_review.is_shared(chapter, track) else episode_id)
    storage.save_bytes(namespace, f"audio/{filename}", mp3)
    native_review.set_track(episode, chapter_number, track, filename, cues)
    return jsonify({"ok": True})


@app.route(f"{NATIVE_REVIEW_TRACK}/comments", methods=["POST"])
def add_native_review_comment(episode_id, chapter_number, track):
    text = request.form.get("text", "").strip()
    time_ = request.form.get("time", type=float)
    if not text or time_ is None:
        abort(400)
    episode, _ = _episode_chapter(episode_id, chapter_number)
    comment = native_review.add_comment(episode, chapter_number, track, time_,
                                        request.form.get("sr_no", type=int), text)
    return jsonify(_serialize_comment(comment))


@app.route(f"{NATIVE_REVIEW_TRACK}/comments/<comment_id>/delete", methods=["POST"])
def delete_native_review_comment(episode_id, chapter_number, track, comment_id):
    episode, _ = _episode_chapter(episode_id, chapter_number)
    native_review.delete_comment(episode, chapter_number, track, comment_id)
    return jsonify({"ok": True})


@app.route("/episode/<episode_id>/chapter/<int:chapter_number>/native-review/verify", methods=["POST"])
def verify_native_review_chapter(episode_id, chapter_number):
    episode, chapter = _episode_chapter(episode_id, chapter_number)
    key = native_review.english_key(episode)
    shared = native_review.shared_tracks([key]).get(key)
    verified = request.form.get("verified") == "true"
    if verified and not native_review.chapter_uploaded(chapter, shared):
        abort(400)
    native_review.set_verified(episode_id, chapter_number, verified)
    return jsonify({"verified": verified,
                    "status": native_review.final_audio_status(db.get_episode(episode_id), shared)})


# Started at import so it also runs under gunicorn (single worker, see Dockerfile). Under the
# Flask dev reloader only the serving child process (WERKZEUG_RUN_MAIN) starts it; the DB lease
# makes an accidental second sweeper harmless anyway.
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    start_stalled_sweeper()


if __name__ == "__main__":
    app.run(debug=True)
