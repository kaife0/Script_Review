"""MongoDB connection and episode CRUD. Single `episodes` collection, chapters/rows embedded."""
import os
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import MongoClient

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


def row_audio_done(row: dict) -> bool:
    return row.get("audio_status") == "done"


def verification_counts(episode: dict) -> tuple[int, int]:
    """Return (verified_rows, total_rows) across all chapters."""
    rows = [row for chapter in episode["chapters"] for row in chapter["rows"]]
    verified = sum(1 for row in rows if row.get("human_verified"))
    return verified, len(rows)
