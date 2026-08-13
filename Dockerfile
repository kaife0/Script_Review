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

# Bake the Italian voice pack into the image so Cloud Run instances don't need
# to download it on cold start. Add more `download_voices.sh <language>` lines
# as more languages get sherpa_onnx voice pools in config/voice_casting.json.
RUN chmod +x scripts/download_voices.sh && scripts/download_voices.sh italian

ENV PORT=8080
EXPOSE 8080

CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 0 app:app
