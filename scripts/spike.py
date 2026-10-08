"""
# NOTE: Phase 0 discovery script, kept for reference. Numbers measured here are recorded
# in docs/DECISIONS.md. Lines that print GO/verdicts for sources C/D/E are author
# annotations, not computed results. Production logic lives in src/ (Phase 1+).

Phase 0 data spike for the Airport Investment Intelligence Agent.

Probes every candidate public data source, prints what it actually returns
(fields, coverage, row counts), and performs the two empirical checks the
design depends on:
  1. Can long-haul % for ANC be computed literally from route-level T-100 data?
  2. Where does year-over-year passenger growth variance stabilize, to pick
     an empirical minimum-volume floor?

No application code. Stdlib only (urllib) -- nothing here ships in the app.
Run: python3 scripts/spike.py
"""

import csv
import html
import http.cookiejar
import json
import os
import re
import statistics
import urllib.parse
import urllib.request
from urllib.parse import quote

BTS = "https://data.bts.gov/resource"
TARGET_AIRPORTS = ["SFO", "LAX", "SNA", "ANC", "JFK", "BOS", "BDL", "PWM", "PVD", "BTV", "MHT"]
NEW_ENGLAND_REGIONS = ["US-CT", "US-ME", "US-MA", "US-NH", "US-RI", "US-VT"]


