"""Row and comment CRUD on the nested chapters[].rows[] array, via MongoDB arrayFilters.
Split out of core.db to keep episode-level CRUD and row-level CRUD independently readable.
"""
import uuid

from bson import ObjectId

from core.db import episodes_collection, _now

COMMENT_TARGETS = ("english", "ai_translation", "reviewer_edit", "audio")
ANCHORABLE_COMMENT_TARGETS = ("english", "ai_translation")


def _row_filters(sr_no: int) -> list[dict]:
    return [{"r.sr_no": sr_no}, {"c.rows.sr_no": sr_no}]


def update_row(episode_id: str, sr_no: int, **fields) -> None:
    """Update a single row (by sr_no) inside whichever chapter contains it, via arrayFilters."""
    set_fields = {f"chapters.$[c].rows.$[r].{k}": v for k, v in fields.items()}
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {**set_fields, "updated_at": _now()}},
        array_filters=_row_filters(sr_no),
    )


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


def set_reviewer_complete(episode_id: str, sr_no: int, complete: bool) -> None:
    update_row(episode_id, sr_no, reviewer_complete=complete)


def add_comment(episode_id: str, sr_no: int, target: str, text: str, author: str,
                 anchor: dict | None = None) -> dict:
    comment = {
        "id": uuid.uuid4().hex, "text": text, "author": author,
        "created_at": _now(), "resolved": False, "replies": [],
        "anchor": anchor if (anchor and target in ANCHORABLE_COMMENT_TARGETS) else None,
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


def delete_comment(episode_id: str, sr_no: int, target: str, comment_id: str) -> None:
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$pull": {f"chapters.$[c].rows.$[r].comments.{target}": {"id": comment_id}},
         "$set": {"updated_at": _now()}},
        array_filters=_row_filters(sr_no),
    )


def delete_comment_reply(episode_id: str, sr_no: int, target: str, comment_id: str, reply_id: str) -> None:
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$pull": {f"chapters.$[c].rows.$[r].comments.{target}.$[cm].replies": {"id": reply_id}},
         "$set": {"updated_at": _now()}},
        array_filters=[*_row_filters(sr_no), {"cm.id": comment_id}],
    )


def set_chapter_title_translation(episode_id: str, chapter_number: int, translated_title: str, audio_path: str | None) -> None:
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"chapters.$[c].translated_title": translated_title,
                   "chapters.$[c].title_audio_path": audio_path,
                   "updated_at": _now()}},
        array_filters=[{"c.chapter_number": chapter_number}],
    )
