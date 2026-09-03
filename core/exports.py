"""On-demand export builders: xlsx and docx. Not run during the pipeline."""
import io

from core.report_excel import build_excel_report
from core.report_docx import build_docx_report


def build_docx_export(episode: dict) -> io.BytesIO:
    """Build the reviewed-script docx (same format as the input script) on demand."""
    doc = build_docx_report(episode)
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def build_xlsx_export(episode: dict) -> io.BytesIO:
    """Build the xlsx report on demand and return it as an in-memory buffer."""
    parsed = {
        "title": episode["title"],
        "theme": episode.get("theme", ""),
        "rows": [
            {**row, "chapter": chapter["chapter_number"], "chapter_title": chapter["title"]}
            for chapter in episode["chapters"]
            for row in chapter["rows"]
        ],
        "warnings": episode.get("alignment_warnings", []),
    }
    reviews = [{"comment": r["review_comment"] or "", "flag": r["review_flag"] or "ok"} for r in parsed["rows"]]
    audio_paths = {r["sr_no"]: r["audio_path"] for r in parsed["rows"] if r["audio_path"]}

    buffer = io.BytesIO()
    tmp_path = buffer  # openpyxl needs a path-like or file object; BytesIO works with save()
    build_excel_report(parsed, reviews, audio_paths, episode["target_lang_name"], tmp_path)
    buffer.seek(0)
    return buffer
