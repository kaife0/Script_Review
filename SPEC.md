# Audiobook Translation Review Platform — Build Spec

Living spec. Instructions are cumulative — later sections amend/override earlier ones where they conflict. Update this file whenever the user changes direction so a fresh session can pick up context without re-deriving it from chat history.

## Original scope (v1)

Flask app. Reviewer uploads English master .docx + translated .docx for a kids'
audiobook episode. Pipeline: parse both docx -> align dialogue rows by
chapter/index -> batched Claude review pass (comment + ok/note flag per row,
batched per chapter, retry once on row-count mismatch then fall back to
per-row calls) -> offline neural TTS per translated line via sherpa-onnx
(Piper/VITS voices, distinct voice per character via config-driven casting
pool, pitch/tempo variation via ffmpeg `rubberband` filter) -> results page.

Source docx paragraph format:
```
Title: <title>
Theme: <theme>
Chapter N: <chapter title>
[
"Speaker: (optional stage direction) dialogue text",
...
]
```
Parse with python-docx only (no pandoc). Split each quoted line on the FIRST
`": "` into (speaker, text). Track chapter number/title from heading lines.
Mismatched chapter/row counts between the two docs -> alignment warning,
still align best-effort by index.

Non-negotiables (still apply):
- No embedded base64 audio anywhere (HTML or exports) — linked relative paths
  or a serving route only.
- Validate batched LLM JSON row count against chapter row count before
  accepting; retry once; fall back to per-row calls for that chapter if still
  mismatched.
- Voice casting and target-language voice packs are config-driven
  (`config/voice_casting.json`), not hardcoded.
- If no voice pack exists for the requested target language, fail clearly
  rather than silently.

## v2 changes — MongoDB, language-first nav, RQ, pluggable TTS

Supersedes the original Flask-only, file-based, synchronous-pipeline design.

### Persistence: MongoDB
Single `episodes` collection, chapters + rows embedded (no separate
collections, no cross-episode joins). Schema:
```js
{
  _id, title, source_lang: "en", target_lang, target_lang_name,
  status: "uploaded"|"parsing"|"reviewing"|"tts"|"done"|"failed",
  error_message, created_at, updated_at,
  chapters: [{ chapter_number, title, rows: [{
    sr_no, speaker, english, translated,
    review_comment, review_flag: "ok"|"note"|null,
    audio_path, audio_status: "pending"|"done"|"failed"
  }]}]
}
```
Index on `target_lang` and `status`. No auth, no `users` collection, no
owner-scoping — single shared instance. Keep `/episode/<id>` URL shape
owner-agnostic so auth can be bolted on later without a URL reshape.

### Routes — language-first navigation
- `GET /` — landing grid of target languages with >=1 episode
  (`db.episodes.distinct("target_lang")`), each a card; plus "add a new
  language" (= upload in an unseen language). Drive the language list/dropdown
  from a `SUPPORTED_LANGUAGES` config (code, name, which TTS backend(s) have
  voices for it) — not derived only from Mongo — so upload can show "TTS
  available" vs "text-only, no audio yet" honestly.
- `GET /lang/<lang_code>` — dashboard: episodes for that language, newest
  first, title/status/chapter+row count, link to result if done else a
  status/progress link. "Create New" button pre-fills target language.
- `GET /lang/<lang_code>/new` — upload form, target language locked from URL.
- `POST /lang/<lang_code>/new` — creates episode doc (`status: "uploaded"`),
  enqueues one RQ job, redirects to progress page.
- `GET /episode/<episode_id>` — branches on status: progress view if not
  done, else renders results dynamically (see below).
- `GET /episode/<episode_id>/status` — JSON for polling.

### Background jobs: RQ + Redis
`POST /lang/<lang_code>/new` enqueues one job per episode. Worker runs
parse -> LLM review -> TTS, updating `episode.status` and per-row
`audio_status` as each stage completes (so the progress page reflects real
state, not just "processing"). Document `rq worker` alongside `flask run` in
README.

