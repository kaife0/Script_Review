"""Supported target languages and which TTS backend(s) have voices for each.

Drives the upload-form dropdown and lets the UI say "TTS available" vs
"text-only review" honestly instead of failing mid-pipeline. `voice_backends`
lists backend keys (see core/tts.py) that have a configured voice pool for
this language; empty list = review-only, no audio.
"""

SUPPORTED_LANGUAGES = [
    {"code": "it", "name": "Italian", "voice_key": "italian", "voice_backends": ["sherpa_onnx"]},
    {"code": "de", "name": "German", "voice_key": "german", "voice_backends": []},
    {"code": "es", "name": "Spanish", "voice_key": "spanish", "voice_backends": []},
    {"code": "fr", "name": "French", "voice_key": "french", "voice_backends": []},
]

_BY_CODE = {lang["code"]: lang for lang in SUPPORTED_LANGUAGES}


def get_language(code: str) -> dict | None:
    return _BY_CODE.get(code)


def language_name(code: str) -> str:
    lang = get_language(code)
    return lang["name"] if lang else code


def has_tts_backend(code: str, backend: str) -> bool:
    lang = get_language(code)
    return bool(lang) and backend in lang["voice_backends"]


def voice_key(code: str) -> str:
    """Key into config/voice_casting.json for this language code (e.g. 'it' -> 'italian')."""
    lang = get_language(code)
    return lang["voice_key"] if lang else code
