import hashlib
import re
import uuid

from bson import ObjectId

from core.db import episodes_collection, get_db, _now
from core.db_rows import _chapter_filter

TRACKS = ("en", "native")
TEXT_FIELDS = {"en": "english", "native": "reviewer_text"}
_EP_NUMBER = re.compile(r"\bep(?:isode)?\.?\s*(\d+)", re.I)


def english_collection():
    return get_db().english_audio


def shared_namespace(key: str) -> str:
    return f"english_{key}"


def _fingerprint(episode: dict) -> str:
    text = "\n".join(" ".join(r["english"].lower().split())
                     for c in episode["chapters"] for r in c["rows"])
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _episode_number(title: str) -> int | None:
    m = _EP_NUMBER.search(title or "")
    return int(m.group(1)) if m else None


def english_key(episode: dict) -> str | None:
    if episode.get("english_key"):
        return episode["english_key"]
    if not episode.get("chapters"):
        return None
    key = _fingerprint(episode)
    number = _episode_number(episode["title"])
    if number is not None and not episodes_collection().find_one({"english_key": key}, {"_id": 1}):
        for other in episodes_collection().find(
                {"english_key": {"$exists": True}, "target_lang": {"$ne": episode["target_lang"]}},
                {"title": 1, "english_key": 1}):
            if _episode_number(other["title"]) == number:
                key = other["english_key"]
                break
    set_english_key(episode["_id"], key)
    episode["english_key"] = key
    return key


def set_english_key(episode_id: str, key: str) -> None:
    episodes_collection().update_one({"_id": ObjectId(episode_id)}, {"$set": {"english_key": key}})


def linked_episodes(key: str, exclude_id: str) -> list[dict]:
    return [{**d, "_id": str(d["_id"])} for d in episodes_collection().find(
        {"english_key": key, "_id": {"$ne": ObjectId(exclude_id)}},
        {"title": 1, "target_lang_name": 1})]


def shared_tracks(keys) -> dict[str, dict]:
    docs = english_collection().find({"_id": {"$in": [k for k in keys if k]}})
    return {d["_id"]: d.get("chapters", {}) for d in docs}


def is_shared(chapter: dict, track: str) -> bool:
    return track == "en" and "en" not in chapter.get("native_review", {})


def chapter_tracks(chapter: dict, shared: dict | None) -> dict:
    tracks = dict(chapter.get("native_review", {}))
    if "en" not in tracks and shared and str(chapter["chapter_number"]) in shared:
        tracks["en"] = shared[str(chapter["chapter_number"])]
    return tracks


def chapter_uploaded(chapter: dict, shared: dict | None = None) -> bool:
    tracks = chapter_tracks(chapter, shared)
    return all(t in tracks for t in TRACKS)


def final_audio_status(episode: dict, shared: dict | None = None) -> str:
    chapters = episode["chapters"]
    if chapters and all(c.get("final_audio_verified") for c in chapters):
        return "verified"
    if chapters and all(chapter_uploaded(c, shared) for c in chapters):
        return "uploaded"
    return "pending"


def _target(episode: dict, chapter_number: int, track: str):
    chapter = next(c for c in episode["chapters"] if c["chapter_number"] == chapter_number)
    if is_shared(chapter, track):
        return english_collection(), {"_id": english_key(episode)}, f"chapters.{chapter_number}", None
    return (episodes_collection(), {"_id": ObjectId(episode["_id"])},
            f"chapters.$[c].native_review.{track}", _chapter_filter(chapter_number))


def _update(episode: dict, chapter_number: int, track: str, build) -> None:
    coll, query, prefix, array_filters = _target(episode, chapter_number, track)
    update = build(prefix)
    update.setdefault("$set", {})["updated_at"] = _now()
    coll.update_one(query, update, array_filters=array_filters, upsert=array_filters is None)


def set_track(episode: dict, chapter_number: int, track: str, audio_path: str, cues: list[dict]) -> int:
    _update(episode, chapter_number, track, lambda p: {"$set": {
        p: {"audio_path": audio_path, "cues": cues, "comments": [], "uploaded_at": _now()}}})
    chapter = next(c for c in episode["chapters"] if c["chapter_number"] == chapter_number)
    query = ({"english_key": english_key(episode)} if is_shared(chapter, track)
             else {"_id": ObjectId(episode["_id"])})
    return episodes_collection().update_many(
        query, {"$set": {"chapters.$[c].final_audio_verified": False}},
        array_filters=_chapter_filter(chapter_number)).matched_count


def line_at(cues: list[dict], time: float) -> int | None:
    return next((c["sr_no"] for c in reversed(cues) if c["start"] <= time), None)


def resync_track(episode: dict, chapter_number: int, track: str, cues: list[dict], comments: list[dict]) -> None:
    for comment in comments:
        comment["sr_no"] = line_at(cues, comment["time"])
    _update(episode, chapter_number, track, lambda p: {"$set": {
        f"{p}.cues": cues, f"{p}.comments": comments, f"{p}.uploaded_at": _now()}})


def set_verified(episode_id: str, chapter_number: int, verified: bool) -> None:
    episodes_collection().update_one(
        {"_id": ObjectId(episode_id)},
        {"$set": {"chapters.$[c].final_audio_verified": verified, "updated_at": _now()}},
        array_filters=_chapter_filter(chapter_number))


def add_comment(episode: dict, chapter_number: int, track: str, time: float, sr_no: int | None, text: str) -> dict:
    comment = {"id": uuid.uuid4().hex, "time": time, "sr_no": sr_no, "text": text, "created_at": _now()}
    _update(episode, chapter_number, track, lambda p: {"$push": {f"{p}.comments": comment}})
    return comment


def delete_comment(episode: dict, chapter_number: int, track: str, comment_id: str) -> None:
    _update(episode, chapter_number, track, lambda p: {"$pull": {f"{p}.comments": {"id": comment_id}}})
