"""MongoDB connection and episode-level CRUD. Single `episodes` collection,
chapters/rows embedded. Row/comment CRUD lives in core.db_rows, re-exported
below so callers keep using db.get_row(), db.add_comment(), etc. unchanged.
"""
import os
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import MongoClient

_client: MongoClient | None = None

# Episode lifecycle statuses. "tts_done" means translation + TTS finished and the
# episode is ready for human review; "reviewed" means every row has been marked
# human_verified (100% review complete). Kept distinct so the dashboard can tell
# "ready to review" apart from "actually signed off" instead of collapsing both
# into a single "done".
STATUS_TTS_DONE = "tts_done"
STATUS_REVIEWED = "reviewed"
# Old episodes/tests may still carry the pre-rename "done" status; treat it as
# equivalent to STATUS_TTS_DONE wherever a status is read back out.
LEGACY_DONE_STATUSES = {"done"}


def get_db():
    global _client
    if _client is None:
        uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        _client = MongoClient(uri)
    return _client[os.environ.get("MONGO_DB_NAME", "audiobook_review")]


def episodes_collection():
    return get_db().episodes


def ensure_indexes() -> None:
    coll = episodes_collection()
    coll.create_index("target_lang")
    coll.create_index("status")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_episode(title: str, target_lang: str, target_lang_name: str) -> str:
    doc = {
        "title": title,
        "source_lang": "en",
        "target_lang": target_lang,
        "target_lang_name": target_lang_name,
        "status": "uploaded",
        "error_message": None,
        "created_at": _now(),
        "updated_at": _now(),
        "chapters": [],
    }
    result = episodes_collection().insert_one(doc)
    return str(result.inserted_id)


def _normalize_status(doc: dict) -> dict:
    """Map the legacy "done" status (pre-dating the tts_done/reviewed split) to its
    modern equivalent, so old episodes read back with an accurate status without
    needing a separate migration to run first. Expects doc["_id"] already stringified."""
    if doc.get("status") in LEGACY_DONE_STATUSES:
        counts = _row_counts(doc["_id"])
        all_verified = counts["total_rows"] > 0 and counts["verified_rows"] == counts["total_rows"]
        doc["status"] = STATUS_REVIEWED if all_verified else STATUS_TTS_DONE
    return doc


