"""MongoDB connection and episode CRUD. Single `episodes` collection, chapters/rows embedded."""
import os
import uuid
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import MongoClient

COMMENT_TARGETS = ("english", "ai_translation", "reviewer_edit", "audio")

_client: MongoClient | None = None


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


def get_episode(episode_id: str) -> dict | None:
    doc = episodes_collection().find_one({"_id": ObjectId(episode_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def list_episodes_for_language(target_lang: str) -> list[dict]:
    docs = list(episodes_collection().find({"target_lang": target_lang}).sort("created_at", -1))
    for doc in docs:
        doc["_id"] = str(doc["_id"])
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


def update_row(episode_id: str, sr_no: int, **fields) -> None:
    """Update a single row (by sr_no) inside whichever chapter contains it, via arrayFilters."""
    set_fields = {f"chapters.$[c].rows.$[r].{k}": v for k, v in fields.items()}
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {**set_fields, "updated_at": _now()}},
        array_filters=[{"r.sr_no": sr_no}, {"c.rows.sr_no": sr_no}],
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


def _row_filters(sr_no: int) -> list[dict]:
    return [{"r.sr_no": sr_no}, {"c.rows.sr_no": sr_no}]


def get_row(episode_id: str, sr_no: int) -> dict:
    """Fetch only the one row's fields (not the whole episode) via an aggregation projection."""
    pipeline = [
        {"$match": {"_id": ObjectId(episode_id)}},
        {"$project": {"chapters": 1}},
        {"$unwind": "$chapters"},
        {"$unwind": "$chapters.rows"},
        {"$match": {"chapters.rows.sr_no": sr_no}},
        {"$replaceRoot": {"newRoot": "$chapters.rows"}},
        {"$limit": 1},
    ]
    result = list(episodes_collection().aggregate(pipeline))
    if not result:
        raise ValueError(f"Row {sr_no} not found in episode {episode_id}")
    return result[0]


def set_reviewer_text(episode_id: str, sr_no: int, text: str) -> None:
    """Save a new reviewer edit, appending to history and truncating any redo tail."""
    row = get_row(episode_id, sr_no)
    history = row.get("reviewer_history", [])
    index = row.get("reviewer_history_index", -1)
    history = history[:index + 1]
    history.append({"text": text, "edited_at": _now()})
    update_row(episode_id, sr_no, reviewer_text=text,
               reviewer_history=history, reviewer_history_index=len(history) - 1)


def move_reviewer_history(episode_id: str, sr_no: int, direction: str) -> str:
    """Move the undo/redo pointer one step; direction is 'undo' or 'redo'. Returns the
    resulting reviewer_text."""
    row = get_row(episode_id, sr_no)
    history = row.get("reviewer_history", [])
    index = row.get("reviewer_history_index", -1)
    if direction == "undo" and index > 0:
        index -= 1
    elif direction == "redo" and index < len(history) - 1:
        index += 1
    text = history[index]["text"] if history else row.get("translated", "")
    update_row(episode_id, sr_no, reviewer_text=text, reviewer_history_index=index)
    return text


def add_comment(episode_id: str, sr_no: int, target: str, text: str, author: str) -> dict:
    comment = {
        "id": uuid.uuid4().hex, "text": text, "author": author,
        "created_at": _now(), "resolved": False, "replies": [],
    }
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$push": {f"chapters.$[c].rows.$[r].comments.{target}": comment},
         "$set": {"updated_at": _now()}},
        array_filters=_row_filters(sr_no),
    )
    return comment


def add_comment_reply(episode_id: str, sr_no: int, target: str, comment_id: str, text: str, author: str) -> dict:
    reply = {"id": uuid.uuid4().hex, "text": text, "author": author, "created_at": _now()}
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$push": {f"chapters.$[c].rows.$[r].comments.{target}.$[cm].replies": reply},
         "$set": {"updated_at": _now()}},
        array_filters=[*_row_filters(sr_no), {"cm.id": comment_id}],
    )
    return reply


def set_comment_resolved(episode_id: str, sr_no: int, target: str, comment_id: str, resolved: bool) -> None:
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {f"chapters.$[c].rows.$[r].comments.{target}.$[cm].resolved": resolved,
                   "updated_at": _now()}},
        array_filters=[*_row_filters(sr_no), {"cm.id": comment_id}],
    )


def set_reviewer_complete(episode_id: str, sr_no: int, complete: bool) -> None:
    update_row(episode_id, sr_no, reviewer_complete=complete)


def set_episode_title_translation(episode_id: str, translated_title: str, audio_path: str | None) -> None:
    update_episode(episode_id, translated_title=translated_title, title_audio_path=audio_path)


def set_chapter_title_translation(episode_id: str, chapter_number: int, translated_title: str, audio_path: str | None) -> None:
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"chapters.$[c].translated_title": translated_title,
                   "chapters.$[c].title_audio_path": audio_path,
                   "updated_at": _now()}},
        array_filters=[{"c.chapter_number": chapter_number}],
    )
