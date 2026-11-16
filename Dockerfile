FROM python:3.11-slim

# ffmpeg (built with librubberband on Debian) for pitch/tempo voice variation;
# curl/bzip2 only needed at build time to fetch voice packs, not kept in the final layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl bzip2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake voice packs into the image so Cloud Run instances don't need to
# download them on cold start. Add a line here for each language added to
# config/voice_casting.json.
RUN chmod +x scripts/download_voices.sh \
    && scripts/download_voices.sh italian \
    && scripts/download_voices.sh german \
    && scripts/download_voices.sh spanish \
    && scripts/download_voices.sh french

RUN python scripts/tts_smoke_test.py

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/jobs && chown appuser:appuser /app/jobs
USER appuser

ENV PORT=8080
EXPOSE 8080

# One worker: the pipeline thread registry (core/jobs.py) and the TTS engine cache are per-process,
# so a second worker allowed duplicate pipeline runs and doubled model memory (OOM kills on 2GiB).
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 0 app:app
