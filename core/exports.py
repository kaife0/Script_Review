"""On-demand export builders: standalone HTML+audio zip, and xlsx. Not run during the pipeline."""
import io
import zipfile

from core import storage
from core.report_excel import build_excel_report


def build_html_export_zip(episode_id: str, rendered_html: str) -> io.BytesIO:
    """Zip the rendered results HTML together with the episode's audio folder (relative links intact)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", rendered_html)
        for filename in storage.list_files(episode_id, "audio"):
            zf.writestr(f"audio/{filename}", storage.read_bytes(episode_id, f"audio/{filename}"))
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
