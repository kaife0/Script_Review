import sys

from dotenv import load_dotenv

load_dotenv()

from core import db, native_review, storage
from core.alignment import align_lines
from core.tts import transcode_to_mp3

reencode = "--reencode" in sys.argv

for episode in db.episodes_collection().find({"chapters.native_review": {"$exists": True}}):
    episode_id = str(episode["_id"])
    for chapter in episode["chapters"]:
        for track, data in chapter.get("native_review", {}).items():
            path = f"audio/{data['audio_path']}"
            mp3 = storage.read_bytes(episode_id, path)
            if reencode:
                mp3 = transcode_to_mp3(mp3, "audio.mp3", bitrate="192k")
            lines = [(r["sr_no"], r[native_review.TEXT_FIELDS[track]]) for r in chapter["rows"]]
            cues = align_lines(mp3, lines)
            if reencode:
                storage.save_bytes(episode_id, path, mp3)
            native_review.resync_track(episode_id, chapter["chapter_number"], track, cues, data["comments"])
            print(f"{episode['title']} | ch {chapter['chapter_number']} | {track}: {len(cues)}/{len(lines)} lines synced")
