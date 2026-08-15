"""Runs the episode pipeline off the request thread so uploads return immediately.

A plain background thread, not a queue: this app runs as a single process, and
MongoDB already gives us the incremental, resumable state the pipeline needs.
Cloud Run keeps the instance alive via min-instances=1, so the thread isn't
killed mid-job when the request that started it finishes.
"""
import threading

from core.pipeline import run_pipeline

_running: set[str] = set()
_lock = threading.Lock()


def start_pipeline(episode_id: str) -> None:
    with _lock:
        if episode_id in _running:
            return
        _running.add(episode_id)

    def _run():
        try:
            run_pipeline(episode_id)
        except Exception:
            pass  # status/error_message already recorded on the episode doc
        finally:
            with _lock:
                _running.discard(episode_id)

    threading.Thread(target=_run, daemon=True).start()


def is_running(episode_id: str) -> bool:
    with _lock:
        return episode_id in _running
