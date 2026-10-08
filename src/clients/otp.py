"""
Client for downloading BTS On-Time Performance (OTP) monthly ZIPs
(Reporting Carrier On-Time Performance, 1987-present).

No stable API -- these are static monthly ZIPs served from transtats.bts.gov:
    https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_<YYYY>_<M>.zip

Each month is ~30-35MB; a trailing-12-month pull is ~400MB total -- per
CLAUDE.md's cost rules, ask the user before running the real download.

CLI:
    python -m src.clients.otp <start_year> <start_month> <end_year> <end_month> [--force]
    python -m src.clients.otp [--force]    # defaults to the 12 months ending
                                            # at the latest month available
                                            # upstream (found via HEAD probes)
    --force re-downloads a month even if a valid ZIP already exists on disk.

Saves data/raw/otp_<year>_<month>.zip (gitignored). Does read each monthly
ZIP (~30-35MB) fully into memory here (see `_attempt_download`) before
writing it to disk -- it does NOT load the decoded CSV inside the ZIP into
memory; that's src/cache/otp_aggregate.py's job.

`download_month` skips a month whose ZIP already exists on disk and looks
valid (starts with a PK header and is > 1MB), logging "skipped (exists)".
Pass --force on the CLI (or force=True to `download_month`) to re-download
anyway.
"""

import datetime
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 300
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 10

URL_TEMPLATE = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
USER_AGENT = "airport-investment-agent/1.0"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw")
LOG_DIR = os.path.join(REPO_ROOT, "data", "logs")

logger = logging.getLogger("otp")


class OTPDownloadError(Exception):
    """Raised when an OTP monthly ZIP download fails after all retries."""


def _configure_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "otp.log")
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return log_path


def month_url(year, month):
    return URL_TEMPLATE.format(year=year, month=month)


def month_range(start_year, start_month, end_year, end_month):
    """Inclusive list of (year, month) tuples from start to end."""
    if (start_year, start_month) > (end_year, end_month):
        raise ValueError(f"start ({start_year}-{start_month}) is after end ({end_year}-{end_month})")
    months = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        months.append((y, m))
        m += 1
        if m == 13:
            m = 1
            y += 1
    return months


def _month_exists(year, month):
    url = month_url(year, month)
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return False
    except urllib.error.URLError:
        return False


def find_latest_available_month(probe_from=None):
    """
    Walks backward month-by-month from `probe_from` (default: today, UTC),
    HEAD-requesting each month's ZIP, and returns the first (year, month)
    that exists upstream. Raises OTPDownloadError if nothing is found within
    24 months (BTS publishes with a lag of roughly one to two months).
    """
    if probe_from is None:
        today = datetime.datetime.now(datetime.timezone.utc)
        y, m = today.year, today.month
    else:
        y, m = probe_from
    for _ in range(24):
        if _month_exists(y, m):
            return y, m
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    raise OTPDownloadError("could not find any available OTP month in the last 24 months via HEAD probes")


def default_window():
    """The 12 months ending at the latest month available upstream."""
    end_year, end_month = find_latest_available_month()
    y, m = end_year, end_month - 11
    while m <= 0:
        m += 12
        y -= 1
    return (y, m, end_year, end_month)


def _attempt_download(year, month):
    url = month_url(year, month)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    resp = urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS)
    content = resp.read()
    ct = resp.headers.get("Content-Type", "")
    logger.info("GET %s -> HTTP %s, Content-Type=%s, %d bytes", url, resp.status, ct, len(content))

    if content[:2] != b"PK":
        preview = content[:200].decode("utf-8", errors="replace")
        raise OTPDownloadError(
            f"response for {year}-{month:02d} was not a ZIP (Content-Type={ct!r}, "
            f"{len(content)} bytes, body did not start with PK). First 200 chars: {preview!r}"
        )
    return content


MIN_VALID_ZIP_BYTES = 1_000_000


def _is_valid_existing_zip(path):
    if not os.path.exists(path):
        return False
    if os.path.getsize(path) <= MIN_VALID_ZIP_BYTES:
        return False
    with open(path, "rb") as f:
        return f.read(2) == b"PK"


def download_month(year, month, raw_dir=RAW_DIR, force=False):
    """
    Download one month's OTP ZIP. Returns the saved path. Unless `force` is
    True, skips (and logs "skipped (exists)") if `dest_path` already exists
    and looks like a valid ZIP (PK header, size > 1MB).
    """
    os.makedirs(raw_dir, exist_ok=True)
    dest_path = os.path.join(raw_dir, f"otp_{year}_{month:02d}.zip")

    if not force and _is_valid_existing_zip(dest_path):
        logger.info("year=%s month=%s skipped (exists): %s", year, month, dest_path)
        return dest_path

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info("year=%s month=%s attempt=%d/%d", year, month, attempt, MAX_ATTEMPTS)
        try:
            content = _attempt_download(year, month)
            with open(dest_path, "wb") as f:
                f.write(content)
            logger.info("year=%s month=%s saved %s (%d bytes)", year, month, dest_path, len(content))
            return dest_path
        except (OTPDownloadError, urllib.error.URLError, TimeoutError, socket.timeout) as e:
            last_error = e
            logger.warning("year=%s month=%s attempt=%d failed: %s", year, month, attempt, e)
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS)

    raise OTPDownloadError(
        f"{year}-{month:02d}: all {MAX_ATTEMPTS} attempts failed. Last error: {last_error}"
    )


def main(argv):
    log_path = _configure_logging()

    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]

    if not argv:
        start_year, start_month, end_year, end_month = default_window()
    elif len(argv) == 4:
        try:
            start_year, start_month, end_year, end_month = (int(a) for a in argv)
        except ValueError:
            print("error: all of start_year start_month end_year end_month must be ints", file=sys.stderr)
            return 2
    else:
        print(
            "usage: python -m src.clients.otp [<start_year> <start_month> <end_year> <end_month>] [--force]",
            file=sys.stderr,
        )
        return 2

    months = month_range(start_year, start_month, end_year, end_month)
    print(f"OTP download: {len(months)} month(s), {months[0]}..{months[-1]} (~{len(months) * 35}MB estimated)")

    results = []
    for year, month in months:
        try:
            path = download_month(year, month, force=force)
            size = os.path.getsize(path)
            results.append(f"  {year}-{month:02d}: OK -> {path} ({size:,} bytes)")
        except OTPDownloadError as e:
            results.append(f"  {year}-{month:02d}: FAILED -> {e}")

    print("OTP download summary:")
    print("\n".join(results))
    print(f"Full log: {log_path}")
    return 0 if all("OK" in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
