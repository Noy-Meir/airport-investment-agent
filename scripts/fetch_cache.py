"""
Downloads the prebuilt data/cache.db from a GitHub Release so a fresh clone
does not need to rebuild from BTS/FAA/OurAirports source data (slow, and
requires raw-data downloads of its own -- see scripts/build_cache.py).

The expected SHA-256 is read from the committed data/cache.db.sha256 file
(not hardcoded here), so republishing the release just means updating that
one file.

Usage:
    python scripts/fetch_cache.py
    python scripts/fetch_cache.py --force
"""

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cache.config import CACHE_DB_PATH

RELEASE_URL = "https://github.com/Noy-Meir/airport-investment-agent/releases/download/data-v1/cache.db"
SHA256_PATH = CACHE_DB_PATH + ".sha256"
TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 1024


class FetchCacheError(Exception):
    pass


def _read_expected_sha256(sha256_path=None):
    sha256_path = sha256_path or SHA256_PATH
    if not os.path.exists(sha256_path):
        raise FetchCacheError(
            f"{sha256_path} not found -- this file must be committed with the "
            "expected hash of the published cache.db release before "
            "scripts/fetch_cache.py can verify a download."
        )
    with open(sha256_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    # Accept either a bare hash or standard "<hash>  <filename>" sha256sum format.
    digest = text.split()[0] if text else ""
    if len(digest) != 64:
        raise FetchCacheError(f"{sha256_path} does not contain a valid SHA-256 hash: {text!r}")
    return digest.lower()


def _sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": "airport-investment-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            total = resp.getheader("Content-Length")
            total = int(total) if total else None
            downloaded = 0
            with open(dest_path, "wb") as out:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    _print_progress(downloaded, total)
            print()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FetchCacheError(
                f"download failed: HTTP 404 for {url} -- the data release may not be "
                "published yet. Run scripts/build_cache.py instead to rebuild from "
                "source (slower)."
            ) from e
        raise FetchCacheError(f"download failed: HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise FetchCacheError(f"download failed: {e}") from e


def _print_progress(downloaded, total):
    if total:
        pct = downloaded * 100 // total
        print(f"\rdownloading cache.db: {pct}% ({downloaded / 1e6:.1f}/{total / 1e6:.1f} MB)", end="", flush=True)
    else:
        print(f"\rdownloading cache.db: {downloaded / 1e6:.1f} MB", end="", flush=True)


def fetch_cache(dest_path=CACHE_DB_PATH, force=False):
    expected = _read_expected_sha256()

    if not force and os.path.exists(dest_path):
        if _sha256_of_file(dest_path) == expected:
            print("cache already up to date")
            return
        print("existing cache.db hash does not match expected -- re-downloading")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp_path = dest_path + ".download"

    _download(RELEASE_URL, tmp_path)

    actual = _sha256_of_file(tmp_path)
    if actual != expected:
        os.remove(tmp_path)
        raise FetchCacheError(
            f"SHA-256 mismatch: expected {expected}, got {actual} -- discarded download."
        )

    os.replace(tmp_path, dest_path)
    print(f"cache.db installed at {dest_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if cache.db already matches")
    args = parser.parse_args()

    try:
        fetch_cache(force=args.force)
    except FetchCacheError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
