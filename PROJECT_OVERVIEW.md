# Audiobook Translation Review Platform

A web app for reviewing translated kids' audiobook scripts: upload an English master script and a translated version, get an AI-assisted line-by-line review, and (for supported languages) auto-generated narration audio per character.

**Live URL:** https://script-review-892635735954.us-central1.run.app

---

## What it does

1. Reviewer picks a target language and uploads two `.docx` files — the English master script and the translated script.
2. The app parses both, lining up dialogue chapter-by-chapter, row-by-row.
3. Claude (Anthropic's LLM) reviews each line and flags it `ok` or `note` with a short comment.
4. For languages with voice packs configured, each character gets a distinct synthesized voice (offline TTS, no per-request cloud cost) reading their translated lines.
5. Reviewer sees a results page with English/translated side by side, the AI comment, the audio clip, and can edit comments, flip flags, and mark lines as human-verified — with a live progress bar showing how much of the episode has been checked.
6. Results can be exported as a standalone HTML+audio bundle or an Excel workbook at any time.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Flask (Python) | Simple, direct control over routes and rendering |
| Database | MongoDB Atlas | One document per episode, chapters/rows embedded — no joins needed for this shape of data |
| File storage | Google Cloud Storage | Uploaded scripts and generated audio; Cloud Run's own disk is wiped on every restart |
| LLM review | Anthropic Claude API | Batched per-chapter calls, JSON-validated, retries then falls back to per-row calls if the model's output doesn't match |
| Text-to-speech | sherpa-onnx (offline, Piper/VITS voices) | No cloud TTS bill, no API key required; pitch/tempo varied per character via ffmpeg |
| Hosting | Google Cloud Run | Pay-per-use container hosting, scales to zero when idle |
| Build | Cloud Build (via `gcloud run deploy --source`) | Builds the Docker image from source automatically, no local Docker needed |

---

## Architecture: how a request actually flows

```
Browser
  │
  ▼
Cloud Run (Flask app, containerized)
  │
  ├──► MongoDB Atlas        (episode data: chapters, rows, review comments, flags, verification state)
  ├──► Google Cloud Storage (uploaded .docx files, generated .mp3 audio clips)
  └──► Anthropic API        (LLM review calls)
```

Everything runs as **one Cloud Run service** — no separate background worker, no message queue. When a script is uploaded, the whole pipeline (parse → review → narrate) runs synchronously inside that single request. This was a deliberate simplification for this app's scale (single reviewer, personal use): it avoids running and paying for an always-on worker process, at the cost of the browser waiting while a script processes.

---

## The processing pipeline, step by step

1. **Parse** — the two `.docx` files are downloaded from GCS, read with `python-docx`, and split into chapters and `"Speaker: line"` dialogue rows. English and translated rows are aligned by chapter and position; mismatches are flagged as warnings, not hard failures.
2. **Review** — each chapter's rows are sent to Claude in a single batched call, asking for a JSON array of `{comment, flag}` per row. If the response doesn't parse or its length doesn't match, it retries once, then falls back to reviewing each row individually — so a single bad response never silently drops feedback.
3. **Narrate** *(only for languages with a configured voice pack — currently Italian)* — each character in the episode is assigned a distinct voice from a small pool (narrator gets a fixed voice; others round-robin), synthesized offline with sherpa-onnx, with slight pitch/tempo shifts per voice so characters sound distinct. Clips are uploaded to GCS as they're made.

Every stage writes its results into MongoDB as it completes — not all at once at the end. That matters for retries: if something fails partway (say, one TTS call errors), retrying doesn't start over. It re-checks what's already saved and only redoes the missing pieces.

---

## The review page

- Side-by-side English and translated text per line, color-coded (flagged lines and narrator lines stand out).
- Inline editing: change the AI's comment or flip its `ok`/`note` flag directly on the page, saved instantly.
- **Human verification tracking**: each line has a "Mark done" toggle, separate from the AI's flag — this is the reviewer's own checklist. A progress bar at the top shows live completion (e.g. "14 / 20 lines marked done") as you work through the episode.
- Audio player inline per line, for languages with narration.
- Export buttons for a standalone HTML+audio zip (works offline, no server needed) or an Excel report.

---

## What's deployed vs. what's still manual

**Working now:** hosting, database, file storage, parsing, the review page UI, exports, human verification tracking.

**Not yet wired up:** the Anthropic API key (LLM review step) — intentionally held back pending a model choice, so uploads currently stop before the review stage. TTS is Italian-only for now, since that's the only language with voice models baked into the deployed image; other languages get a text-only review until voice packs are added for them.