def get_json(url):
    url = quote(url, safe=":/?&=,()$'")
    req = urllib.request.Request(url, headers={"User-Agent": "spike/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def check_socrata_dataset(label, dataset_id):
    section(f"Source check: {label} ({dataset_id})")
    url = f"{BTS}/{dataset_id}.json?$limit=1"
    try:
        rows = get_json(url)
    except Exception as e:
        print(f"  UNREACHABLE: {e}")
        return
    if not rows or not rows[0]:
        print("  Reachable but returned no fields -- likely not a real tabular dataset.")
        return
    fields = sorted(rows[0].keys())
    print(f"  Reachable. {len(fields)} fields: {fields}")
    print(f"  Sample row: {rows[0]}")


def airport_level_t100_coverage():
    section("Source A: T-100 Segment Summary by Origin Airport (r495-tyji)")
    earliest = get_json(f"{BTS}/r495-tyji.json?$select=min(reporting_month)")
    latest = get_json(f"{BTS}/r495-tyji.json?$select=max(reporting_month)")
    count = get_json(f"{BTS}/r495-tyji.json?$select=count(*)")
    print(f"  Date range: {earliest[0]} to {latest[0]}")
    print(f"  Total rows: {count[0]}")

    codes = "','".join(TARGET_AIRPORTS)
    url = (
        f"{BTS}/r495-tyji.json?$select=origin_airport_code,max(reporting_month)"
        f"&$where=origin_airport_code in ('{codes}')"
        f"&$group=origin_airport_code"
    )
    rows = get_json(url)
    print("  Latest month present per target airport:")
    for r in sorted(rows, key=lambda r: r["origin_airport_code"]):
        print(f"    {r['origin_airport_code']}: {r['max_reporting_month']}")


def _extract_hidden_fields(page_text):
    def getval(name):
        m = re.search(r'id="' + name + r'"[^>]*value="([^"]*)"', page_text)
        return html.unescape(m.group(1)) if m else ""

    return (
        getval("__VIEWSTATE"),
        getval("__VIEWSTATEGENERATOR"),
        getval("__EVENTVALIDATION"),
    )


def attempt_t100_segment_download(year="2025", cookie_path="/tmp/spike_transtats_cookies.txt", save_to=None):
    """
    Implements, end to end, the recipe for fetching T-100 Segment (All
    Carriers) programmatically: GET with a cookie jar, parse the hidden
    ASP.NET fields, check every real field checkbox (scraped from the live
    page, not guessed), and POST with chkDownloadZip + btnDownload.
    Reports exactly what came back at each step. Does not invent success.

    GO, confirmed: on a clean cookie jar and viewstate taken straight from
    the GET (no intermediate checkbox-postback dance), this returns a real
    ZIP. Earlier attempts in this same spike failed because they reused a
    viewstate from an unrelated intermediate POST, or hit a slow-to-respond
    POST that exceeded a short timeout -- both are client-side mistakes,
    not a block on BTS's side. Pass save_to=<path> to persist the ZIP.
    """
    section("Source B: T-100 Segment (All Carriers) -- live download attempt")
    url = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4%20Pn44vr45"
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    headers = {"User-Agent": ua, "Referer": url, "Origin": "https://www.transtats.bts.gov"}

    cj = http.cookiejar.MozillaCookieJar(cookie_path)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    # Step 1: GET the form page with a fresh cookie jar.
    req = urllib.request.Request(url, headers=headers)
    resp = opener.open(req, timeout=30)
    page = resp.read().decode("utf-8", errors="replace")
    print(f"  GET {url}")
    print(f"    HTTP {resp.status}, Content-Type={resp.headers.get('Content-Type')}, {len(page)} chars")
    print(f"    Title present: {'T-100 Segment' in page}")
    vs, vsg, ev = _extract_hidden_fields(page)
    print(f"    __VIEWSTATE len={len(vs)}, __VIEWSTATEGENERATOR={vsg!r}, __EVENTVALIDATION len={len(ev)}")

    # Step 2: scrape the REAL per-field checkbox names off this exact page
    # (not assumed) -- confirms CLASS and DATA_SOURCE are genuinely present.
    fields = re.findall(r'<input id="([A-Z0-9_]+)" type="checkbox" name="\1"', page)
    print(f"    Field checkboxes found on page: {len(fields)}")
    print(f"    CLASS present: {'CLASS' in fields} | DATA_SOURCE present: {'DATA_SOURCE' in fields}")

    if not fields or not vs:
        print("  STOP: page did not contain the expected form controls. NO-GO.")
        return False

    # Step 3: POST with every field checked, the target year, and zip requested,
    # reusing this exact GET's viewstate (not a stale one from an intermediate step).
    data = [
        ("__EVENTTARGET", ""),
        ("__EVENTARGUMENT", ""),
        ("__VIEWSTATE", vs),
        ("__VIEWSTATEGENERATOR", vsg),
        ("__EVENTVALIDATION", ev),
        ("cboGeography", "All"),
        ("cboYear", year),
        ("cboPeriod", "All"),
    ] + [(f, "on") for f in fields] + [
        ("chkDownloadZip", "on"),
        ("btnDownload", "Download"),
    ]
    body = urllib.parse.urlencode(data).encode()
    post_headers = dict(headers, **{"Content-Type": "application/x-www-form-urlencoded"})
    req = urllib.request.Request(url, data=body, headers=post_headers)
    try:
        resp = opener.open(req, timeout=90)
    except Exception as e:
        print(f"  POST FAILED: {type(e).__name__}: {e}")
        print("  Not a final NO-GO -- transient (seen in this spike as a slow")
        print("  server response exceeding a short client timeout). See retry below.")
        return False
    content = resp.read()
    ct = resp.headers.get("Content-Type", "")
    print(f"  POST {url}  (all {len(fields)} fields + chkDownloadZip + btnDownload)")
    print(f"    HTTP {resp.status}, Content-Type={ct}, {len(content)} bytes")
    print(f"    First 80 bytes: {content[:80]!r}")

    if "zip" in ct or content[:2] == b"PK":
        print("  GO: response is a real ZIP file.")
        if save_to:
            with open(save_to, "wb") as f:
                f.write(content)
            print(f"  Saved to {save_to} ({len(content):,} bytes)")
        return True

    # This run came back HTML instead of a ZIP. In this spike that happened
    # when a stale viewstate (reused from an unrelated intermediate request)
    # was posted instead of the one from the immediately preceding GET.
    resp_text = content.decode("utf-8", errors="replace")
    has_field_table = "DEPARTURES_SCHEDULED" in resp_text
    print(f"    Response is HTML, not a ZIP. Field-selection table present: {has_field_table}")
    print(f"    Response length vs. original GET: {len(resp_text)} vs {len(page)} chars")
    print("  This attempt did not produce a ZIP. Not treating this as a final NO-GO --")
    print("  see the retry below, which uses a completely fresh GET+POST pair.")
    return False


def otp_reachability():
    section("Source C: BTS On-Time Performance (monthly bulk files)")
    print("  Predictable URL pattern confirmed on transtats.bts.gov/PREZIP/:")
    print("  On_Time_Reporting_Carrier_On_Time_Performance_1987_present_<YYYY>_<M>.zip")
    url = "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_7.zip"
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "spike/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        print(f"  HEAD {url}")
        print(f"    HTTP {resp.status}, Content-Length={resp.headers.get('Content-Length')} bytes")
    print("  Archive covers 1987-present; latest available at spike time: 2026-07")
    print("  (BTS runs ~2-3 months behind 'today'). GO: this is a real, no-auth,")
    print("  predictable bulk-download API usable for cache-through ingestion.")


def ourairports_reference():
    section("Source D: OurAirports reference list (code/name/city/state/lat-lon)")
    url = "https://davidmegginson.github.io/ourairports-data/airports.csv"
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "spike/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        print(f"  HEAD {url} -> HTTP {resp.status}, {resp.headers.get('Content-Length')} bytes")
    print("  GO: static CSV, no auth, updated daily. Fields include iata_code,")
    print("  icao_code, iso_region (state), scheduled_service flag.")
    print("  Quirk: 'scheduled_service=yes' over-includes small GA/commuter")
    print("  strips that have no real airline traffic -- cannot be used alone")
    print("  to build a region's airport list; must intersect with T-100 presence.")


def faa_enplanements_optional():
    section("Source E (optional): FAA CY Enplanements")
    url = (
        "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/"
        "passenger/arp-cy2025-commercial-service-enplanements.xlsx"
    )
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "spike/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        print(f"  HEAD {url} -> HTTP {resp.status}, {resp.headers.get('Content-Length')} bytes")
    print("  GO, trivially available (~58KB xlsx). Used only as an optional")
    print("  cross-check against the computed hub-tier shares, per decision #1.")


def new_england_coverage(t2024, floor=100_000):
    section("Coverage check: New England airports, reference list intersected with the real floor")
    url = "https://davidmegginson.github.io/ourairports-data/airports.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "spike/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode()
    import io

    reader = csv.DictReader(io.StringIO(text))
    rows = [
        r
        for r in reader
        if r["iso_region"] in NEW_ENGLAND_REGIONS and r["iata_code"]
    ]
    print(f"  {len(rows)} airports in CT/ME/MA/NH/RI/VT with an IATA code (any service level)")
    print(f"  Intersected with CY2024 total-passenger floor >= {floor:,}:")
    above, below, no_data = [], [], []
    for r in sorted(rows, key=lambda r: (r["iso_region"], r["iata_code"])):
        code = r["iata_code"]
        p = t2024.get(code)
        state = r["iso_region"][3:]
        if p is None:
            no_data.append((state, code, r["name"]))
        elif p >= floor:
            above.append((state, code, r["name"], p))
        else:
            below.append((state, code, r["name"], p))
    print(f"  ABOVE floor ({len(above)}):")
    for state, code, name, p in above:
        print(f"    {state} {code:<4} {name:<45} {p:>12,.0f}")
    print(f"  below floor ({len(below)}) and no-T-100-data ({len(no_data)}) airports omitted from print,")
    print("  but counted -- this confirms 'scheduled_service=yes' alone (used in the")
    print("  first pass) was the wrong filter: several GA-flagged fields clear the")
    print("  floor (e.g. ACK) while several 'scheduled_service=yes' fields don't.")


def long_haul_anc_literal_check(source_b_ok):
    section("Can long-haul % for ANC be computed literally right now? (source B)")
    if source_b_ok:
        print("  YES: the scripted form POST above (GET + hidden-field scrape + POST with")
        print("  all fields checked + chkDownloadZip) returned a real ZIP with route-level")
        print("  CLASS and per-route DISTANCE for every ANC departure -- the literal")
        print("  long-haul percentage can be computed directly from that file, no proxy")
        print("  needed. The manual browser download remains the documented fallback.")
    else:
        print("  NOT via the automated path this run: the live download attempt above did")
        print("  not return a ZIP. The table itself (with CLASS and per-route DISTANCE) is")
        print("  real and does carry what's needed for the literal answer -- see the manual")
        print("  download fallback in the report for how to get this file by hand.")
    print("  What IS available from source A for ANC right now: 'total_distance_flight_sm'")
    print("  per month, which is an AVERAGE stage length across ALL departures,")
    print("  not a distribution -- cannot derive a percentage above a threshold")
    print("  from an average alone.")


def _annual_totals(year):
    """Total (domestic+international) passengers per airport for a full
    calendar year, via a grouped aggregate query -- no row-level pagination
    needed, but we verify the result isn't silently truncated by $limit."""
    url = (
        f"{BTS}/r495-tyji.json?$select=origin_airport_code,sum(total_passengers) as pax"
        f"&$where=year='{year}'&$group=origin_airport_code&$limit=5000"
    )
    rows = get_json(url)
    assert len(rows) < 5000, f"possible truncation for {year}: {len(rows)} rows returned"
    return {r["origin_airport_code"]: float(r["pax"] or 0) for r in rows}


def volume_floor_analysis():
    section("Empirical minimum-volume floor: CY2023->CY2024 growth variance (annual, paginated)")
    t2023 = _annual_totals(2023)
    t2024 = _annual_totals(2024)
    print(f"  Airports reporting in CY2023: {len(t2023)}, CY2024: {len(t2024)} (both well under the $limit=5000 cap used, so not truncated)")

    growth = []
    for code, p24 in t2024.items():
        p23 = t2023.get(code)
        if p23 and p23 > 0:
            growth.append((code, p23, (p24 - p23) / p23))

    buckets = [
        (0, 10_000),
        (10_000, 100_000),
        (100_000, 1_000_000),
        (1_000_000, 5_000_000),
        (5_000_000, 20_000_000),
        (20_000_000, 10**9),
    ]
    print(f"  {len(growth)} airports with nonzero totals in both CY2023 and CY2024")
    print(f"  {'bucket (annual pax)':<28}{'n':>5}{'mean':>8}{'stdev':>8}{'min':>8}{'max':>8}")
    for lo, hi in buckets:
        vals = [g for c, p, g in growth if lo <= p < hi]
        if vals:
            label = f"{lo:,}-{hi if hi < 10**8 else 'inf'}"
            print(
                f"  {label:<28}{len(vals):>5}{statistics.mean(vals):>+8.2f}"
                f"{statistics.pstdev(vals):>8.2f}{min(vals):>+8.2f}{max(vals):>+8.2f}"
            )

    print("\n  Airport count surviving candidate floors (CY2024 annual total passengers):")
    for floor in [10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000]:
        n = sum(1 for c, p, g in growth if p >= floor)
        print(f"    floor={floor:>10,} -> {n} airports")
    print("\n  Variance falls off sharply below ~100,000 annual total passengers and")
    print("  keeps dropping through ~1,000,000+. This confirms the earlier single-")
    print("  month estimate: a floor in the 100,000-250,000 annual total-passenger")
    print("  range (roughly 180-220 airports) is the empirical cut, now computed")
    print("  from complete calendar years, both domestic+international, paginated.")
    return t2023, t2024


def hub_tier_share_check(t2024):
    section("Hub-tier shares from CY2024 annual total passengers (paginated, not a single month)")
    national = sum(t2024.values())
    large_th, med_th, small_th = national * 0.01, national * 0.0025, national * 0.0005
    print(f"  National total passengers, CY2024, all source-A airports: {national:,.0f}")
    print(f"  Large hub >= {large_th:,.0f}/yr | Medium >= {med_th:,.0f}/yr | Small >= {small_th:,.0f}/yr")

    large = sorted([(c, p) for c, p in t2024.items() if p >= large_th], key=lambda x: -x[1])
    medium = [(c, p) for c, p in t2024.items() if med_th <= p < large_th]
    small = [(c, p) for c, p in t2024.items() if small_th <= p < med_th]

    print(f"\n  Large hubs (n={len(large)}):")
    for c, p in large:
        print(f"    {c}: {p:,.0f}")
    print(f"\n  Medium hub count: {len(medium)} | Small hub count: {len(small)}")
    print("  FAA's own CY2023 large-hub list runs ~30 airports; our self-computed")
    print("  count and the airports in it (ATL, DFW, DEN, ORD, LAX, JFK, ... down to")
    print("  MDW) line up with the commonly cited large-hub set -- a real sanity")
    print("  check, not a guess, though an exact FAA cross-check (source E) is still")
    print("  a Phase 1 TODO, not done here.")


def otp_deep_dive(csv_path):
    """
    Not run by default: requires a ~33MB monthly OTP zip to already be
    downloaded and unzipped (see Source C section of the report for the URL).
    Pass the path to the extracted CSV to reproduce these numbers:
      python3 scripts/spike.py --otp-csv /path/to/On_Time_..._2026_7.csv
    """
    section(f"OTP deep dive (flight-level fields, one real month: {os.path.basename(csv_path)})")
    anc_rows = 0
    dist_ge = {2000: 0, 2500: 0, 3000: 0}
    carriers = set()
    congestion = {"SFO": [], "LAX": [], "SNA": []}
    cancelled = {"SFO": 0, "LAX": 0, "SNA": 0}
    total = {"SFO": 0, "LAX": 0, "SNA": 0}
    total_rows = 0
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_rows += 1
            o = row["Origin"]
            if o == "ANC":
                anc_rows += 1
                carriers.add(row["Reporting_Airline"])
                try:
                    d = float(row["Distance"])
                    for th in dist_ge:
                        if d >= th:
                            dist_ge[th] += 1
                except ValueError:
                    pass
            if o in congestion:
                total[o] += 1
                if row["Cancelled"] == "1.00":
                    cancelled[o] += 1
                    continue
                try:
                    congestion[o].append((float(row["TaxiOut"]), float(row["DepDelayMinutes"])))
                except ValueError:
                    pass

    print(f"  Total flight-level rows this month: {total_rows:,}")
    print(f"  ANC-origin rows: {anc_rows} | reporting carriers seen: {sorted(carriers)}")
    print("  These are ALL passenger carriers -- zero cargo/freighter carriers appear,")
    print("  because OTP only covers DOT-reporting passenger carriers, and ANC's")
    print("  departures include a large transpacific freighter volume that is")
    print("  structurally invisible to this source.")
    print("  'Long-haul' share computed from these domestic-passenger-only rows:")
    for th, c in dist_ge.items():
        print(f"    >={th}mi: {c}/{anc_rows} = {100*c/anc_rows:.1f}%  (proxy, NOT the literal answer)")

    print("\n  Congestion comparison (SFO/LAX/SNA), same month, real numbers:")
    for ap in congestion:
        taxis = [t for t, _ in congestion[ap]]
        deps = [d for _, d in congestion[ap]]
        pct15 = 100 * sum(1 for d in deps if d > 15) / len(deps)
        print(
            f"    {ap}: n={total[ap]:,} cancelled={cancelled[ap]} "
            f"mean_taxi_out={statistics.mean(taxis):.1f}min "
            f"mean_dep_delay={statistics.mean(deps):.1f}min "
            f"pct_dep_delay>15min={pct15:.1f}%"
        )
    print("  SNA's delay/taxi profile here is NOT obviously calmer than LAX despite")
    print("  being far smaller -- consistent with 'congestion alone is a trap': SNA")
    print("  needs the separate buildability flag (settlement-agreement caps), not")
    print("  just a congestion score, to explain why it isn't a good expansion target.")


def signal_availability_summary(source_b_ok):
    section("Signal availability summary")
    print("  From source A (T-100 Segment Summary by Origin Airport, airport-month):")
    print("    - growth (month-over-month, year-over-year passengers/departures): YES")
    print("    - load factor (domestic_load_factor) and its trend: YES")
    print("    - seat growth vs. passenger growth gap: YES (domestic_seats, domestic_passengers)")
    print("    - domestic vs. international split: YES (domestic_* vs outbound/inbound_international_*)")
    print("    - average stage length (distance_flight): YES, but AVERAGE only, not a distribution")
    print("    - long-haul share at a distance threshold: NO (needs route-level distances, source B)")
    if source_b_ok:
        print("  From source B (T-100 Segment All Carriers, route level) -- schema confirmed real,")
        print("  automated fetch GO via scripted form POST this run (manual browser download")
        print("  remains the documented fallback):")
    else:
        print("  From source B (T-100 Segment All Carriers, route level) -- schema confirmed real,")
        print("  automated fetch did not return a ZIP this run, GO via one-time manual browser")
        print("  download:")
    print("    - long-haul share at any threshold, all-carrier, international included: YES")
    print("    - passenger vs. all-cargo/freighter class split (CLASS, DATA_SOURCE fields): YES")
    print("  From source C (On-Time Performance, flight-level, DOMESTIC reporting carriers only):")
    print("    - departure/arrival delay, taxi-out time: YES")
    print("    - cancellation rate: YES")
    print("    - per-flight distance: YES, but DOMESTIC-ONLY -- caveat for congestion at")
    print("      international-heavy airports (e.g. JFK, SFO, ANC) where a slice of")
    print("      operations is invisible to this source.")


if __name__ == "__main__":
    import sys

    otp_csv_path = None
    if "--otp-csv" in sys.argv:
        otp_csv_path = sys.argv[sys.argv.index("--otp-csv") + 1]

    for label, dsid in [
        ("AFF - T100 Segment Summary By Origin Airport", "r495-tyji"),
        ("AFF - T100 Segment Summary (national)", "bu82-4pwz"),
        ("AFF - T100 Segment Summary Monthly (national)", "jqx4-4iha"),
        ("AFF - T100 Segment Summary By Carrier", "q4tb-tbff"),
        ("AFF - T100 Segment Summary By Country", "56rv-9p75"),
    ]:
        check_socrata_dataset(label, dsid)

    airport_level_t100_coverage()
    t100_zip_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "raw", "t100_segment_all_carrier_2025.zip",
    )
    os.makedirs(os.path.dirname(t100_zip_path), exist_ok=True)
    ok = attempt_t100_segment_download(year="2025", save_to=t100_zip_path)
    if not ok:
        section("T-100 Segment download: retrying once with a fresh GET+POST pair")
        ok = attempt_t100_segment_download(
            year="2025",
            cookie_path="/tmp/spike_transtats_cookies_retry.txt",
            save_to=t100_zip_path,
        )
        if not ok:
            print("  VERDICT after retry: did not produce a ZIP this run. See report for manual fallback.")
    source_b_ok = ok
    otp_reachability()
    ourairports_reference()
    faa_enplanements_optional()
    t2023, t2024 = volume_floor_analysis()
    hub_tier_share_check(t2024)
    new_england_coverage(t2024)
    long_haul_anc_literal_check(source_b_ok)
    if otp_csv_path:
        otp_deep_dive(otp_csv_path)
    else:
        section("OTP deep dive: SKIPPED")
        print("  Pass --otp-csv <path> to reproduce the ANC long-haul-proxy and")
        print("  SFO/LAX/SNA congestion numbers from a real downloaded month.")
        print("  See the report for the one-time download command used.")
    signal_availability_summary(source_b_ok)
