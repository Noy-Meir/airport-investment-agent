"""
Client for downloading BTS T-100 Segment (All Carriers), route-level data.

There is no stable API for this table (see docs/DECISIONS.md, Source B) --
it only exists behind transtats.bts.gov's ASP.NET form (`DL_SelectFields.aspx`,
table FMG). This module scripts that form: GET the page fresh, scrape the
real hidden fields and field checkboxes off that exact page, then POST with
every field checked + the target year + chkDownloadZip.

CLI:
    python -m src.clients.t100_routes 2024 2025

Each year is fetched with its own fresh cookie jar / viewstate and retried
once on failure. Saves data/raw/t100_segment_all_carrier_<year>.zip (gitignored).
Never loads the resulting ZIP/CSV into memory here -- that's scripts/build_cache.py's job.
"""

import html
import http.cookiejar
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 300
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 10

FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4%20Pn44vr45"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw")
LOG_DIR = os.path.join(REPO_ROOT, "data", "logs")

logger = logging.getLogger("t100_routes")


class T100DownloadError(Exception):
    """Raised when the FMG form download fails after all retries."""


def _configure_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "t100_routes.log")
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return log_path


def _extract_hidden_fields(page_text):
    def getval(name):
        m = re.search(r'id="' + name + r'"[^>]*value="([^"]*)"', page_text)
        return html.unescape(m.group(1)) if m else ""

    return (
        getval("__VIEWSTATE"),
        getval("__VIEWSTATEGENERATOR"),
        getval("__EVENTVALIDATION"),
    )


def _attempt_download(year, cookie_path):
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": FORM_URL,
        "Origin": "https://www.transtats.bts.gov",
    }
    cj = http.cookiejar.MozillaCookieJar(cookie_path)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    req = urllib.request.Request(FORM_URL, headers=headers)
    resp = opener.open(req, timeout=TIMEOUT_SECONDS)
    page = resp.read().decode("utf-8", errors="replace")
    logger.info("GET %s -> HTTP %s, %d chars", FORM_URL, resp.status, len(page))

    vs, vsg, ev = _extract_hidden_fields(page)
    fields = re.findall(r'<input id="([A-Z0-9_]+)" type="checkbox" name="\1"', page)
    logger.info(
        "viewstate_len=%d field_checkboxes=%d CLASS_present=%s",
        len(vs), len(fields), "CLASS" in fields,
    )

    if not fields or not vs:
        raise T100DownloadError(
            "form page did not contain the expected hidden fields / checkboxes "
            "(BTS may have changed the form -- see docs/DECISIONS.md Source B for the manual fallback)"
        )

    data = [
        ("__EVENTTARGET", ""),
        ("__EVENTARGUMENT", ""),
        ("__VIEWSTATE", vs),
        ("__VIEWSTATEGENERATOR", vsg),
        ("__EVENTVALIDATION", ev),
        ("cboGeography", "All"),
        ("cboYear", str(year)),
        ("cboPeriod", "All"),
    ] + [(f, "on") for f in fields] + [
        ("chkDownloadZip", "on"),
        ("btnDownload", "Download"),
    ]
    body = urllib.parse.urlencode(data).encode()
    post_headers = dict(headers, **{"Content-Type": "application/x-www-form-urlencoded"})
    req = urllib.request.Request(FORM_URL, data=body, headers=post_headers)

    resp = opener.open(req, timeout=TIMEOUT_SECONDS)
    content = resp.read()
    ct = resp.headers.get("Content-Type", "")
    logger.info("POST -> HTTP %s, Content-Type=%s, %d bytes", resp.status, ct, len(content))

    if content[:2] != b"PK":
        preview = content[:200].decode("utf-8", errors="replace")
        raise T100DownloadError(
            f"response for year {year} was not a ZIP (Content-Type={ct!r}, "
            f"{len(content)} bytes, body did not start with PK). "
            f"First 200 chars: {preview!r}. See docs/DECISIONS.md Source B for the manual fallback."
        )

    return content


def download_year(year, raw_dir=RAW_DIR):
    """Download the T-100 Segment All Carrier ZIP for one year. Returns the saved path."""
    os.makedirs(raw_dir, exist_ok=True)
    dest_path = os.path.join(raw_dir, f"t100_segment_all_carrier_{year}.zip")

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        cookie_path = os.path.join(LOG_DIR, f".cookies_{year}_attempt{attempt}.txt")
        logger.info("year=%s attempt=%d/%d", year, attempt, MAX_ATTEMPTS)
        try:
            content = _attempt_download(year, cookie_path)
            with open(dest_path, "wb") as f:
                f.write(content)
            logger.info("year=%s saved %s (%d bytes)", year, dest_path, len(content))
            return dest_path
        except (T100DownloadError, urllib.error.URLError, TimeoutError) as e:
            last_error = e
            logger.warning("year=%s attempt=%d failed: %s", year, attempt, e)
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS)
        finally:
            try:
                os.remove(cookie_path)
            except OSError:
                pass

    raise T100DownloadError(
        f"year {year}: all {MAX_ATTEMPTS} attempts failed. Last error: {last_error}"
    )


def main(argv):
    if not argv:
        print("usage: python -m src.clients.t100_routes <year> [<year> ...]", file=sys.stderr)
        return 2

    log_path = _configure_logging()
    results = []
    for arg in argv:
        try:
            year = int(arg)
        except ValueError:
            print(f"error: {arg!r} is not a valid year", file=sys.stderr)
            return 2
        try:
            path = download_year(year)
            size = os.path.getsize(path)
            results.append(f"  {year}: OK -> {path} ({size:,} bytes)")
        except T100DownloadError as e:
            results.append(f"  {year}: FAILED -> {e}")

    print("T-100 route-level download summary:")
    print("\n".join(results))
    print(f"Full log: {log_path}")
    return 0 if all("OK" in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
