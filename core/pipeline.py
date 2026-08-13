"""Idempotent episode pipeline: parse -> LLM review -> TTS. Writes incrementally to Mongo
so a retry on the same episode_id skips whatever stage/row already completed.
"""
import os
import tempfile

from anthropic import Anthropic

from core import db, storage
from core.llm_review import review_chapter
from core.parsing import parse_pair
from core.tts import get_backend
from core.voice_registry import VoicePackMissingError, cast_voices
from config.languages import has_tts_backend, voice_key


def _rows_to_chapters(parsed: dict) -> list[dict]:
    """Reshape parsing.py's flat row list into Mongo's chapters[].rows[] embedded schema."""
    chapters: dict[int, dict] = {}
    for row in parsed["rows"]:
        chapter = chapters.setdefault(row["chapter"], {
            "chapter_number": row["chapter"],
            "title": row["chapter_title"],
            "rows": [],
        })
        chapter["rows"].append({
            "sr_no": row["sr_no"],
            "speaker": row["speaker"],
            "english": row["english"],
            "translated": row["translated"],
            "review_comment": None,
            "review_flag": None,
            "audio_path": None,
            "audio_status": "pending",
            "human_verified": False,
        })
    return [chapters[num] for num in sorted(chapters)]


def _run_parse_stage(episode_id: str, episode: dict) -> dict:
    db.set_episode_status(episode_id, "parsing")
    with tempfile.TemporaryDirectory() as tmp_dir:
        english_path = os.path.join(tmp_dir, "english.docx")
        translated_path = os.path.join(tmp_dir, "translated.docx")
        with open(english_path, "wb") as f:
            f.write(storage.read_bytes(episode_id, "uploads/english.docx"))
        with open(translated_path, "wb") as f:
            f.write(storage.read_bytes(episode_id, "uploads/translated.docx"))
        parsed = parse_pair(english_path, translated_path)

    chapters = _rows_to_chapters(parsed)
    db.set_episode_chapters(episode_id, chapters)
    if parsed["warnings"]:
        db.update_episode(episode_id, alignment_warnings=parsed["warnings"])
    return db.get_episode(episode_id)


def _run_review_stage(episode_id: str, episode: dict) -> dict:
    db.set_episode_status(episode_id, "reviewing")
    client = Anthropic()
    for chapter in episode["chapters"]:
        if db.chapter_rows_reviewed(chapter):
            continue
        reviews = review_chapter(client, chapter["rows"])
        for row, review in zip(chapter["rows"], reviews):
            db.update_row(episode_id, row["sr_no"],
                           review_comment=review["comment"], review_flag=review["flag"])
    return db.get_episode(episode_id)


def _run_tts_stage(episode_id: str, episode: dict) -> dict:
    target_lang = episode["target_lang"]
    if not has_tts_backend(target_lang, os.environ.get("TTS_BACKEND", "sherpa_onnx")):
        db.update_episode(episode_id, tts_skipped_reason="no_voice_pack_for_language")
        return db.get_episode(episode_id)

    db.set_episode_status(episode_id, "tts")
    backend = get_backend()
    voice_lang = voice_key(target_lang)

    speakers = list(dict.fromkeys(
        row["speaker"] for chapter in episode["chapters"] for row in chapter["rows"]
    ))
    casting = cast_voices(voice_lang, speakers)

    for chapter in episode["chapters"]:
        for row in chapter["rows"]:
            if db.row_audio_done(row):
                continue
            text = row["translated"].strip()
            if not text:
                db.update_row(episode_id, row["sr_no"], audio_status="done", audio_path=None)
                continue
            try:
                voice = casting[row["speaker"]]
                audio_bytes = backend.synthesize(text, voice, voice_lang)
                filename = f"row_{row['sr_no']}.mp3"
                storage.save_bytes(episode_id, f"audio/{filename}", audio_bytes)
                db.update_row(episode_id, row["sr_no"], audio_status="done", audio_path=filename)
            except Exception as exc:
                db.update_row(episode_id, row["sr_no"], audio_status="failed")
                raise RuntimeError(f"TTS failed for row {row['sr_no']}: {exc}") from exc

    return db.get_episode(episode_id)


def run_pipeline(episode_id: str) -> None:
    """Run parse -> review -> TTS for an episode. Idempotent: skips stages/rows already done.
    Expects english.docx/translated.docx already saved under uploads/ for this episode.
    """
    try:
        episode = db.get_episode(episode_id)
        if episode is None:
            raise RuntimeError(f"Episode {episode_id} not found")

        if not episode["chapters"]:
            episode = _run_parse_stage(episode_id, episode)

        episode = _run_review_stage(episode_id, episode)
        episode = _run_tts_stage(episode_id, episode)

        db.set_episode_status(episode_id, "done")
    except VoicePackMissingError as exc:
        db.set_episode_status(episode_id, "failed", error_message=str(exc))
    except Exception as exc:
        db.set_episode_status(episode_id, "failed", error_message=str(exc))
        raise
