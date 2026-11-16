"""File storage for uploaded scripts and generated audio.

Cloud Run's filesystem is ephemeral, so uploads/audio live in a GCS bucket
(GCS_BUCKET env var) instead of local disk. Falls back to a local directory
when GCS_BUCKET is unset, so local development needs no GCP credentials.
"""
import glob
import os
import time

_BUCKET_NAME = os.environ.get("GCS_BUCKET")
_LOCAL_ROOT = os.environ.get("AUDIO_ROOT", os.path.join(os.path.dirname(__file__), "..", "jobs"))

_bucket = None


def _key(episode_id: str, relative_path: str = "") -> str:
    parts = relative_path.split("/") if relative_path else []
    bad = any(c in episode_id + relative_path for c in "\\:\0")
    if bad or "/" in episode_id or any(p in ("", ".", "..") for p in [episode_id, *parts]):
        raise FileNotFoundError(relative_path)
    return "/".join([episode_id, *parts])


def _local(episode_id: str, relative_path: str = "") -> str:
    root = os.path.realpath(_LOCAL_ROOT)
    full = os.path.realpath(os.path.join(root, *_key(episode_id, relative_path).split("/")))
    if os.path.commonpath([root, full]) != root or full == root:
        raise FileNotFoundError(relative_path)
    return full


def _bucket_client():
    global _bucket
    if _bucket is None:
        from google.cloud import storage
        _bucket = storage.Client().bucket(_BUCKET_NAME)
    return _bucket


def using_gcs() -> bool:
    return bool(_BUCKET_NAME)


def save_bytes(episode_id: str, relative_path: str, data: bytes) -> None:
    """Store a file under this episode's namespace (e.g. 'uploads/english.docx', 'audio/row_1.mp3')."""
    if using_gcs():
        blob = _bucket_client().blob(_key(episode_id, relative_path))
        blob.upload_from_string(data)
        return
    full_path = _local(episode_id, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(data)


def read_bytes(episode_id: str, relative_path: str) -> bytes:
    if using_gcs():
        blob = _bucket_client().blob(_key(episode_id, relative_path))
        return blob.download_as_bytes()
    full_path = _local(episode_id, relative_path)
    with open(full_path, "rb") as f:
        return f.read()


def size(episode_id: str, relative_path: str) -> int:
    if using_gcs():
        blob = _bucket_client().get_blob(_key(episode_id, relative_path))
        if blob is None:
            raise FileNotFoundError(relative_path)
        return blob.size
    return os.path.getsize(_local(episode_id, relative_path))


def read_range(episode_id: str, relative_path: str, start: int, end: int) -> bytes:
    if using_gcs():
        return _bucket_client().blob(_key(episode_id, relative_path)).download_as_bytes(start=start, end=end)
    with open(_local(episode_id, relative_path), "rb") as f:
        f.seek(start)
        return f.read(end - start + 1)


def delete_stale(pattern: str, max_age_seconds: int) -> int:
    cutoff = time.time() - max_age_seconds
    if using_gcs():
        stale = [b for b in _bucket_client().list_blobs(match_glob=f"*/{pattern}") if b.updated.timestamp() < cutoff]
        for blob in stale:
            blob.delete()
        return len(stale)
    stale = [p for p in glob.glob(os.path.join(_LOCAL_ROOT, "*", pattern), recursive=True)
             if os.path.isfile(p) and os.path.getmtime(p) < cutoff]
    for path in stale:
        os.remove(path)
    return len(stale)


def exists(episode_id: str, relative_path: str) -> bool:
    if using_gcs():
        blob = _bucket_client().blob(_key(episode_id, relative_path))
        return blob.exists()
    full_path = _local(episode_id, relative_path)
    return os.path.isfile(full_path)


def list_files(episode_id: str, prefix: str) -> list[str]:
    """List filenames (not full paths) under episode_id/prefix/."""
    key_prefix = _key(episode_id, prefix) + "/"
    if using_gcs():
        blobs = _bucket_client().list_blobs(prefix=key_prefix)
        return [b.name[len(key_prefix):] for b in blobs]
    dir_path = _local(episode_id, prefix)
    if not os.path.isdir(dir_path):
        return []
    return os.listdir(dir_path)


def delete_file(episode_id: str, relative_path: str) -> None:
    """Remove a single file (e.g. a reviewer-audio recording) without touching the rest of
    the episode's files. Idempotent -- silently no-ops if the file doesn't exist."""
    if using_gcs():
        blob = _bucket_client().blob(_key(episode_id, relative_path))
        try:
            blob.delete()
        except Exception:
            pass
        return
    full_path = _local(episode_id, relative_path)
    if os.path.isfile(full_path):
        os.remove(full_path)


def delete_episode_files(episode_id: str) -> None:
    """Remove every uploaded/generated file for this episode."""
    if using_gcs():
        for blob in _bucket_client().list_blobs(prefix=_key(episode_id) + "/"):
            blob.delete()
        return
    import shutil
    dir_path = _local(episode_id)
    if os.path.isdir(dir_path):
        shutil.rmtree(dir_path)
