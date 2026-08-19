# Audiobook Translation Review Platform

A web app for reviewing translated kids' audiobook scripts: upload an English master script and a translated version, get an AI-assisted line-by-line review, and get auto-generated narration audio, one distinct voice per character.

**Live URL:** https://script-review-892635735954.us-central1.run.app

---

## What it does

1. Reviewer picks a target language (Italian, German, Spanish, or French) and uploads two `.docx` files — the English master script and the translated script.
2. The app reads both and lines up the dialogue, chapter by chapter, line by line.
3. An AI model reviews each line and flags it `ok` or `note` with a short comment explaining why.
4. Each character in the script gets a distinct computer-generated voice reading their translated lines.
5. The reviewer gets a results page: English and translated text side by side, the AI's comment, an audio clip per line, and the ability to edit comments, change flags, and check off lines as reviewed — with a progress bar showing how much of the episode is done.
6. Results can be downloaded at any time as a standalone webpage (with audio included) or as an Excel file.

---

## What it's built with

- **Flask** — the web application itself (Python).
- **MongoDB** — the database. Every episode (its chapters, lines, comments, flags, review status) is stored as one document.
- **Google Cloud Storage** — where uploaded scripts and generated audio clips are kept.
- **Anthropic (Claude)** — the AI model that reviews each translated line.
- **sherpa-onnx** — free, offline text-to-speech software that turns translated text into spoken audio, no per-use cost.
- **Google Cloud Run** — where the whole app is hosted. It only costs money while it's actually being used; it goes idle (and free) when nobody's using it.

---

## How it works, step by step

**1. You open the site.** It loads instantly — Cloud Run starts it up on demand.

**2. You upload two scripts.** They're saved to cloud storage right away.

**3. The app processes the episode in the background**, so the page responds instantly instead of making you wait and stare at a frozen screen:
   - Reads both documents and matches up each line of dialogue between English and the translation.
   - Translates the episode title and each chapter title, with its own narrated audio.
   - Sends the lines to the AI for review, chapter by chapter, and gets back a comment and a flag for each one.
   - Finds difficult or tricky words per line and suggests a couple of translation options with plain-English meanings, for the reviewer's reference.
   - Generates spoken audio for every translated line, in parallel, using a different voice for each character so it doesn't sound like one narrator reading everyone's parts.

**4. Everything is saved as it happens**, not just at the end. This means if something goes wrong partway through, restarting the job doesn't start over from scratch — it only redoes the part that failed.

**5. You review the results.** Every line shows the original English, the translation, the AI's comment, and a play button for the audio. You can correct the AI's comment, change its flag, and tick a "done" box per line as you personally verify it — a running progress bar tracks how much of the episode you've gotten through.

**6. You can export anytime** — either a self-contained webpage with all the audio bundled in (works without internet, good for sharing), or an Excel spreadsheet with the same information.

---

## Current status

**Working:** the website, uploading (processed in the background so the page never freezes), the review page with per-line editing and undo/redo, comments, the progress tracker, exports, and voice generation are all built and confirmed working for Italian, with the same setup in place for German, Spanish, and French.

**Not turned on yet:** the AI review step is fully wired up to the real Claude API, but the account's billing hasn't been set up yet, so live AI calls fail with a billing error rather than a code error. Once billing is added, no further changes are needed — reviewing, difficult-words, and title translation will all start working immediately.
