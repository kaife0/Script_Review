"""Batched Claude review pass: generates a reviewer comment + flag per dialogue row."""
import json
import os
from anthropic import Anthropic

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")

SYSTEM_PROMPT = (
    "You are a professional translation reviewer for children's audiobook scripts. "
    "For each dialogue row you are given (English original + translated line), write a "
    "1-2 sentence reviewer comment covering: why it was translated this way, notable "
    "words/idioms, tone or meaning changes, and kid-appropriateness. If genuinely nothing "
    "stands out, write exactly \"OK - Accurate and natural translation.\" and use flag \"ok\". "
    "Otherwise use flag \"note\". Respond with a JSON array only, one object per input row in "
    "the same order, each shaped as {\"comment\": string, \"flag\": \"ok\"|\"note\"}. "
    "The array length must exactly equal the number of input rows."
)


def _build_user_message(rows: list[dict]) -> str:
    payload = [
        {"sr_no": r["sr_no"], "speaker": r["speaker"], "english": r["english"], "translated": r["translated"]}
        for r in rows
    ]
    return json.dumps(payload, ensure_ascii=False)


def _extract_json_array(text: str) -> list | None:
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _call_claude(client: Anthropic, rows: list[dict]) -> list | None:
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(rows)}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    result = _extract_json_array(text)
    if result is None or len(result) != len(rows):
        return None
    return result


def _review_single_row(client: Anthropic, row: dict) -> dict:
    result = _call_claude(client, [row])
    if result is None:
        return {"comment": "Review unavailable due to an API error.", "flag": "note"}
    item = result[0]
    return {
        "comment": item.get("comment", "OK - Accurate and natural translation."),
        "flag": item.get("flag", "ok"),
    }


def review_chapter(client: Anthropic, rows: list[dict]) -> list[dict]:
    """Review one chapter's rows. Batched call -> retry once -> per-row fallback.

    Each row needs sr_no, speaker, english, translated keys. Returns one
    {comment, flag} dict per row, same order.
    """
    for _ in range(2):
        result = _call_claude(client, rows)
        if result is not None:
            return [
                {
                    "comment": item.get("comment", "OK - Accurate and natural translation."),
                    "flag": item.get("flag", "ok"),
                }
                for item in result
            ]
    return [_review_single_row(client, row) for row in rows]
