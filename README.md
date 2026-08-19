# Audiobook Translation Review Platform

Upload an English master script and a translated script (.docx) for a kids'
audiobook episode. The app parses both, aligns dialogue line-by-line, runs a
batched Claude review pass, finds difficult words with suggested translations,
synthesizes narration audio for the translated lines via offline neural TTS,
and renders a results page you can edit inline or export as a standalone
HTML+audio bundle or an Excel workbook.

## Architecture

- **Flask** app (`app.py`) — language-first navigation: pick a target
  language, see its episodes, upload a new one.
- **MongoDB** — single `episodes` collection, chapters/rows embedded. No
  separate report files are generated during processing; the results page
  renders live from the database.
- **Background processing** — episode processing (parse → translate titles →
  review → difficult words → TTS) runs on a daemon thread per episode
  (`core/jobs.py`), not on the request thread, so uploads return immediately.
  No separate worker process or queue service required.
- **Anthropic (Claude)** — reviews each translated line, finds difficult
  words, translates titles. Swappable via `core/llm_client.py`'s
  `LLMClient` interface.
- **sherpa-onnx** — default offline TTS backend (Piper/VITS voices), run in
  parallel across rows via a thread pool. Pluggable via `TTS_BACKEND` for
  future Google Cloud TTS / Coqui XTTS backends.

## Prerequisites

- Python 3.10+
- MongoDB (local or a connection string in `MONGO_URI`, e.g. MongoDB Atlas)
- `ffmpeg` on PATH, built with the `rubberband` filter (for pitch/tempo voice
  variation). Check with `ffmpeg -filters | grep rubberband`.
- An Anthropic API key with billing enabled

## Setup

```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env      # fill in ANTHROPIC_API_KEY, MONGO_URI, etc.
```

### Download voice packs

Voice packs are large binary model files, so they aren't checked into the
repo — download them once per language:

```bash
scripts/download_voices.sh italian
```

This pulls the Piper voice models listed in `config/voice_casting.json` from
the [sherpa-onnx tts-models release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models)
into `voices/`. To add another language: add an entry to
`config/voice_casting.json` (release prefix + voice pool), mark it in
`config/languages.py`'s `SUPPORTED_LANGUAGES` with `"sherpa_onnx"` in
`voice_backends`, then run `scripts/download_voices.sh <language_key>`.

If no voice pack exists for a language, the app still runs the full text
review — it just skips the TTS stage and says so, rather than failing
silently mid-pipeline.

## Running

```bash
flask --app app run --debug
```

Then open `http://localhost:5000`. Uploads are processed in the background
automatically — no separate worker process needed.

## Pipeline stages

Each episode moves through
`uploaded → parsing → translating_titles → reviewing → finding_difficult_words → tts → done`
(or `failed`). The pipeline is idempotent per stage and per row: if a run
fails partway through (a transient API error, a bad TTS call), retrying
re-runs the same episode and skips whatever was already written to Mongo —
already-reviewed chapters and already-synthesized rows aren't redone. Retry
from the episode's progress page, or `POST /episode/<id>/retry`.

## Project layout

```
app.py                    # Flask routes
core/
  parsing.py              # docx -> aligned dialogue rows
  llm_client.py            # provider-agnostic LLM client (Anthropic)
  llm_review.py             # batched Claude review/difficult-words/title calls
  pipeline.py               # idempotent parse -> review -> tts stage runner
  jobs.py                   # background thread runner for the pipeline
  tts.py                    # pluggable TTSBackend interface (sherpa_onnx default)
  voice_registry.py         # language -> voice pool, voice pack path resolution
  db.py                     # MongoDB connection + episode CRUD
  storage.py                 # GCS / local-disk file storage abstraction
  report_excel.py            # xlsx export builder (on-demand, not in the pipeline)
  exports.py                 # on-demand HTML+audio zip and xlsx export helpers
config/
  languages.py               # SUPPORTED_LANGUAGES: code, name, TTS backend availability
  voice_casting.json          # per-language voice pool + casting config
scripts/
  download_voices.sh          # fetch voice packs from the sherpa-onnx release
templates/                    # Jinja2 templates (landing, dashboard, upload, episode results)
jobs/                         # per-episode uploaded docx + generated audio (gitignored, local fallback)
```

## Deployment

Deployed to Google Cloud Run (`gcloud run deploy --source .`). Set
`GCS_BUCKET` so uploads/audio persist in Cloud Storage instead of the
container's ephemeral filesystem. See `PROJECT_OVERVIEW.md` for a
plain-language summary of what's built and its current status.
