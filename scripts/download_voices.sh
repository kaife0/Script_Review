#!/usr/bin/env bash
# Download sherpa-onnx Piper voice packs referenced in config/voice_casting.json.
# Usage: scripts/download_voices.sh [language]   (default: italian)
set -euo pipefail

LANG_KEY="${1:-italian}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOICES_DIR="$ROOT_DIR/voices"
RELEASE_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"

mkdir -p "$VOICES_DIR"

PREFIX=$(python3 -c "
import json
cfg = json.load(open('$ROOT_DIR/config/voice_casting.json'))
print(cfg['$LANG_KEY']['release_prefix'])
")
VOICE_IDS=$(python3 -c "
import json
cfg = json.load(open('$ROOT_DIR/config/voice_casting.json'))
print(' '.join(v['id'] for v in cfg['$LANG_KEY']['voices']))
")

for voice_id in $VOICE_IDS; do
    archive="${PREFIX}${voice_id}.tar.bz2"
    dest_dir="$VOICES_DIR/${PREFIX}${voice_id}"
    if [ -d "$dest_dir" ]; then
        echo "Already downloaded: $dest_dir"
        continue
    fi
    echo "Downloading $archive ..."
    curl -L -o "$VOICES_DIR/$archive" "$RELEASE_URL/$archive"
    tar -xjf "$VOICES_DIR/$archive" -C "$VOICES_DIR"
    rm "$VOICES_DIR/$archive"
    echo "Extracted to $dest_dir"
done

echo "Done. Voice packs are in $VOICES_DIR"
