"""Row and comment CRUD on the nested chapters[].rows[] array, via MongoDB arrayFilters.
Split out of core.db to keep episode-level CRUD and row-level CRUD independently readable.
"""
import uuid

from bson import ObjectId

from core.db import episodes_collection, _now

COMMENT_TARGETS = ("english", "ai_translation", "reviewer_edit", "audio")
ANCHORABLE_COMMENT_TARGETS = ("english", "ai_translation")
TITLE_COMMENT_TARGETS = ("english", "ai_translation", "reviewer_edit")


def _row_filters(sr_no: int) -> list[dict]:
    return [{"r.sr_no": sr_no}, {"c.rows.sr_no": sr_no}]


def _chapter_filter(chapter_number: int) -> list[dict]:
    return [{"c.chapter_number": chapter_number}]


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


def set_row_audio(episode_id: str, sr_no: int, *, audio_path: str | None, audio_status: str,
                   audio_generated_from_text: str) -> None:
    """Set the TTS-lane audio fields together (initial pipeline pass and manual regenerate both use this)."""
    update_row(episode_id, sr_no, audio_path=audio_path, audio_status=audio_status,
               audio_generated_from_text=audio_generated_from_text)


def set_reviewer_audio(episode_id: str, sr_no: int, *, audio_path: str, audio_status: str) -> None:
    """Set reviewer-uploaded audio and make it primary."""
    update_row(episode_id, sr_no, reviewer_audio_path=audio_path,
               reviewer_audio_status=audio_status, audio_source="reviewer")


def clear_reviewer_audio(episode_id: str, sr_no: int) -> None:
    """Remove reviewer audio and revert to TTS as primary."""
    update_row(episode_id, sr_no, reviewer_audio_path=None,
               reviewer_audio_status=None, audio_source="tts")


def set_audio_source(episode_id: str, sr_no: int, source: str) -> None:
    if source not in ("tts", "reviewer"):
        raise ValueError(f"Invalid audio source: {source!r}")
    update_row(episode_id, sr_no, audio_source=source)


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
                   "chapters.$[c].title_reviewer_text": translated_title,
                   "chapters.$[c].title_reviewer_history": [],
                   "chapters.$[c].title_reviewer_history_index": -1,
                   "chapters.$[c].title_comments": {t: [] for t in TITLE_COMMENT_TARGETS},
                   "chapters.$[c].title_audio_status": "done" if audio_path else "pending",
                   "chapters.$[c].title_audio_generated_from_text": translated_title if audio_path else None,
                   "updated_at": _now()}},
        array_filters=_chapter_filter(chapter_number),
    )


# ---- Title-level CRUD (episode title + chapter titles; not inside chapters[].rows[]) ----

def get_chapter_title(episode_id: str, chapter_number: int) -> dict:
    pipeline = [
        {"$match": {"_id": ObjectId(episode_id)}},
        {"$project": {"chapters": 1}},
        {"$unwind": "$chapters"},
        {"$match": {"chapters.chapter_number": chapter_number}},
        {"$replaceRoot": {"newRoot": "$chapters"}},
        {"$limit": 1},
    ]
    result = list(episodes_collection().aggregate(pipeline))
    if not result:
        raise ValueError(f"Chapter {chapter_number} not found in episode {episode_id}")
    return result[0]


def set_chapter_reviewer_text(episode_id: str, chapter_number: int, text: str) -> None:
    chapter = get_chapter_title(episode_id, chapter_number)
    history = chapter.get("title_reviewer_history", [])
    index = chapter.get("title_reviewer_history_index", -1)
    history = history[:index + 1]
    history.append({"text": text, "edited_at": _now()})
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"chapters.$[c].title_reviewer_text": text,
                   "chapters.$[c].title_reviewer_history": history,
                   "chapters.$[c].title_reviewer_history_index": len(history) - 1,
                   "updated_at": _now()}},
        array_filters=_chapter_filter(chapter_number),
    )


def move_chapter_reviewer_history(episode_id: str, chapter_number: int, direction: str) -> str:
    chapter = get_chapter_title(episode_id, chapter_number)
    history = chapter.get("title_reviewer_history", [])
    index = chapter.get("title_reviewer_history_index", -1)
    if direction == "undo" and index > 0:
        index -= 1
    elif direction == "redo" and index < len(history) - 1:
        index += 1
    text = history[index]["text"] if history else chapter.get("translated_title", "")
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"chapters.$[c].title_reviewer_text": text,
                   "chapters.$[c].title_reviewer_history_index": index,
                   "updated_at": _now()}},
        array_filters=_chapter_filter(chapter_number),
    )
    return text


