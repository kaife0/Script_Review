"""Pluggable TTS backend interface. Default: sherpa-onnx (offline, free, Piper voices).

Backend selection via TTS_BACKEND env var: sherpa_onnx | google | xtts.
"""
import os
import subprocess
import tempfile
import threading

from core.voice_registry import resolve_voice_model

_sherpa_engine_cache: dict[str, object] = {}
_sherpa_engine_lock = threading.Lock()


class TTSBackend:
    """Common interface every TTS backend implements."""

    def synthesize(self, text: str, voice: dict, lang: str) -> bytes:
        """Return mp3 audio bytes for `text`, spoken with the given voice config."""
        raise NotImplementedError


def _apply_pitch_tempo(wav_path: str, out_path: str, pitch_semitones: float, tempo: float) -> None:
    """Re-encode wav_path -> out_path (mp3), applying pitch/tempo via ffmpeg's rubberband filter."""
    if pitch_semitones == 0 and tempo == 1.0:
        subprocess.run(["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", out_path],
                        check=True, capture_output=True)
        return
    pitch_scale = 2 ** (pitch_semitones / 12)
    filter_arg = f"rubberband=pitch={pitch_scale}:tempo={tempo}"
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-af", filter_arg, "-codec:a", "libmp3lame", out_path],
        check=True, capture_output=True,
    )


class SherpaOnnxBackend(TTSBackend):
    """Offline neural TTS via sherpa-onnx running Piper/VITS voice models. No cloud, no API key."""

    def _get_engine(self, lang: str, voice_id: str):
        import sherpa_onnx

        key = f"{lang}:{voice_id}"
        if key in _sherpa_engine_cache:
            return _sherpa_engine_cache[key]

        with _sherpa_engine_lock:
            if key in _sherpa_engine_cache:
                return _sherpa_engine_cache[key]
            paths = resolve_voice_model(lang, voice_id)
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=paths["model"],
                        tokens=paths["tokens"],
                        data_dir=paths["data_dir"],
                    ),
                    num_threads=1,
                    provider="cpu",
                ),
            )
            engine = sherpa_onnx.OfflineTts(config)
            _sherpa_engine_cache[key] = engine
            return engine

    def synthesize(self, text: str, voice: dict, lang: str) -> bytes:
        import soundfile as sf

        engine = self._get_engine(lang, voice["id"])
        audio = engine.generate(text=text, sid=0, speed=1.0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_wav = os.path.join(tmp_dir, "raw.wav")
            final_mp3 = os.path.join(tmp_dir, "final.mp3")
            sf.write(raw_wav, audio.samples, audio.sample_rate)
            _apply_pitch_tempo(raw_wav, final_mp3, voice["pitch_semitones"], voice["tempo"])
            with open(final_mp3, "rb") as f:
                return f.read()


class GoogleCloudTTSBackend(TTSBackend):
    """Stub. Requires GOOGLE_APPLICATION_CREDENTIALS to enable."""

    def synthesize(self, text: str, voice: dict, lang: str) -> bytes:
        raise NotImplementedError(
            "GoogleCloudTTSBackend is not configured. Set GOOGLE_APPLICATION_CREDENTIALS "
            "and implement core.tts.GoogleCloudTTSBackend.synthesize to enable this backend."
        )


class XTTSBackend(TTSBackend):
    """Stub for Coqui XTTS-v2. Requires a local model path to enable."""

    def synthesize(self, text: str, voice: dict, lang: str) -> bytes:
        raise NotImplementedError(
            "XTTSBackend is not configured. Set XTTS_MODEL_PATH and implement "
            "core.tts.XTTSBackend.synthesize to enable this backend."
        )


_BACKENDS = {
    "sherpa_onnx": SherpaOnnxBackend,
    "google": GoogleCloudTTSBackend,
    "xtts": XTTSBackend,
}

_backend_instance: TTSBackend | None = None


def get_backend() -> TTSBackend:
    global _backend_instance
    if _backend_instance is None:
        name = os.environ.get("TTS_BACKEND", "sherpa_onnx")
        cls = _BACKENDS.get(name)
        if cls is None:
            raise ValueError(f"Unknown TTS_BACKEND '{name}'. Valid options: {', '.join(_BACKENDS)}")
        _backend_instance = cls()
    return _backend_instance
