"""Language -> voice pool config, and voice-pack path resolution."""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "voice_casting.json")
VOICES_DIR = os.path.join(os.path.dirname(__file__), "..", "voices")

RELEASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"


class VoicePackMissingError(Exception):
    pass


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_language_config(language: str) -> dict:
    config = _load_config()
    lang_config = config.get(language.lower())
    if lang_config is None:
        available = ", ".join(config.keys())
        raise VoicePackMissingError(
            f"No voice pool configured for language '{language}'. "
            f"Available languages: {available or 'none'}. "
            f"Add an entry to config/voice_casting.json and download the voice pack."
        )
    return lang_config


def voice_pack_dir(language: str, voice_id: str) -> str:
    lang_config = get_language_config(language)
    prefix = lang_config["release_prefix"]
    return os.path.join(VOICES_DIR, f"{prefix}{voice_id}")


def resolve_voice_model(language: str, voice_id: str) -> dict:
    """Return {model, tokens, data_dir} paths for a voice, or raise if not downloaded."""
    pack_dir = voice_pack_dir(language, voice_id)
    if not os.path.isdir(pack_dir):
        raise VoicePackMissingError(
            f"Voice pack for '{language}' voice '{voice_id}' not found at {pack_dir}. "
            f"Run scripts/download_voices.sh to download it from the sherpa-onnx tts-models release."
        )
    model_path = None
    tokens_path = None
    for fname in os.listdir(pack_dir):
        if fname.endswith(".onnx"):
            model_path = os.path.join(pack_dir, fname)
        elif fname == "tokens.txt":
            tokens_path = os.path.join(pack_dir, fname)
    if not model_path or not tokens_path:
        raise VoicePackMissingError(
            f"Voice pack directory {pack_dir} is incomplete (missing .onnx model or tokens.txt)."
        )
    espeak_data = os.path.join(pack_dir, "espeak-ng-data")
    return {
        "model": model_path,
        "tokens": tokens_path,
        "data_dir": espeak_data if os.path.isdir(espeak_data) else "",
    }


def cast_voices(language: str, speakers: list[str]) -> dict[str, dict]:
    """Assign each distinct speaker a voice from the language's pool (round-robin, narrator pinned)."""
    lang_config = get_language_config(language)
    voices = lang_config["voices"]
    narrator_voice_id = lang_config.get("narrator_voice_id")

    casting: dict[str, dict] = {}
    non_narrator_speakers = []
    for speaker in speakers:
        if speaker.lower() == "narrator" and narrator_voice_id:
            casting[speaker] = next(v for v in voices if v["id"] == narrator_voice_id)
        else:
            non_narrator_speakers.append(speaker)

    pool = [v for v in voices if v["id"] != narrator_voice_id] or voices
    for i, speaker in enumerate(non_narrator_speakers):
        casting[speaker] = pool[i % len(pool)]

    return casting
