"""Parse paired English/translated docx scripts into aligned dialogue rows."""
from dataclasses import dataclass, asdict
import re
from docx import Document

TITLE_WORDS = "Title|Titolo|Titel|Título|Titre"
THEME_WORDS = "Theme|Tema|Thème"
CHAPTER_WORDS = "Chapter|Capitolo|Kapitel|Capítulo|Chapitre"

CHAPTER_RE = re.compile(rf"^(?:{CHAPTER_WORDS})\s+(\d+)\s*[:\-–]\s*([^\[]+)", re.IGNORECASE)
TITLE_RE = re.compile(rf"^(?:{TITLE_WORDS})\s*:\s*(.+)$", re.IGNORECASE)
THEME_RE = re.compile(rf"^(?:{THEME_WORDS})\s*:\s*(.+)$", re.IGNORECASE)
QUOTED_LINE_RE = re.compile(r'"([^"]*)"\s*,?')


@dataclass
class DialogueRow:
    sr_no: int
    chapter: int
    chapter_title: str
    speaker: str
    english: str
    translated: str


def _split_speaker(line: str) -> tuple[str, str]:
    """Split on the FIRST ': ' into (speaker, text); text may start with a stage direction.

    Tolerates an accidental doubled separator in the source script (e.g. "Zuzu: : (...)"
    instead of "Zuzu: (...)") by stripping one more leading ': ' or ':' off the text."""
    idx = line.find(": ")
    if idx == -1:
        return line.strip(), ""
    speaker, text = line[:idx].strip(), line[idx + 2:].strip()
    while text.startswith(":"):
        text = text[1:].strip()
    return speaker, text


def _parse_doc(path: str) -> tuple[str, str, list[tuple[int, str, list[tuple[str, str]]]]]:
    """Return (title, theme, chapters) where chapters = [(chapter_num, chapter_title, [(speaker, text), ...])].

    Tolerant of `[`/`]` block markers sharing a paragraph with dialogue text
    (rather than requiring them alone on their own line) — any paragraph
    containing '[' opens a block, any paragraph containing ']' closes it,
    and quoted lines are extracted from anywhere within a paragraph.
    """
    doc = Document(path)
    title = ""
    theme = ""
    chapters: list[tuple[int, str, list[tuple[str, str]]]] = []
    current_lines: list[tuple[str, str]] | None = None
    in_block = False

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        if not in_block:
            m = TITLE_RE.match(text)
            if m:
                title = m.group(1).strip()
                continue
            m = THEME_RE.match(text)
            if m:
                theme = m.group(1).strip()
                continue
            m = CHAPTER_RE.match(text)
            if m:
                current_lines = []
                chapters.append((int(m.group(1)), m.group(2).strip(), current_lines))
                text = text[m.end():]
                if "[" not in text:
                    continue

        if "[" in text:
            in_block = True
            text = text.split("[", 1)[1]

        if in_block:
            closes = "]" in text
            if closes:
                text = text.split("]", 1)[0]
            if current_lines is not None:
                for m in QUOTED_LINE_RE.finditer(text):
                    speaker, dialogue = _split_speaker(m.group(1))
                    current_lines.append((speaker, dialogue))
            if closes:
                in_block = False

    return title, theme, chapters


def parse_pair(english_path: str, translated_path: str | None) -> dict:
    """Parse both docs, align by chapter index then row index. Returns rows + alignment warnings.

    `translated_path` may be None when no translated script was uploaded -- every row's
    `translated` field is left blank and no alignment warnings are raised (there's nothing
    to misalign against); the pipeline fills them in via AI translation instead.
    """
    en_title, en_theme, en_chapters = _parse_doc(english_path)
    if translated_path is None:
        tr_title, tr_theme, tr_chapters = "", "", []
    else:
        tr_title, tr_theme, tr_chapters = _parse_doc(translated_path)

    warnings: list[str] = []
    rows: list[DialogueRow] = []

    if translated_path is not None and len(en_chapters) != len(tr_chapters):
        warnings.append(
            f"Chapter count mismatch: English has {len(en_chapters)}, "
            f"translated has {len(tr_chapters)}."
        )

    chapter_count = max(len(en_chapters), len(tr_chapters))
    sr_no = 1
    for i in range(chapter_count):
        en_ch = en_chapters[i] if i < len(en_chapters) else None
        tr_ch = tr_chapters[i] if i < len(tr_chapters) else None

        chapter_num = en_ch[0] if en_ch else (tr_ch[0] if tr_ch else i + 1)
        chapter_title = en_ch[1] if en_ch else (tr_ch[1] if tr_ch else "")

        en_lines = en_ch[2] if en_ch else []
        tr_lines = tr_ch[2] if tr_ch else []

        if translated_path is not None and len(en_lines) != len(tr_lines):
            warnings.append(
                f"Chapter {chapter_num} ('{chapter_title}') dialogue count mismatch: "
                f"English has {len(en_lines)}, translated has {len(tr_lines)}."
            )

        line_count = max(len(en_lines), len(tr_lines))
        for j in range(line_count):
            en_speaker, en_text = en_lines[j] if j < len(en_lines) else ("", "")
            _, tr_text = tr_lines[j] if j < len(tr_lines) else ("", "")
            rows.append(DialogueRow(
                sr_no=sr_no,
                chapter=chapter_num,
                chapter_title=chapter_title,
                speaker=en_speaker,
                english=en_text,
                translated=tr_text,
            ))
            sr_no += 1

    return {
        "title": en_title or tr_title,
        "theme": en_theme or tr_theme,
        "rows": [asdict(r) for r in rows],
        "warnings": warnings,
    }
