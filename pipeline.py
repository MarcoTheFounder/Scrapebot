"""
pipeline.py — main orchestrator for the SyncTalents Leads Scraper.

WHAT IT DOES
------------
1. Scrapes PhilJobNet for active job postings
2. Deduplicates by company (one record per company)
3. Enriches each company with contact details:
   - Step 1: DuckDuckGo + website scraper
   - Step 2: Hunter.io (only if Step 1 found nothing)
4. Writes results to Google Sheets

USAGE
-----
Full run (2 pages, all companies):
    python pipeline.py

Quick smoke test (1 page, 5 companies):
    python pipeline.py --pages 1 --limit 5

More pages = more leads but slower:
    python pipeline.py --pages 5

HARD-BLANK RULE
---------------
Any field that can't be confirmed stays blank.
No guessing. No inferring. Empty = honest.
"""

import argparse
import time

from collectors.philjobnet import collect, dedupe_by_company
from enrichers.website import enrich as website_enrich
from enrichers.hunter import enrich as hunter_enrich
from sheets_writer import write_leads

ENRICHMENT_DELAY = 2  # seconds between companies — avoids rate limiting


def run(pages=2, limit=None):
    # ------------------------------------------------------------------ #
    # Step 1 — Scrape PhilJobNet
    # ------------------------------------------------------------------ #
    print("\n=== STEP 1: Scraping PhilJobNet ===")
    jobs = collect(pages=pages, limit=limit)
    print(f"Total jobs scraped: {len(jobs)}")

    # ------------------------------------------------------------------ #
    # Step 2 — Deduplicate by company
    # ------------------------------------------------------------------ #
    print("\n=== STEP 2: Deduplicating by company ===")
    companies = dedupe_by_company(jobs)
    print(f"Unique companies: {len(companies)}")

    # ------------------------------------------------------------------ #
    # Step 3 — Enrich each company
    # ------------------------------------------------------------------ #
    print("\n=== STEP 3: Enriching companies ===")
    enriched = []
    for i, company in enumerate(companies, 1):
        name = company.get("company_name", "Unknown")
        print(f"[{i}/{len(companies)}] {name}")

        # Step 3a: try website first
        company = website_enrich(company)

        # Step 3b: if no email yet, try Hunter
        if not company.get("email"):
            company = hunter_enrich(company)

        # Log result
        email = company.get("email", "")
        source = company.get("email_source", "")
        if email:
            print(f"  ✓ Email found ({source}): {email}")
        else:
            print(f"  — No email found (blank — hard-blank rule)")

        enriched.append(company)
        time.sleep(ENRICHMENT_DELAY)

    # ------------------------------------------------------------------ #
    # Step 4 — Write to Google Sheets
    # ------------------------------------------------------------------ #
    print("\n=== STEP 4: Writing to Google Sheets ===")
    write_leads(enriched)

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #
    total = len(enriched)
    with_email = sum(1 for c in enriched if c.get("email"))
    with_phone = sum(1 for c in enriched if c.get("phone"))

    print(f"\n=== DONE ===")
    print(f"Companies processed : {total}")
    print(f"Emails found        : {with_email} ({round(with_email/total*100)}% coverage)")
    print(f"Phones found        : {with_phone} ({round(with_phone/total*100)}% coverage)")
    print(f"Blanks (no email)   : {total - with_email} — honest zeroes, not guesses")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SyncTalents Leads Scraper")
    ap.add_argument("--pages", type=int, default=2,
                    help="PhilJobNet listing pages to scrape (10 jobs each)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap on companies to enrich (for smoke tests)")
    args = ap.parse_args()

    run(pages=args.pages, limit=args.limit)