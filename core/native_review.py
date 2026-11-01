import uuid

from bson import ObjectId

from core.db import episodes_collection, _now
from core.db_rows import _chapter_filter

TRACKS = ("en", "native")
TEXT_FIELDS = {"en": "english", "native": "reviewer_text"}


def _update(episode_id: str, chapter_number: int, update: dict) -> None:
    update.setdefault("$set", {})["updated_at"] = _now()
    episodes_collection().update_one({"_id": ObjectId(episode_id)}, update,
                                     array_filters=_chapter_filter(chapter_number))


def chapter_uploaded(chapter: dict) -> bool:
    return all(t in chapter.get("native_review", {}) for t in TRACKS)


def final_audio_status(episode: dict) -> str:
    chapters = episode["chapters"]
    if chapters and all(c.get("final_audio_verified") for c in chapters):
        return "verified"
    if chapters and all(chapter_uploaded(c) for c in chapters):
        return "uploaded"
    return "pending"


def set_track(episode_id: str, chapter_number: int, track: str, audio_path: str, cues: list[dict]) -> None:
    _update(episode_id, chapter_number, {"$set": {
        f"chapters.$[c].native_review.{track}": {
            "audio_path": audio_path, "cues": cues, "comments": [], "uploaded_at": _now()},
        "chapters.$[c].final_audio_verified": False}})


def line_at(cues: list[dict], time: float) -> int | None:
    return next((c["sr_no"] for c in reversed(cues) if c["start"] <= time), None)


def resync_track(episode_id: str, chapter_number: int, track: str, cues: list[dict], comments: list[dict]) -> None:
    for comment in comments:
        comment["sr_no"] = line_at(cues, comment["time"])
    _update(episode_id, chapter_number, {"$set": {
        f"chapters.$[c].native_review.{track}.cues": cues,
        f"chapters.$[c].native_review.{track}.comments": comments,
        f"chapters.$[c].native_review.{track}.uploaded_at": _now()}})


def set_verified(episode_id: str, chapter_number: int, verified: bool) -> None:
    _update(episode_id, chapter_number, {"$set": {"chapters.$[c].final_audio_verified": verified}})


def add_comment(episode_id: str, chapter_number: int, track: str, time: float, sr_no: int | None, text: str) -> dict:
    comment = {"id": uuid.uuid4().hex, "time": time, "sr_no": sr_no, "text": text, "created_at": _now()}
    _update(episode_id, chapter_number, {"$push": {f"chapters.$[c].native_review.{track}.comments": comment}})
    return comment


def delete_comment(episode_id: str, chapter_number: int, track: str, comment_id: str) -> None:
    _update(episode_id, chapter_number,
            {"$pull": {f"chapters.$[c].native_review.{track}.comments": {"id": comment_id}}})
