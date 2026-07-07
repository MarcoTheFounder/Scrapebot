"""
collectors/jobstreet.py — scrapes JobStreet Philippines for active job postings.

HOW IT WORKS
------------
JobStreet PH (ph.jobstreet.com) is built on SEEK's platform. Their search
page loads job data via a GraphQL API endpoint, not plain HTML. We call
that same endpoint directly — no browser, no Jina.ai needed.

The endpoint returns clean JSON with company name, location, industry,
and job details already structured. Much simpler than PhilJobNet's
ASP.NET postback pagination.

TOS NOTE
--------
JobStreet prohibits scraping in their ToS. Keep volume low (weekly runs,
~5 pages max), keep delays polite, and use output internally only.
This is meaningfully lower risk than LinkedIn but not zero risk.

OUTPUT SCHEMA (one dict per job — same structure as philjobnet.py)
------------------------------------------------------------------
position_title, job_id, job_url, posted_date, salary, work_location,
company_name, company_id, company_url, company_address,
employment_size, industry

Use dedupe_by_company() from philjobnet.py to collapse to one record
per company before enrichment.

STANDALONE TEST
---------------
    python collectors/jobstreet.py --pages 2 --limit 5
"""

import argparse
import csv
import re
import sys
import time

import requests

BASE_URL = "https://ph.jobstreet.com"

# JobStreet's internal search API — same endpoint the browser calls
SEARCH_URL = f"{BASE_URL}/api/chalice-search/v4/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-PH,en;q=0.9",
    "Referer": "https://ph.jobstreet.com/jobs",
}

DEFAULT_DELAY = 2.0
PAGE_SIZE = 10  # 10 results per page, same as PhilJobNet

FIELD_ORDER = [
    "position_title", "job_id", "job_url", "posted_date", "salary",
    "work_location", "company_name", "company_id", "company_url",
    "company_address", "employment_size", "industry",
]


def _build_params(page=1):
    """Build query params for the JobStreet search API."""
    return {
        "siteKey": "PH-Main",
        "sourcesystem": "houston",
        "userqueryid": "",
        "userid": "",
        "usersessionid": "",
        "eventCaptureSessionId": "",
        "where": "philippines",
        "page": page,
        "pageSize": PAGE_SIZE,
        "include": "seodata",
        "locale": "en-PH",
        "solrEnabled": "true",
    }


def _parse_job(raw):
    """
    Extract clean fields from a single JobStreet job result dict.
    Hard-blank rule: any field we can't confirm stays "".
    """
    job = {k: "" for k in FIELD_ORDER}

    job["job_id"] = str(raw.get("id", ""))
    job["position_title"] = raw.get("title", "")

    # Build job URL from ID
    title_slug = re.sub(r"[^a-z0-9]+", "-", job["position_title"].lower()).strip("-")
    if job["job_id"]:
        job["job_url"] = f"{BASE_URL}/job/{title_slug}-{job['job_id']}"

    # Company info
    advertiser = raw.get("advertiser", {}) or {}
    job["company_name"] = advertiser.get("description", "")
    job["company_id"] = str(advertiser.get("id", ""))
    if job["company_id"]:
        job["company_url"] = f"{BASE_URL}/companies/{job['company_id']}"

    # Location
    location = raw.get("jobLocation", {}) or {}
    label = location.get("label", "")
    if label:
        job["work_location"] = label

    # Salary — JobStreet sometimes includes salary range
    salary = raw.get("salary", "") or ""
    if salary:
        job["salary"] = salary

    # Posted date
    listing_date = raw.get("listingDate", "") or raw.get("listingDateDisplay", "")
    if listing_date:
        job["posted_date"] = listing_date[:10]  # keep YYYY-MM-DD only

    # Industry / classification
    classification = raw.get("classification", {}) or {}
    job["industry"] = classification.get("description", "")

    return job


def _request(session, params, verify=True):
    """GET the search API with retries."""
    last = None
    for attempt in range(3):
        try:
            r = session.get(
                SEARCH_URL, params=params, timeout=30, verify=verify
            )
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code == 403:
                print(
                    "  [!] 403 Forbidden — JobStreet may be blocking the request. "
                    "Try increasing delay or running from a different IP.",
                    file=sys.stderr,
                )
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed after 3 attempts: {last}")


def collect(pages=2, limit=None, delay=DEFAULT_DELAY, verify=True):
    """
    Main entry point — scrape JobStreet PH listings.
    Returns list of job dicts (same schema as philjobnet.collect()).
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    jobs = []
    job_ids_seen = set()

    for page in range(1, pages + 1):
        print(f"[1/1] Fetching JobStreet page {page}...")
        params = _build_params(page)

        try:
            r = _request(session, params, verify=verify)
        except RuntimeError as e:
            print(f"  [!] {e}", file=sys.stderr)
            break

        try:
            data = r.json()
        except Exception:
            print(
                f"  [!] Page {page} didn't return valid JSON. "
                "JobStreet may have changed their API. "
                "Saving raw response to debug_jobstreet.html.",
                file=sys.stderr,
            )
            with open("debug_jobstreet.html", "w", encoding="utf-8") as f:
                f.write(r.text)
            break

        results = data.get("data", []) or []
        if not results:
            print(f"  [!] No results on page {page} — stopping.")
            break

        new = 0
        for raw in results:
            job = _parse_job(raw)
            if not job["job_id"] or job["job_id"] in job_ids_seen:
                continue
            job_ids_seen.add(job["job_id"])
            jobs.append(job)
            new += 1

        print(f"      page {page}: {new} new jobs")

        if limit and len(jobs) >= limit:
            jobs = jobs[:limit]
            break

        time.sleep(delay)

    return jobs


def dedupe_by_company(jobs):
    """
    Collapse job rows to one record per company.
    Mirrors philjobnet.dedupe_by_company() — same contract.
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


# CLI for standalone testing
def main():
    ap = argparse.ArgumentParser(description="JobStreet PH collector")
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    ap.add_argument("--csv", default="jobstreet_jobs.csv")
    ap.add_argument("--insecure", action="store_true")
    args = ap.parse_args()

    jobs = collect(
        pages=args.pages, limit=args.limit,
        delay=args.delay, verify=not args.insecure
    )

    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELD_ORDER)
        w.writeheader()
        w.writerows(jobs)

    companies = dedupe_by_company(jobs)
    print(f"\nDone: {len(jobs)} jobs -> {len(companies)} unique companies -> {args.csv}")
    for c in companies[:5]:
        print(
            f"  - {c['company_name']} [{c['company_id']}] | "
            f"{c['industry'] or 'industry: blank'} | "
            f"{c['job_count']} job(s)"
        )


if __name__ == "__main__":
    main()