"""File storage for uploaded scripts and generated audio.

Cloud Run's filesystem is ephemeral, so uploads/audio live in a GCS bucket
(GCS_BUCKET env var) instead of local disk. Falls back to a local directory
when GCS_BUCKET is unset, so local development needs no GCP credentials.
"""
import os

_BUCKET_NAME = os.environ.get("GCS_BUCKET")
_LOCAL_ROOT = os.environ.get("AUDIO_ROOT", os.path.join(os.path.dirname(__file__), "..", "jobs"))

_bucket = None


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
        blob = _bucket_client().blob(f"{episode_id}/{relative_path}")
        blob.upload_from_string(data)
        return
    full_path = os.path.join(_LOCAL_ROOT, episode_id, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(data)


def read_bytes(episode_id: str, relative_path: str) -> bytes:
    if using_gcs():
        blob = _bucket_client().blob(f"{episode_id}/{relative_path}")
        return blob.download_as_bytes()
    full_path = os.path.join(_LOCAL_ROOT, episode_id, relative_path)
    with open(full_path, "rb") as f:
        return f.read()


def list_files(episode_id: str, prefix: str) -> list[str]:
    """List filenames (not full paths) under episode_id/prefix/."""
    key_prefix = f"{episode_id}/{prefix}/"
    if using_gcs():
        blobs = _bucket_client().client.list_blobs(_bucket_client(), prefix=key_prefix)
        return [b.name[len(key_prefix):] for b in blobs]
    dir_path = os.path.join(_LOCAL_ROOT, episode_id, prefix)
    if not os.path.isdir(dir_path):
        return []
    return os.listdir(dir_path)
