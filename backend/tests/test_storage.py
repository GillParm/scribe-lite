from __future__ import annotations

import hashlib
import io

import pytest

from scribe.storage import AudioStore, AudioTooLargeError


def test_save_streams_and_hashes(store: AudioStore) -> None:
    data = b"abc" * 1000
    stored = store.save("enc-1", io.BytesIO(data), ".wav", max_bytes=10_000)
    assert stored.relative_path == "enc-1.wav"
    assert stored.size == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert store.path("enc-1.wav").read_bytes() == data


def test_save_refuses_oversized_files_and_cleans_up(store: AudioStore) -> None:
    with pytest.raises(AudioTooLargeError):
        store.save("big", io.BytesIO(b"x" * 101), ".wav", max_bytes=100)
    assert not store.path("big.wav").exists()


def test_delete_returns_bytes_freed(store: AudioStore) -> None:
    store.save("a", io.BytesIO(b"12345"), ".wav", max_bytes=100)
    assert store.delete("a.wav") == 5
    assert store.delete("a.wav") == 0


def test_paths_cannot_escape_root(store: AudioStore) -> None:
    with pytest.raises(ValueError):
        store.path("../outside.wav")
