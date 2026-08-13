# Audiobook Translation Review Platform

Upload an English master script and a translated script (.docx) for a kids'
audiobook episode. The app parses both, aligns dialogue line-by-line, runs a
batched Claude review pass, synthesizes narration audio for the translated
lines via offline neural TTS, and renders a results page you can edit inline
or export as a standalone HTML+audio bundle or an Excel workbook.

## Architecture

- **Flask** app (`app.py`) — language-first navigation: pick a target
  language, see its episodes, upload a new one.
- **MongoDB** — single `episodes` collection, chapters/rows embedded. No
  separate report files are generated during processing; the results page
  renders live from the database.
- **RQ + Redis** — episode processing (parse → LLM review → TTS) runs in a
  background worker, not on the request thread.
- **sherpa-onnx** — default offline TTS backend (Piper/VITS voices). Pluggable
  via `TTS_BACKEND` for future Google Cloud TTS / Coqui XTTS backends.

## Prerequisites

- Python 3.10+
- MongoDB running locally (or a connection string in `MONGO_URI`)
- Redis running locally (or a connection string in `REDIS_URL`)
- `ffmpeg` on PATH, built with the `rubberband` filter (for pitch/tempo voice
  variation). Check with `ffmpeg -filters | grep rubberband`.
- An Anthropic API key

## Setup

```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env      # fill in ANTHROPIC_API_KEY, Mongo/Redis URLs as needed
```

### Download voice packs

Only Italian voices are bundled by default. Voice packs are large binary
model files, so they aren't checked into the repo — download them once:

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

Two processes, both needed:

```bash
# Terminal 1 — the web app
flask --app app run --debug

# Terminal 2 — the background worker that processes uploaded episodes
rq worker episodes --url $REDIS_URL
```

Then open `http://localhost:5000`.

## Pipeline stages

Each episode moves through `uploaded → parsing → reviewing → tts → done`
(or `failed`). The pipeline is idempotent per stage and per row: if a job
fails partway through (a transient API error, a bad TTS call), retrying
re-enqueues the same episode and skips whatever was already written to
Mongo — already-reviewed chapters and already-synthesized rows aren't
redone. Retry from the episode's progress page, or `POST
/episode/<id>/retry`.

## Project layout

```
app.py                  # Flask routes
core/
  parsing.py            # docx -> aligned dialogue rows
  llm_review.py          # batched Claude review calls, retry + per-row fallback
  pipeline.py            # idempotent parse -> review -> tts stage runner
  tts.py                 # pluggable TTSBackend interface (sherpa_onnx default)
  voice_registry.py      # language -> voice pool, voice pack path resolution
  db.py                   # MongoDB connection + episode CRUD
  queue.py               # RQ queue setup
  report_excel.py         # xlsx export builder (on-demand, not in the pipeline)
  exports.py              # on-demand HTML+audio zip and xlsx export helpers
config/
  languages.py            # SUPPORTED_LANGUAGES: code, name, TTS backend availability
  voice_casting.json       # per-language voice pool + casting config
scripts/
  download_voices.sh       # fetch voice packs from the sherpa-onnx release
templates/                 # Jinja2 templates (landing, dashboard, upload, episode results)
jobs/                      # per-episode uploaded docx + generated audio (gitignored)
```

## Notes on scale

The current design runs the RQ worker as a single process — fine for one
reviewer's workload. For higher throughput, run multiple `rq worker`
processes against the same Redis queue; RQ handles concurrent workers without
code changes since each job (one episode) is self-contained.
