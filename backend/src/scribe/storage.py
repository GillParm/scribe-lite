"""Audio files on local disk (a Docker volume in compose; a GCS mount on Cloud Run).

The database stores paths relative to the store root, so the root can move.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024

# Extensions accepted for upload, keyed by MIME type.
AUDIO_TYPES: dict[str, str] = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/webm": ".webm",
}


class AudioTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class StoredAudio:
    relative_path: str
    sha256: str
    size: int


class AudioStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, relative_path: str) -> Path:
        resolved = (self.root / relative_path).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ValueError("audio path escapes the store root")
        return resolved

    def save(self, name: str, source: BinaryIO, suffix: str, max_bytes: int) -> StoredAudio:
        """Stream `source` to disk, hashing as it goes. Refuses files over `max_bytes`."""
        self.root.mkdir(parents=True, exist_ok=True)
        relative = f"{name}{suffix}"
        target = self.path(relative)
        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("wb") as out:
                while chunk := source.read(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise AudioTooLargeError(f"audio is larger than {max_bytes} bytes")
                    digest.update(chunk)
                    out.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return StoredAudio(relative_path=relative, sha256=digest.hexdigest(), size=size)

    def delete(self, relative_path: str) -> int:
        """Delete a file and return the bytes freed (0 if it was already gone)."""
        target = self.path(relative_path)
        try:
            size = target.stat().st_size
        except FileNotFoundError:
            return 0
        target.unlink(missing_ok=True)
        return size
