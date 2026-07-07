"""
PhilJobNet collector — scrapes DOLE's PhilJobNet portal (philjobnet.gov.ph).

WHY THIS FILE IS BUILT THE WAY IT IS
------------------------------------
1. The site is ASP.NET WebForms. Pagination is NOT a URL change — clicking
   "page 2" submits the whole page back to the server as a form POST
   (__doPostBack). We must capture the server's hidden state tokens
   (__VIEWSTATE, __EVENTVALIDATION, etc.) and send them back with each
   page request. A plain GET for "page 2" silently returns page 1 forever.

2. Listing rows mash title/salary/company/location into one anchor with no
   reliable structure, so we DON'T parse fields from listings. Listings give
   us job URLs only. Clean fields come from each job's detail page.

3. Each job detail page links to a company profile page that exposes the
   company's full street address, employment size, and industry — free
   enrichment, zero API cost. We fetch each company page ONCE (cached).

4. HARD-BLANK RULE applies to parsing too: any field we can't confidently
   extract stays "" — never guessed, never inferred.

OUTPUT SCHEMA (one dict per job)
--------------------------------
position_title, job_id, job_url, posted_date, salary, work_location,
company_name, company_id, company_url, company_address, employment_size,
industry

Use dedupe_by_company(jobs) to collapse to one record per company before
the enrichment stage of the pipeline.

STANDALONE TEST
---------------
    python collectors/philjobnet.py --pages 2 --limit 5
"""

import argparse
import csv
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://philjobnet.gov.ph"
LISTING_URL = f"{BASE}/job-vacancies/"
GRIDVIEW_TARGET = "ctl00$BodyContentPlaceHolder$GridView1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-PH,en;q=0.9",
}

DEFAULT_DELAY = 1.5  # seconds between requests — be polite to a gov server

FIELD_ORDER = [
    "position_title", "job_id", "job_url", "posted_date", "salary",
    "work_location", "company_name", "company_id", "company_url",
    "company_address", "employment_size", "industry",
]

# Matches employment-size buckets like "200 and over (Large)", "10 to 99 (Small)"
SIZE_RE = re.compile(r"^\d[\d,]*\s+(?:and over|to\s+\d[\d,]*)\s*\(.+\)$")


# ---------------------------------------------------------------------------
# Parsing functions (pure: HTML in, data out — easy to test in isolation)
# ---------------------------------------------------------------------------

def extract_postback_fields(soup):
    """Collect every hidden form input (__VIEWSTATE etc.) for the next POST."""
    fields = {}
    for inp in soup.select("input[type=hidden]"):
        name = inp.get("name")
        if name:
            fields[name] = inp.get("value", "")
    return fields


def parse_listing(soup):
    """Return job-detail URLs from a listing page (order preserved, deduped)."""
    urls = []
    for a in soup.select('a[href*="/job-vacancies/job/"]'):
        href = a.get("href", "")
        full = href if href.startswith("http") else BASE + href
        if full not in urls:
            urls.append(full)
    return urls


def parse_detail(soup, job_url):
    """Extract clean fields from a job detail page. Blank on any doubt."""
    job = {k: "" for k in FIELD_ORDER}
    job["job_url"] = job_url

    m = re.search(r"-(\d+)/?$", job_url)
    if m:
        job["job_id"] = m.group(1)

    h1 = soup.find("h1")
    if h1:
        job["position_title"] = h1.get_text(strip=True)

    comp = soup.select_one('a[href*="/job-vacancies/company/"]')
    if comp:
        job["company_name"] = comp.get_text(strip=True)
        href = comp.get("href", "")
        job["company_url"] = href if href.startswith("http") else BASE + href
        m = re.search(r"-(\d+)/?$", job["company_url"])
        if m:
            job["company_id"] = m.group(1)

    text = soup.get_text(" ", strip=True)

    m = re.search(r"Posted on\s+(\d{1,2}\s+\w+\s+\d{4})", text)
    if m:
        job["posted_date"] = m.group(1)

    m = re.search(r"(₱[\d,]+(?:\.\d+)?|Salary not specified)", text)
    if m:
        job["salary"] = m.group(1)

    lines = [s.strip() for s in soup.stripped_strings]
    try:
        i = lines.index("Work location")
        job["work_location"] = lines[i + 1]
    except (ValueError, IndexError):
        pass  # stays blank — hard-blank rule

    return job


def parse_company(soup, company_name=""):
    """Extract address / size / industry from a company profile page.

    Page layout: NAME, ADDRESS, SIZE, INDUSTRY appear as consecutive text
    lines. The size line ("200 and over (Large)") is the only one with a
    rigid format, so we anchor on it and read its neighbours.
    """
    out = {"company_address": "", "employment_size": "", "industry": ""}
    lines = [s.strip() for s in soup.stripped_strings]

    for i, line in enumerate(lines):
        if SIZE_RE.match(line):
            out["employment_size"] = line
            prev = lines[i - 1] if i > 0 else ""
            if prev and prev != company_name and "," in prev:
                out["company_address"] = prev
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt and nxt == nxt.upper() and not nxt.startswith("COMPANY"):
                out["industry"] = nxt
            break

    return out


