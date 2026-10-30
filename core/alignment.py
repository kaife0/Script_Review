import os
from itertools import islice

import requests

API_URL = "https://api.elevenlabs.io/v1/forced-alignment"


def align_lines(audio: bytes, lines: list[tuple[int, str]]) -> list[dict]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    lines = [(sr_no, text.strip()) for sr_no, text in lines if text.strip()]
    resp = requests.post(
        API_URL,
        headers={"xi-api-key": api_key},
        files={"file": ("audio.mp3", audio, "audio/mpeg")},
        data={"text": "\n".join(text for _, text in lines)},
        timeout=300,
    )
    resp.raise_for_status()
    words = iter([w for w in resp.json()["words"] if w["text"].strip()])
    cues = []
    for sr_no, text in lines:
        chunk = list(islice(words, len(text.split())))
        if chunk:
            cues.append({"sr_no": sr_no, "start": chunk[0]["start"], "end": chunk[-1]["end"]})
    return cues