def get_episode(episode_id: str) -> dict | None:
    doc = episodes_collection().find_one({"_id": ObjectId(episode_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
        doc = _normalize_status(doc)
    return doc


def list_episodes_for_language(target_lang: str) -> list[dict]:
    docs = list(episodes_collection().find({"target_lang": target_lang}).sort("created_at", -1))
    for doc in docs:
        doc["_id"] = str(doc["_id"])
        _normalize_status(doc)
    return docs


def list_target_languages() -> list[str]:
    return episodes_collection().distinct("target_lang")


def update_episode(episode_id: str, **fields) -> None:
    fields["updated_at"] = _now()
    episodes_collection().update_one({"_id": ObjectId(episode_id)}, {"$set": fields})


def set_episode_chapters(episode_id: str, chapters: list[dict]) -> None:
    update_episode(episode_id, chapters=chapters)


def set_episode_status(episode_id: str, status: str, error_message: str | None = None) -> None:
    update_episode(episode_id, status=status, error_message=error_message)


def delete_episode(episode_id: str) -> None:
    episodes_collection().delete_one({"_id": ObjectId(episode_id)})


def set_episode_title_translation(episode_id: str, translated_title: str, audio_path: str | None) -> None:
    update_episode(
        episode_id,
        translated_title=translated_title,
        title_audio_path=audio_path,
        title_reviewer_text=translated_title,
        title_reviewer_history=[],
        title_reviewer_history_index=-1,
        title_comments={t: [] for t in TITLE_COMMENT_TARGETS},
        title_audio_status="done" if audio_path else "pending",
        title_audio_generated_from_text=translated_title if audio_path else None,
    )


def chapter_rows_reviewed(chapter: dict) -> bool:
    return all(row.get("review_comment") is not None for row in chapter["rows"])


def chapter_words_found(chapter: dict) -> bool:
    return all(row.get("difficult_words") is not None for row in chapter["rows"])


def titles_translated(episode: dict) -> bool:
    if episode.get("translated_title") is None:
        return False
    return all(chapter.get("translated_title") is not None for chapter in episode["chapters"])


def row_audio_done(row: dict) -> bool:
    return row.get("audio_status") == "done"


def _row_counts(episode_id: str) -> dict:
    """Single aggregation pass over all rows, counting every stage at once
    instead of fetching the full episode document into Python."""
    pipeline = [
        {"$match": {"_id": ObjectId(episode_id)}},
        {"$project": {"rows": {"$reduce": {
            "input": "$chapters", "initialValue": [],
            "in": {"$concatArrays": ["$$value", "$$this.rows"]},
        }}}},
        {"$project": {
            "total_rows": {"$size": "$rows"},
            "reviewed_rows": {"$size": {"$filter": {"input": "$rows", "cond": {"$ne": ["$$this.review_comment", None]}}}},
            "words_found_rows": {"$size": {"$filter": {"input": "$rows", "cond": {"$ne": ["$$this.difficult_words", None]}}}},
            "audio_done_rows": {"$size": {"$filter": {"input": "$rows", "cond": {"$eq": ["$$this.audio_status", "done"]}}}},
            "verified_rows": {"$size": {"$filter": {"input": "$rows", "cond": {"$eq": ["$$this.human_verified", True]}}}},
        }},
    ]
    result = list(episodes_collection().aggregate(pipeline))
    if not result:
        return {"total_rows": 0, "reviewed_rows": 0, "words_found_rows": 0, "audio_done_rows": 0, "verified_rows": 0}
    result[0].pop("_id", None)
    return result[0]


def progress_counts(episode_id: str, episode: dict | None = None) -> dict:
    """Per-stage completion counts, used to render a live progress breakdown."""
    counts = _row_counts(episode_id)
    counts["titles_translated"] = titles_translated(episode) if episode is not None else None
    return counts


def verification_counts(episode_id: str) -> tuple[int, int]:
    """Return (verified_rows, total_rows) across all chapters."""
    counts = _row_counts(episode_id)
    return counts["verified_rows"], counts["total_rows"]


def review_percent(episode_id: str) -> int:
    """Percentage (0-100) of rows marked human_verified, for progress displays."""
    verified, total = verification_counts(episode_id)
    return round((verified / total) * 100) if total else 0


def sync_review_status(episode_id: str) -> str | None:
    """Promote an episode to STATUS_REVIEWED once every row is human_verified, or
    demote it back to STATUS_TTS_DONE if a row is un-verified afterwards. No-ops for
    episodes still mid-pipeline (parsing/tts/etc) or failed -- only the two
    post-pipeline statuses are affected. Returns the new status, or None if the
    episode's status wasn't touched (e.g. mid-pipeline or not found)."""
    episode = get_episode(episode_id)
    if episode is None or episode["status"] not in (STATUS_TTS_DONE, STATUS_REVIEWED):
        return None
    verified, total = verification_counts(episode_id)
    target_status = STATUS_REVIEWED if total > 0 and verified == total else STATUS_TTS_DONE
    if episode["status"] != target_status:
        set_episode_status(episode_id, target_status)
    return target_status


def audio_statuses(episode_id: str) -> list[dict]:
    """Lightweight per-row audio state for polling while TTS still runs in the
    background, without fetching full rows. Includes the reviewer-audio lane
    fields so pollers can avoid clobbering a reviewer's uploaded take."""
    pipeline = [
        {"$match": {"_id": ObjectId(episode_id)}},
        {"$project": {"rows": {"$reduce": {
            "input": "$chapters", "initialValue": [],
            "in": {"$concatArrays": ["$$value", "$$this.rows"]},
        }}}},
        {"$unwind": "$rows"},
        {"$replaceRoot": {"newRoot": "$rows"}},
        {"$project": {"_id": 0, "sr_no": 1, "audio_status": 1, "audio_path": 1,
                       "reviewer_audio_path": 1, "reviewer_audio_status": 1,
                       "audio_source": 1, "audio_generated_from_text": 1, "reviewer_text": 1}},
    ]
    return list(episodes_collection().aggregate(pipeline))


# Row/comment CRUD lives in core.db_rows; re-exported here so `db.get_row(...)`,
# `db.add_comment(...)`, etc. keep working for existing callers unchanged.
from core.db_rows import (  # noqa: E402
    COMMENT_TARGETS,
    ANCHORABLE_COMMENT_TARGETS,
    TITLE_COMMENT_TARGETS,
    update_row,
    get_row,
    set_reviewer_text,
    move_reviewer_history,
    set_reviewer_complete,
    set_row_audio,
    set_reviewer_audio,
    clear_reviewer_audio,
    set_audio_source,
    add_comment,
    add_comment_reply,
    set_comment_resolved,
    delete_comment,
    delete_comment_reply,
    set_chapter_title_translation,
    get_chapter_title,
    set_chapter_reviewer_text,
    move_chapter_reviewer_history,
    set_chapter_title_audio,
    set_episode_reviewer_text,
    move_episode_reviewer_history,
    set_episode_title_audio,
    add_title_comment,
    add_title_comment_reply,
    set_title_comment_resolved,
    delete_title_comment,
    delete_title_comment_reply,
)