def dedupe_by_company(jobs):
    """Collapse job rows to one record per company (keyed by company_id).

    Pipeline contract: enrich ONCE per company, never per job.
    """
    companies = {}
    for j in jobs:
        key = j["company_id"] or j["company_name"]
        if not key:
            continue
        c = companies.setdefault(key, {
            "company_name": j["company_name"],
            "company_id": j["company_id"],
            "company_url": j["company_url"],
            "company_address": j["company_address"],
            "employment_size": j["employment_size"],
            "industry": j["industry"],
            "job_count": 0,
            "sample_positions": [],
            "latest_posted": j["posted_date"],
        })
        c["job_count"] += 1
        if j["position_title"] and len(c["sample_positions"]) < 3:
            c["sample_positions"].append(j["position_title"])
    for c in companies.values():
        c["sample_positions"] = "; ".join(c["sample_positions"])
    return list(companies.values())


# ---------------------------------------------------------------------------
# Network layer
# ---------------------------------------------------------------------------

def _request(session, method, url, verify=True, **kw):
    """GET/POST with 3 retries and exponential backoff."""
    last = None
    for attempt in range(3):
        try:
            r = session.request(method, url, timeout=30, verify=verify, **kw)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code == 403:
                print("  [!] 403 Forbidden — the server is refusing us. "
                      "Possible bot filtering; see watch-outs.", file=sys.stderr)
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed after 3 attempts: {method} {url} ({last})")


def _dump(html, name):
    with open(name, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [debug] Saved raw HTML to {name} — send this file to Claude.",
          file=sys.stderr)


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def collect(pages=2, limit=None, delay=DEFAULT_DELAY, verify=True):
    session = requests.Session()
    session.headers.update(HEADERS)

    # --- Page 1: a normal GET --------------------------------------------
    print(f"[1/3] Fetching listing page 1 ...")
    r = _request(session, "GET", LISTING_URL, verify=verify)
    landing_url = r.url  # POSTs must go to the post-redirect URL
    soup = BeautifulSoup(r.text, "html.parser")
    job_urls = parse_listing(soup)
    print(f"      page 1: {len(job_urls)} jobs")

    if not job_urls:
        _dump(r.text, "debug_listing.html")
        raise SystemExit("No jobs parsed from page 1 — selectors need "
                         "adjusting against debug_listing.html.")

    # --- Pages 2..N: ASP.NET postbacks -----------------------------------
    for page in range(2, pages + 1):
        fields = extract_postback_fields(soup)
        if "__VIEWSTATE" not in fields:
            _dump(r.text, f"debug_page{page - 1}.html")
            print(f"  [!] No __VIEWSTATE on previous page — cannot paginate. "
                  f"Stopping at page {page - 1}.", file=sys.stderr)
            break
        fields["__EVENTTARGET"] = GRIDVIEW_TARGET
        fields["__EVENTARGUMENT"] = f"Page${page}"

        time.sleep(delay)
        print(f"[1/3] Posting back for listing page {page} ...")
        r = _request(session, "POST", landing_url, data=fields, verify=verify)
        soup = BeautifulSoup(r.text, "html.parser")
        new = [u for u in parse_listing(soup) if u not in job_urls]
        print(f"      page {page}: {len(new)} new jobs")

        if not new:
            _dump(r.text, f"debug_page{page}.html")
            print(f"  [!] Page {page} returned no new jobs — the postback "
                  f"likely needs extra form fields. Stopping here; page 1 "
                  f"data is still good.", file=sys.stderr)
            break
        job_urls.extend(new)

    if limit:
        job_urls = job_urls[:limit]

    # --- Detail pages + company pages (cached, once per company) ---------
    jobs, company_cache = [], {}
    for n, url in enumerate(job_urls, 1):
        time.sleep(delay)
        print(f"[2/3] Job detail {n}/{len(job_urls)} ...")
        r = _request(session, "GET", url, verify=verify)
        job = parse_detail(BeautifulSoup(r.text, "html.parser"), url)

        curl = job["company_url"]
        if curl and curl not in company_cache:
            time.sleep(delay)
            print(f"[3/3] Company page: {job['company_name'] or curl}")
            cr = _request(session, "GET", curl, verify=verify)
            company_cache[curl] = parse_company(
                BeautifulSoup(cr.text, "html.parser"), job["company_name"])
        if curl in company_cache:
            job.update(company_cache[curl])

        jobs.append(job)

    return jobs


# ---------------------------------------------------------------------------
# CLI for standalone testing (run this before wiring into pipeline.py)
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="PhilJobNet collector")
    ap.add_argument("--pages", type=int, default=2,
                    help="listing pages to scrape (10 jobs each)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap on job detail pages fetched (smoke tests)")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help="seconds between requests")
    ap.add_argument("--csv", default="philjobnet_jobs.csv",
                    help="output CSV path")
    ap.add_argument("--insecure", action="store_true",
                    help="skip SSL verification (only if you hit SSLError "
                         "on the gov cert)")
    args = ap.parse_args()

    jobs = collect(pages=args.pages, limit=args.limit, delay=args.delay,
                   verify=not args.insecure)

    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELD_ORDER)
        w.writeheader()
        w.writerows(jobs)

    companies = dedupe_by_company(jobs)
    print(f"\nDone: {len(jobs)} jobs -> {len(companies)} unique companies "
          f"-> {args.csv}")
    for c in companies[:5]:
        print(f"  - {c['company_name']} [{c['company_id']}] | "
              f"{c['employment_size'] or 'size: blank'} | "
              f"{c['job_count']} job(s)")


if __name__ == "__main__":
    main()