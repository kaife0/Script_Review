"""Runs the episode pipeline off the request thread so uploads return immediately.

A plain background thread, not a queue; MongoDB already gives us the incremental, resumable
state the pipeline needs. Because a Cloud Run instance can be OOM-killed or reclaimed mid-job
(silently killing this thread), "is a pipeline running" is a heartbeat lease stored in MongoDB
(see db.claim_pipeline_lease) rather than in-process state: it is correct across gunicorn
workers and instances, and a dead holder's lease simply expires. app.py uses that to detect a
stalled episode and resume it (the pipeline is idempotent and skips finished rows).
"""
import logging
import os
import threading
import uuid

from core import db
from core.pipeline import repair_missing_audio, run_pipeline

logger = logging.getLogger(__name__)

_HEARTBEAT_SECONDS = db.PIPELINE_LEASE_SECONDS / 3


def start_pipeline(episode_id: str) -> bool:
    """Start the pipeline in the background unless it is already running. Returns True if started."""
    return _start(episode_id, run_pipeline)


def start_audio_repair(episode_id: str) -> bool:
    """Regenerate only the missing audio of a finished episode, in the background."""
    return _start(episode_id, repair_missing_audio)


def _start(episode_id: str, target) -> bool:
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    if not db.claim_pipeline_lease(episode_id, owner):
        return False

    stop = threading.Event()

    def _heartbeat():
        while not stop.wait(_HEARTBEAT_SECONDS):
            try:
                db.claim_pipeline_lease(episode_id, owner)
            except Exception:
                logger.exception("episode %s: lease heartbeat failed", episode_id)

    def _run():
        threading.Thread(target=_heartbeat, daemon=True).start()
        try:
            target(episode_id)
        except Exception:
            pass  # status/error_message already recorded on the episode doc
        finally:
            stop.set()
            db.release_pipeline_lease(episode_id, owner)

    threading.Thread(target=_run, daemon=True).start()
    return True


def is_running(episode_id: str) -> bool:
    return db.pipeline_lease_active(episode_id)
