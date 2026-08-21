"""Batched LLM calls for translation review: per-row reviewer comments, difficult-word
suggestions, and title/chapter-title translation. Provider-agnostic via core.llm_client.
"""
import json

from core.llm_client import LLMClient

REVIEW_SYSTEM_PROMPT = (
    "You are a professional translation reviewer for children's audiobook scripts. "
    "For each dialogue row you are given (English original + translated line), write a "
    "1-2 sentence reviewer comment covering: why it was translated this way, notable "
    "words/idioms, tone or meaning changes, and kid-appropriateness. If genuinely nothing "
    "stands out, write exactly \"OK - Accurate and natural translation.\" and use flag \"ok\". "
    "Otherwise use flag \"note\". Respond with a JSON array only, one object per input row in "
    "the same order, each shaped as {\"comment\": string, \"flag\": \"ok\"|\"note\"}. "
    "The array length must exactly equal the number of input rows."
)

DIFFICULT_WORDS_SYSTEM_PROMPT = (
    "You are a translation assistant for children's audiobook scripts. For each translated "
    "line you are given, pick out words that are uncommon, ambiguous, or worth double-checking "
    "(skip lines with no such words). For each picked word, suggest 2-3 alternative translations "
    "in the same target language, each with a short English meaning. Respond with a JSON array "
    "only, one object per input row in the same order, each shaped as {\"words\": [{\"word\": "
    "string, \"suggestions\": [{\"text\": string, \"meaning\": string}]}]}. Rows with no "
    "difficult words get an empty \"words\" list. The array length must exactly equal the "
    "number of input rows."
)

TITLE_TRANSLATION_SYSTEM_PROMPT = (
    "You are a translation assistant for children's audiobook scripts. Translate each given "
    "English title into the target language, keeping it natural and age-appropriate. Respond "
    "with a JSON array only, one string per input title in the same order. The array length "
    "must exactly equal the number of input titles."
)

ROW_TRANSLATION_SYSTEM_PROMPT = (
    "You are a translation assistant for children's audiobook scripts. Translate each given "
    "English dialogue line into the target language, keeping tone, meaning, and "
    "kid-appropriateness natural for narration. Respond with a JSON array only, one string per "
    "input line in the same order. The array length must exactly equal the number of input lines."
)


def _extract_json(text: str, opener: str, closer: str) -> list | None:
    text = text.strip()
    start = text.find(opener)
    end = text.rfind(closer)
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _call_json_array(client: LLMClient, system: str, payload: object) -> list | None:
    text = client.complete(system, json.dumps(payload, ensure_ascii=False))
    return _extract_json(text, "[", "]")


def _review_single_row(client: LLMClient, row: dict) -> dict:
    result = _call_json_array(client, REVIEW_SYSTEM_PROMPT, [_row_payload(row)])
    if not result:
        return {"comment": "Review unavailable due to an API error.", "flag": "note"}
    item = result[0]
    return {
        "comment": item.get("comment", "OK - Accurate and natural translation."),
        "flag": item.get("flag", "ok"),
    }


def _row_payload(row: dict) -> dict:
    return {"sr_no": row["sr_no"], "speaker": row["speaker"], "english": row["english"], "translated": row["translated"]}


def review_chapter(client: LLMClient, rows: list[dict]) -> list[dict]:
    """Review one chapter's rows. Batched call -> retry once -> per-row fallback.

    Each row needs sr_no, speaker, english, translated keys. Returns one
    {comment, flag} dict per row, same order.
    """
    payload = [_row_payload(row) for row in rows]
    for _ in range(2):
        result = _call_json_array(client, REVIEW_SYSTEM_PROMPT, payload)
        if result is not None and len(result) == len(rows):
            return [
                {
                    "comment": item.get("comment", "OK - Accurate and natural translation."),
                    "flag": item.get("flag", "ok"),
                }
                for item in result
            ]
    return [_review_single_row(client, row) for row in rows]


def _empty_difficult_words(rows: list[dict]) -> list[dict]:
    return [{"words": []} for _ in rows]


def find_difficult_words(client: LLMClient, rows: list[dict]) -> list[dict]:
    """One {"words": [...]} entry per row, same order. Falls back to empty lists on failure
    (difficult words are a helper aid, not a correctness-critical field)."""
    payload = [{"sr_no": row["sr_no"], "translated": row["translated"]} for row in rows]
    for _ in range(2):
        result = _call_json_array(client, DIFFICULT_WORDS_SYSTEM_PROMPT, payload)
        if result is not None and len(result) == len(rows):
            return [{"words": item.get("words", [])} for item in result]
    return _empty_difficult_words(rows)


def translate_titles(client: LLMClient, titles: list[str], target_language: str) -> list[str]:
    """Translate a batch of titles (episode title + chapter titles) into target_language.
    Falls back to the original English text per title on failure."""
    if not titles:
        return []
    system = f"{TITLE_TRANSLATION_SYSTEM_PROMPT} The target language is {target_language}."
    for _ in range(2):
        result = _call_json_array(client, system, titles)
        if result is not None and len(result) == len(titles):
            return [str(t) for t in result]
    return list(titles)


def translate_rows(client: LLMClient, english_lines: list[str], target_language: str) -> list[str]:
    """Translate a batch of dialogue lines into target_language. Falls back to the original
    English text per line on failure (matches translate_titles' fallback behavior so a
    flaky LLM call never leaves a row silently blank)."""
    if not english_lines:
        return []
    system = f"{ROW_TRANSLATION_SYSTEM_PROMPT} The target language is {target_language}."
    for _ in range(2):
        result = _call_json_array(client, system, english_lines)
        if result is not None and len(result) == len(english_lines):
            return [str(t) for t in result]
    return list(english_lines)
