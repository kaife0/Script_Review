import os
import re
from difflib import SequenceMatcher

import requests

API_URL = "https://api.elevenlabs.io/v1/forced-alignment"
_WORD = re.compile(r"\w+")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def align_lines(audio: bytes, lines: list[tuple[int, str]]) -> list[dict]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    lines = [(sr_no, text.strip()) for sr_no, text in lines if _tokens(text)]
    resp = requests.post(
        API_URL,
        headers={"xi-api-key": api_key},
        files={"file": ("audio.mp3", audio, "audio/mpeg")},
        data={"text": "\n".join(text for _, text in lines)},
        timeout=240,
    )
    resp.raise_for_status()

    expected = [(i, tok) for i, (_, text) in enumerate(lines) for tok in _tokens(text)]
    spoken = [(tok, w["start"], w["end"]) for w in resp.json()["words"] for tok in _tokens(w["text"])]
    spans: dict[int, list[float]] = {}
    matcher = SequenceMatcher(None, [t for _, t in expected], [t for t, _, _ in spoken], autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            line = expected[a + k][0]
            _, start, end = spoken[b + k]
            span = spans.setdefault(line, [start, end])
            span[0], span[1] = min(span[0], start), max(span[1], end)
    if not spans:
        raise RuntimeError("Could not match the script to this audio")
    return [{"sr_no": lines[i][0], "start": s, "end": e} for i, (s, e) in sorted(spans.items())]
