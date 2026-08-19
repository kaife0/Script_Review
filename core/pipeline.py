"""Idempotent episode pipeline: parse -> translate titles -> review -> difficult words -> TTS.
Writes incrementally to Mongo so a retry on the same episode_id skips whatever already completed.
"""
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from core import db, storage
from core.llm_client import get_llm_client
from core.llm_review import review_chapter, find_difficult_words, translate_titles
from core.parsing import parse_pair
from core.tts import get_backend
from core.voice_registry import VoicePackMissingError, cast_voices
from config.languages import has_tts_backend, voice_key

NARRATOR_VOICE_TARGET = "Narrator"

logger = logging.getLogger("pipeline")


def _rows_to_chapters(parsed: dict) -> list[dict]:
    """Reshape parsing.py's flat row list into Mongo's chapters[].rows[] embedded schema."""
    chapters: dict[int, dict] = {}
    for row in parsed["rows"]:
        chapter = chapters.setdefault(row["chapter"], {
            "chapter_number": row["chapter"],
            "title": row["chapter_title"],
            "translated_title": None,
            "title_audio_path": None,
            "rows": [],
        })
        chapter["rows"].append({
            "sr_no": row["sr_no"],
            "speaker": row["speaker"],
            "english": row["english"],
            "translated": row["translated"],
            "reviewer_text": row["translated"],
            "reviewer_history": [],
            "reviewer_history_index": -1,
            "reviewer_complete": False,
            "comments": {target: [] for target in db.COMMENT_TARGETS},
            "difficult_words": None,
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
    db.update_episode(episode_id, title=parsed["title"], translated_title=None, title_audio_path=None)
    if parsed["warnings"]:
        db.update_episode(episode_id, alignment_warnings=parsed["warnings"])

    total_rows = sum(len(c["rows"]) for c in chapters)
    logger.info(
        "episode %s: parsed %d chapters, %d rows, title=%r, warnings=%s",
        episode_id, len(chapters), total_rows, parsed["title"], parsed["warnings"],
    )
    return db.get_episode(episode_id)


def _run_title_translation_stage(episode_id: str, episode: dict) -> dict:
    if db.titles_translated(episode):
        logger.info("episode %s: titles already translated, skipping", episode_id)
        return episode

    db.set_episode_status(episode_id, "translating_titles")
    client = get_llm_client()
    target_lang = episode["target_lang_name"]

    titles = [episode["title"]] + [c["title"] for c in episode["chapters"]]
    translated = translate_titles(client, titles, target_lang)
    episode_title, chapter_titles = translated[0], translated[1:]
    logger.info("episode %s: translated %d titles into %s", episode_id, len(translated), target_lang)

    audio_lang = voice_key(episode["target_lang"])
    tts_enabled = has_tts_backend(episode["target_lang"], os.environ.get("TTS_BACKEND", "sherpa_onnx"))
    backend = get_backend() if tts_enabled else None
    narrator_voice = cast_voices(audio_lang, [NARRATOR_VOICE_TARGET])[NARRATOR_VOICE_TARGET] if tts_enabled else None

    def _narrate(text: str, filename: str) -> str | None:
        if not tts_enabled or not text.strip():
            return None
        audio_bytes = backend.synthesize(text, narrator_voice, audio_lang)
        storage.save_bytes(episode_id, f"audio/{filename}", audio_bytes)
        return filename

    episode_audio = _narrate(episode_title, "title.mp3")
    db.set_episode_title_translation(episode_id, episode_title, episode_audio)

    for chapter, title in zip(episode["chapters"], chapter_titles):
        chapter_audio = _narrate(title, f"chapter_{chapter['chapter_number']}_title.mp3")
        db.set_chapter_title_translation(episode_id, chapter["chapter_number"], title, chapter_audio)

    return db.get_episode(episode_id)


def _run_review_stage(episode_id: str, episode: dict) -> dict:
    db.set_episode_status(episode_id, "reviewing")
    client = get_llm_client()
    for chapter in episode["chapters"]:
        if db.chapter_rows_reviewed(chapter):
            logger.info("episode %s: chapter %d already reviewed, skipping",
                        episode_id, chapter["chapter_number"])
            continue
        reviews = review_chapter(client, chapter["rows"])
        for row, review in zip(chapter["rows"], reviews):
            db.update_row(episode_id, row["sr_no"],
                           review_comment=review["comment"], review_flag=review["flag"])
        logger.info("episode %s: chapter %d reviewed, %d rows",
                    episode_id, chapter["chapter_number"], len(reviews))
    return db.get_episode(episode_id)


def _run_difficult_words_stage(episode_id: str, episode: dict) -> dict:
    db.set_episode_status(episode_id, "finding_difficult_words")
    client = get_llm_client()
    for chapter in episode["chapters"]:
        if db.chapter_words_found(chapter):
            logger.info("episode %s: chapter %d difficult words already found, skipping",
                        episode_id, chapter["chapter_number"])
            continue
        results = find_difficult_words(client, chapter["rows"])
        for row, result in zip(chapter["rows"], results):
            db.update_row(episode_id, row["sr_no"], difficult_words=result["words"])
        logger.info("episode %s: chapter %d difficult words found, %d rows",
                    episode_id, chapter["chapter_number"], len(results))
    return db.get_episode(episode_id)


def _run_tts_stage(episode_id: str, episode: dict) -> dict:
    target_lang = episode["target_lang"]
    if not has_tts_backend(target_lang, os.environ.get("TTS_BACKEND", "sherpa_onnx")):
        logger.info("episode %s: no TTS voice pack for %s, skipping audio", episode_id, target_lang)
        db.update_episode(episode_id, tts_skipped_reason="no_voice_pack_for_language")
        return db.get_episode(episode_id)

    db.set_episode_status(episode_id, "tts")
    backend = get_backend()
    voice_lang = voice_key(target_lang)

    speakers = list(dict.fromkeys(
        row["speaker"] for chapter in episode["chapters"] for row in chapter["rows"]
    ))
    casting = cast_voices(voice_lang, speakers)
    logger.info("episode %s: casting for %s -> %s", episode_id, voice_lang,
                {s: v.get("id") for s, v in casting.items()})

    pending_rows = [
        row for chapter in episode["chapters"] for row in chapter["rows"]
        if not db.row_audio_done(row)
    ]
    logger.info("episode %s: synthesizing audio for %d/%d rows",
                episode_id, len(pending_rows), sum(len(c["rows"]) for c in episode["chapters"]))

    def _synthesize_row(row: dict) -> None:
        text = row["translated"].strip()
        if not text:
            db.update_row(episode_id, row["sr_no"], audio_status="done", audio_path=None)
            return
        voice = casting[row["speaker"]]
        audio_bytes = backend.synthesize(text, voice, voice_lang)
        filename = f"row_{row['sr_no']}.mp3"
        storage.save_bytes(episode_id, f"audio/{filename}", audio_bytes)
        db.update_row(episode_id, row["sr_no"], audio_status="done", audio_path=filename)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_synthesize_row, row): row for row in pending_rows}
        first_error = None
        for future in as_completed(futures):
            row = futures[future]
            try:
                future.result()
            except Exception as exc:
                db.update_row(episode_id, row["sr_no"], audio_status="failed")
                if first_error is None:
                    first_error = RuntimeError(f"TTS failed for row {row['sr_no']}: {exc}")
        if first_error is not None:
            raise first_error

    logger.info("episode %s: audio synthesis complete", episode_id)
    return db.get_episode(episode_id)


def run_pipeline(episode_id: str) -> None:
    """Run parse -> translate titles -> review -> difficult words -> TTS for an episode.
    Idempotent: skips stages/rows already done. Expects english.docx/translated.docx
    already saved under uploads/ for this episode.
    """
    logger.info("episode %s: pipeline starting", episode_id)
    try:
        episode = db.get_episode(episode_id)
        if episode is None:
            raise RuntimeError(f"Episode {episode_id} not found")

        if not episode["chapters"]:
            episode = _run_parse_stage(episode_id, episode)

        episode = _run_title_translation_stage(episode_id, episode)
        episode = _run_review_stage(episode_id, episode)
        episode = _run_difficult_words_stage(episode_id, episode)
        episode = _run_tts_stage(episode_id, episode)

        db.set_episode_status(episode_id, "done")
        logger.info("episode %s: pipeline done", episode_id)
    except VoicePackMissingError as exc:
        logger.warning("episode %s: pipeline failed (missing voice pack): %s", episode_id, exc)
        db.set_episode_status(episode_id, "failed", error_message=str(exc))
    except Exception as exc:
        logger.exception("episode %s: pipeline failed", episode_id)
        db.set_episode_status(episode_id, "failed", error_message=str(exc))
        raise
