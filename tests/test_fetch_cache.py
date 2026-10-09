"""
Verifies scripts/fetch_cache.py's hash-match skip, hash-mismatch failure, and
successful install, all without touching the network (urllib.request.urlopen
is monkeypatched out).
"""

import hashlib
import importlib.util
import os
import sys

import pytest

SCRIPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "fetch_cache.py")
spec = importlib.util.spec_from_file_location("fetch_cache", SCRIPT_PATH)
fetch_cache = importlib.util.module_from_spec(spec)
sys.modules["fetch_cache"] = fetch_cache
spec.loader.exec_module(fetch_cache)


def _write_sha256(path, content_bytes):
    digest = hashlib.sha256(content_bytes).hexdigest()
    with open(path, "w", encoding="utf-8") as f:
        f.write(digest + "\n")
    return digest


class _FakeResponse:
    def __init__(self, body):
        self._body = body
        self._pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getheader(self, name):
        return str(len(self._body)) if name == "Content-Length" else None

    def read(self, n=-1):
        if n is None or n < 0:
            chunk = self._body[self._pos:]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_skips_when_hash_matches(tmp_path, monkeypatch):
    dest = tmp_path / "cache.db"
    body = b"already-correct-content"
    dest.write_bytes(body)
    sha_path = str(dest) + ".sha256"
    _write_sha256(sha_path, body)
    monkeypatch.setattr(fetch_cache, "SHA256_PATH", sha_path)

    def fail_urlopen(*a, **k):
        raise AssertionError("should not attempt a download when hash already matches")

    monkeypatch.setattr(fetch_cache.urllib.request, "urlopen", fail_urlopen)

    fetch_cache.fetch_cache(dest_path=str(dest))
    assert dest.read_bytes() == body


def test_fails_on_hash_mismatch(tmp_path, monkeypatch):
    dest = tmp_path / "cache.db"
    sha_path = str(dest) + ".sha256"
    _write_sha256(sha_path, b"expected-content")
    monkeypatch.setattr(fetch_cache, "SHA256_PATH", sha_path)
    monkeypatch.setattr(fetch_cache.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"wrong-content"))

    with pytest.raises(fetch_cache.FetchCacheError, match="mismatch"):
        fetch_cache.fetch_cache(dest_path=str(dest))

    assert not dest.exists()
    assert not os.path.exists(str(dest) + ".download")


def test_successful_install(tmp_path, monkeypatch):
    dest = tmp_path / "cache.db"
    sha_path = str(dest) + ".sha256"
    body = b"the-real-cache-contents"
    _write_sha256(sha_path, body)
    monkeypatch.setattr(fetch_cache, "SHA256_PATH", sha_path)
    monkeypatch.setattr(fetch_cache.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(body))

    fetch_cache.fetch_cache(dest_path=str(dest))

    assert dest.read_bytes() == body
    assert not os.path.exists(str(dest) + ".download")


def test_missing_sha256_file_raises(tmp_path, monkeypatch):
    dest = tmp_path / "cache.db"
    monkeypatch.setattr(fetch_cache, "SHA256_PATH", str(dest) + ".sha256")

    with pytest.raises(fetch_cache.FetchCacheError, match="not found"):
        fetch_cache.fetch_cache(dest_path=str(dest))
