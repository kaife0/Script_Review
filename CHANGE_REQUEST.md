# Review Page — Upcoming Changes

This document describes a set of improvements planned for the script review page. 
---

## 1. Show the translated title and chapter names

**Right now:** the episode title and each chapter's name are only shown in English, even when reviewing a translated episode.

**Change:** show both — the original English title/chapter name and the translated version next to it, so the reviewer can check that these were translated correctly too, the same way individual dialogue lines are checked.

**Also:** since we already generate spoken audio for dialogue lines, we'll do the same for the translated title and each chapter title — so there's an audio clip for those too, not just the dialogue.

---

## 2. Make the legend "sticky"

**Right now:** at the top of the page there's a small legend explaining the color coding (plain line, flagged line, narrator line). If you scroll down through a long episode, it scrolls out of view.

**Change:** keep the legend fixed at the top of the screen while scrolling, so the color meanings are always visible, no matter how far down the page you are.

---

## 3. Split each line into more sections (the main change)

**Right now:** each line of dialogue has two text boxes side by side — English and the AI's translation — plus a single comment box.

**Change:** each line will have these sections instead:

| Section | What it shows | Can it be edited? | Comments? |
|---|---|---|---|
| **English** | The original English line | No — locked | Yes, can leave comments on it |
| **AI Translated** | The AI's translation, as originally generated | No — locked, kept as a reference/record | Yes, can leave comments on it |
| **Reviewer's Edit** | Starts as a copy of the AI translation | **Yes** — the reviewer can rewrite it, with undo/redo, plus a "complete" checkmark | Yes, can leave comments on it |
| **Audio** | The generated narration for the line | No | Yes, a separate comment box just for feedback on how the audio sounds |

The idea: the AI's original suggestion is never lost or overwritten — it stays visible as a reference. The reviewer works in their own separate box, so it's always clear what the AI proposed versus what a human actually approved. Comments can be left on any of the text sections, similar to how commenting works in Google Docs or Confluence — attached to a specific piece of text, not just a general note for the whole line.

---

## 4. Difficult words helper

**New addition:** for lines that contain tricky or less common words, the AI will pick out those words and suggest 2–3 possible translations for them, each with a plain English meaning attached — so the reviewer can quickly see the options and pick the one that fits best.

This is a reference tool to help the reviewer decide, not an automatic change — it doesn't edit the text for you. The final wording is still up to the reviewer, made in the Reviewer's Edit box described above.

---

## Summary of what's changing

- Titles and chapter names get translated versions shown, with audio.
- The legend stays visible while scrolling.
- Each line goes from 2 boxes to 4: English (locked), AI Translation (locked), Reviewer's Edit (editable, with undo/redo and a completion mark), and Audio — each with its own comments.
- A new difficult-words helper suggests translation options with meanings, for the reviewer's reference.

Everything the reviewer's note/checklist system already does (flagging lines, marking them done, tracking overall progress) stays the same — these changes add more detail and control within each line, they don't replace what's already there.
