import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.tts import SherpaOnnxBackend, _sherpa_engine_cache  # noqa: E402
from core.voice_registry import CONFIG_PATH  # noqa: E402

SAMPLE = {
    "italian": "Il gattino è coraggioso. « Andiamo! »",
    "german": "Das Kätzchen ist mutig. „Los geht's!“",
    "spanish": "El gatito es valiente. « ¡Vamos! »",
    "french": "Le chaton est courageux. « Allons-y ! »",
}


def main() -> int:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    backend = SherpaOnnxBackend()
    failures = 0
    for lang, lang_config in config.items():
        for voice in lang_config["voices"]:
            try:
                mp3 = backend.synthesize(SAMPLE.get(lang, "Hello."), voice, lang)
                assert len(mp3) > 1000, f"only {len(mp3)} bytes"
                print(f"ok    {lang}/{voice['id']}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {lang}/{voice['id']}: {exc}")
            _sherpa_engine_cache.clear()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