### Results rendering: dynamic, not a generated file
- `GET /episode/<episode_id>` renders `templates/episode.html` directly from
  the Mongo doc via Jinja2 when done — same visual design as before (chapter
  sections; white/yellow/blue row coding for straightforward/note/narrator;
  English+translated side by side; comment; inline `<audio>` per row).
  `report_html.py`'s static-build role is retired from the pipeline.
- Audio `src` = `/episode/<episode_id>/audio/<filename>`, a `send_from_directory`
  route reading that episode's on-disk mp3 dir — kept behind one function so
  swapping to signed S3/CDN URLs later is a one-function change.
- Pipeline (RQ job) no longer generates any report file. It only writes
  parse/review/TTS results into the Mongo doc.

### Exports: on-demand, not pipeline steps
- `GET /episode/<episode_id>/export/html` — render the same Jinja template to
  a string, zip with the episode's audio folder, return as download. This is
  where the old always-embedded `report_html.py` logic now lives — invoked
  only here.
- `GET /episode/<episode_id>/export/xlsx` — call `report_excel.py` on demand,
  stream back, don't need to persist after (temp file is fine).

### In-place editing
On the results page: inline-edit `review_comment` (textarea + save) via
`POST /episode/<episode_id>/row/<sr_no>`; toggle `review_flag` ok/note from a
dropdown/button. Updates just that row in the Mongo doc. No history/versioning.

### TTS: pluggable backend interface, sherpa-onnx stays default
`core/tts.py` built around a small interface:
```python
class TTSBackend:
    def synthesize(self, text: str, speaker: str, lang: str) -> bytes: ...  # mp3 bytes
```
- `SherpaOnnxBackend` — existing default, offline, Piper voices per language
  via `config/voice_casting.json`. This is what actually runs unless
  configured otherwise.
- `GoogleCloudTTSBackend`, `XTTSBackend` — stubs only, interface + a clear
  `NotImplementedError("configure GOOGLE_APPLICATION_CREDENTIALS / XTTS model
  path to enable this backend")`. Not wired up.
- Backend selection via `TTS_BACKEND=sherpa_onnx|google|xtts` env var — a
  config change, not a rewrite.
- `SUPPORTED_LANGUAGES` tracks per-language backend/voice availability so the
  UI can be honest about what's available instead of failing mid-pipeline.

### Build order (as instructed)
1. Mongo layer + models
2. Language-first routes/templates
3. RQ wiring
4. TTS backend interface refactor (lowest urgency — sherpa-onnx path already
   works, just needs the interface wrapper)

### Non-negotiables carried forward
- No embedded base64 audio anywhere, ever — linked/served paths only.
- Batched LLM JSON row-count validation -> retry once -> per-row fallback,
  unchanged.
- No auth/user scoping now, but don't paint into a corner for later.

### Job retry: skip-completed, not re-upload (decided)
Because parsed rows, review comments/flags, and per-row `audio_status` are
already persisted to Mongo incrementally as the pipeline runs, a failed job
is retried by re-enqueuing the *same* episode_id — not by re-uploading docx
files. The pipeline function is idempotent per stage/row:
- If `episode.chapters` is empty -> parse and save.
- Per chapter, if any row lacks `review_comment` -> re-run the LLM review
  batch for that chapter (whole chapter, since batching is per-chapter
  anyway); chapters fully reviewed are skipped.
- Per row, if `audio_status != "done"` -> synthesize; rows already `"done"`
  are skipped.
- On any exception, catch at the stage boundary, set `episode.status =
  "failed"` and `episode.error_message`, but leave whatever was already
  written (parsed rows, partial reviews, partial audio) in place so retry has
  something to skip.
A "Retry" action on the episode/progress page re-enqueues the same
`episode_id` (not a new upload). This is intentionally cheap — it reuses the
incremental-write behavior the v2 schema already requires, not new
bookkeeping.
