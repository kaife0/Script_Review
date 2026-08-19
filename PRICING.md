# AI Cost Per Episode

Based on a real sample episode: 5 chapters, 71 lines of dialogue.

## How pricing works

Claude charges per word processed — no subscription, no free tier. You pay for two things: the script text sent **to** the AI (input), and the AI's response written back (output, which costs more per word). For each episode, the app makes three types of AI requests per chapter: reviewing every line, finding difficult words, and translating titles.

## Cost per episode, 1 language

| Model | Normal run | Worst case (retries) |
|---|---|---|
| **Claude Sonnet 5** | ~$0.11 (~$0.07 at intro pricing, through Aug 2026) | ~$0.25 |
| **Claude Opus 5** | ~$0.18 | ~$0.42 |

The "worst case" only happens if the AI's response comes back malformed — the app then retries once, and if it still fails, falls back to reviewing each line individually. This is a safety net for reliability, not something that happens on every run.

## Cost per episode, 4 languages

| Model | Normal run |
|---|---|
| **Claude Sonnet 5** | ~$0.44 |
| **Claude Opus 5** | ~$0.72 |

Every additional language is a separate full run through the AI, since it's different translated text each time. Cost scales directly with number of languages — multiply the 1-language cost by however many languages that episode is translated into.

## Totals at volume

| Episodes | Model | 1 language | 4 languages |
|---|---|---|---|
| 10 | Sonnet 5 | ~$1.10 | ~$4.40 |
| 10 | Opus 5 | ~$1.80 | ~$7.20 |
| 25 | Sonnet 5 | ~$2.75 | ~$11.00 |
| 25 | Opus 5 | ~$4.50 | ~$18.00 |
