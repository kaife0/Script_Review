"""RQ queue setup. `rq worker` (see README) processes jobs enqueued here."""
import os

from redis import Redis
from rq import Queue

_redis: Redis | None = None
_queue: Queue | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    return _redis


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue("episodes", connection=get_redis())
    return _queue


def enqueue_pipeline(episode_id: str, english_path: str, translated_path: str) -> None:
    from core.pipeline import run_pipeline
    get_queue().enqueue(run_pipeline, episode_id, english_path, translated_path,
                         job_timeout="30m")
