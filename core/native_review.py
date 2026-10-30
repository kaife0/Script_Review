import uuid

from bson import ObjectId

from core.db import episodes_collection, _now
from core.db_rows import _chapter_filter

TRACKS = ("en", "native")


def _update(episode_id: str, chapter_number: int, update: dict) -> None:
    update.setdefault("$set", {})["updated_at"] = _now()
    episodes_collection().update_one({"_id": ObjectId(episode_id)}, update,
                                     array_filters=_chapter_filter(chapter_number))


def set_track(episode_id: str, chapter_number: int, track: str, audio_path: str, cues: list[dict]) -> None:
    _update(episode_id, chapter_number, {"$set": {f"chapters.$[c].native_review.{track}": {
        "audio_path": audio_path, "cues": cues, "comments": [], "uploaded_at": _now()}}})


def add_comment(episode_id: str, chapter_number: int, track: str, time: float, sr_no: int | None, text: str) -> dict:
    comment = {"id": uuid.uuid4().hex, "time": time, "sr_no": sr_no, "text": text, "created_at": _now()}
    _update(episode_id, chapter_number, {"$push": {f"chapters.$[c].native_review.{track}.comments": comment}})
    return comment


def delete_comment(episode_id: str, chapter_number: int, track: str, comment_id: str) -> None:
    _update(episode_id, chapter_number,
            {"$pull": {f"chapters.$[c].native_review.{track}.comments": {"id": comment_id}}})