def set_chapter_title_audio(episode_id: str, chapter_number: int, *, audio_path: str | None,
                             audio_status: str, audio_generated_from_text: str) -> None:
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"chapters.$[c].title_audio_path": audio_path,
                   "chapters.$[c].title_audio_status": audio_status,
                   "chapters.$[c].title_audio_generated_from_text": audio_generated_from_text,
                   "updated_at": _now()}},
        array_filters=_chapter_filter(chapter_number),
    )


def set_episode_reviewer_text(episode_id: str, text: str) -> None:
    from core.db import get_episode
    episode = get_episode(episode_id)
    history = episode.get("title_reviewer_history", [])
    index = episode.get("title_reviewer_history_index", -1)
    history = history[:index + 1]
    history.append({"text": text, "edited_at": _now()})
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"title_reviewer_text": text,
                   "title_reviewer_history": history,
                   "title_reviewer_history_index": len(history) - 1,
                   "updated_at": _now()}},
    )


def move_episode_reviewer_history(episode_id: str, direction: str) -> str:
    from core.db import get_episode
    episode = get_episode(episode_id)
    history = episode.get("title_reviewer_history", [])
    index = episode.get("title_reviewer_history_index", -1)
    if direction == "undo" and index > 0:
        index -= 1
    elif direction == "redo" and index < len(history) - 1:
        index += 1
    text = history[index]["text"] if history else episode.get("translated_title", "")
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"title_reviewer_text": text, "title_reviewer_history_index": index,
                   "updated_at": _now()}},
    )
    return text


def set_episode_title_audio(episode_id: str, *, audio_path: str | None, audio_status: str,
                             audio_generated_from_text: str) -> None:
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"title_audio_path": audio_path, "title_audio_status": audio_status,
                   "title_audio_generated_from_text": audio_generated_from_text,
                   "updated_at": _now()}},
    )


def _title_path_and_filter(locator: str) -> tuple[str, list[dict]]:
    if locator == "episode":
        return "title_comments", []
    chapter_number = int(locator.split(":", 1)[1])
    return "chapters.$[c].title_comments", _chapter_filter(chapter_number)


def add_title_comment(episode_id: str, locator: str, target: str, text: str, author: str,
                       anchor: dict | None = None) -> dict:
    comment = {
        "id": uuid.uuid4().hex, "text": text, "author": author,
        "created_at": _now(), "resolved": False, "replies": [],
        "anchor": anchor if (anchor and target in ANCHORABLE_COMMENT_TARGETS) else None,
    }
    path, array_filters = _title_path_and_filter(locator)
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$push": {f"{path}.{target}": comment}, "$set": {"updated_at": _now()}},
        array_filters=array_filters,
    )
    return comment


def add_title_comment_reply(episode_id: str, locator: str, target: str, comment_id: str,
                             text: str, author: str) -> dict:
    reply = {"id": uuid.uuid4().hex, "text": text, "author": author, "created_at": _now()}
    path, array_filters = _title_path_and_filter(locator)
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$push": {f"{path}.{target}.$[cm].replies": reply}, "$set": {"updated_at": _now()}},
        array_filters=[*array_filters, {"cm.id": comment_id}],
    )
    return reply


def set_title_comment_resolved(episode_id: str, locator: str, target: str, comment_id: str, resolved: bool) -> None:
    path, array_filters = _title_path_and_filter(locator)
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {f"{path}.{target}.$[cm].resolved": resolved, "updated_at": _now()}},
        array_filters=[*array_filters, {"cm.id": comment_id}],
    )


def delete_title_comment(episode_id: str, locator: str, target: str, comment_id: str) -> None:
    path, array_filters = _title_path_and_filter(locator)
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$pull": {f"{path}.{target}": {"id": comment_id}}, "$set": {"updated_at": _now()}},
        array_filters=array_filters,
    )


def delete_title_comment_reply(episode_id: str, locator: str, target: str, comment_id: str, reply_id: str) -> None:
    path, array_filters = _title_path_and_filter(locator)
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$pull": {f"{path}.{target}.$[cm].replies": {"id": reply_id}}, "$set": {"updated_at": _now()}},
        array_filters=[*array_filters, {"cm.id": comment_id}],
    )
