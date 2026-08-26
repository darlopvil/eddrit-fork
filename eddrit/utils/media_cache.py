import asyncio
import hashlib
import json
import time
from pathlib import Path

from eddrit import config

# Metadata (content-type) is stored next to the payload so cached responses can
# be served with the right header without re-fetching from Reddit.
_META_SUFFIX = ".meta"

_cleanup_lock = asyncio.Lock()
_last_cleanup = 0.0
CLEANUP_INTERVAL_SECONDS = 600


def _cache_dir() -> Path:
    return Path(config.MEDIA_CACHE_DIR)


def _key(url: str) -> str:
    """Hash the URL so no readable URLs (with their signatures) hit the disk."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _paths(url: str) -> tuple[Path, Path]:
    key = _key(url)
    base = _cache_dir() / key[:2]
    return base / key, base / f"{key}{_META_SUFFIX}"


def get(url: str) -> tuple[bytes, str] | None:
    """Return (payload, content_type) if cached, None otherwise."""
    if not config.MEDIA_CACHE_ENABLED:
        return None
    data_path, meta_path = _paths(url)
    try:
        content_type = json.loads(meta_path.read_text())["content_type"]
        return data_path.read_bytes(), content_type
    except Exception:
        return None


def put(url: str, payload: bytes, content_type: str) -> None:
    if not config.MEDIA_CACHE_ENABLED:
        return
    data_path, meta_path = _paths(url)
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename: a partial file must never be served.
        tmp = data_path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        tmp.rename(data_path)
        meta_path.write_text(json.dumps({"content_type": content_type}))
    except Exception:
        pass


async def cleanup_if_needed() -> None:
    """Drop entries older than the retention window, then trim to the size cap."""
    global _last_cleanup
    if not config.MEDIA_CACHE_ENABLED:
        return
    now = time.time()
    if now - _last_cleanup < CLEANUP_INTERVAL_SECONDS:
        return
    async with _cleanup_lock:
        if now - _last_cleanup < CLEANUP_INTERVAL_SECONDS:
            return
        _last_cleanup = now
        await asyncio.to_thread(_cleanup)


def _cleanup() -> None:
    root = _cache_dir()
    if not root.exists():
        return

    max_age = config.MEDIA_CACHE_DAYS * 86400
    max_bytes = config.MEDIA_CACHE_MAX_MB * 1024 * 1024
    now = time.time()

    entries = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.endswith(_META_SUFFIX):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if now - stat.st_mtime > max_age:
            _remove(path)
            continue
        entries.append((stat.st_mtime, stat.st_size, path))

    total = sum(size for _, size, _ in entries)
    if total <= max_bytes:
        return

    # Over the cap: drop oldest first until back under it.
    for _, size, path in sorted(entries):
        if total <= max_bytes:
            break
        _remove(path)
        total -= size


def _remove(path: Path) -> None:
    for candidate in (path, path.with_name(f"{path.name}{_META_SUFFIX}")):
        try:
            candidate.unlink()
        except OSError:
            pass
