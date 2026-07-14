"""
pipeline.py — main orchestrator for the SyncTalents Leads Scraper.

FLOW
----
1. Scrape PhilJobNet for active job postings
2. Deduplicate by company (one record per company)
3. Enrich each company with contact details:
   - Step 3a: website search (Serper) + contact page scrape
   - Step 3b: Hunter.io (only if website found nothing)
4. Write results to Google Sheets

USAGE
-----
    python pipeline.py                  # 2 pages, all companies
    python pipeline.py --pages 5        # more pages, more leads
    python pipeline.py --pages 1 --limit 3   # quick smoke test

HARD-BLANK RULE
---------------
Any field that can't be confirmed stays blank. No guessing.
"""

import argparse
import time

from collectors.philjobnet import collect, dedupe_by_company
from enrichers.website import enrich as website_enrich
from enrichers.hunter import enrich as hunter_enrich
from sheets_writer import write_leads

ENRICHMENT_DELAY = 2  # seconds between companies — avoids rate limiting


def run(pages=2, limit=None):
    print("\n=== STEP 1: Scraping PhilJobNet ===")
    jobs = collect(pages=pages, limit=limit)
    print(f"Total jobs scraped: {len(jobs)}")

    print("\n=== STEP 2: Deduplicating by company ===")
    companies = dedupe_by_company(jobs)
    print(f"Unique companies: {len(companies)}")

    print("\n=== STEP 3: Enriching companies ===")
    enriched = []
    for i, company in enumerate(companies, 1):
        name = company.get("company_name", "Unknown")
        print(f"[{i}/{len(companies)}] {name}")

        # Step 3a: try website first (Serper search + contact page scrape)
        company = website_enrich(company)

        # Step 3b: if no email yet, try Hunter
        if not company.get("email"):
            company = hunter_enrich(company)

        email = company.get("email", "")
        source = company.get("email_source", "")
        if email:
            print(f"  ✓ Email found ({source}): {email}")
        else:
            print(f"  — No email found (blank — hard-blank rule)")

        enriched.append(company)
        time.sleep(ENRICHMENT_DELAY)

    print("\n=== STEP 4: Writing to Google Sheets ===")
    write_leads(enriched)

    total = len(enriched)
    with_email = sum(1 for c in enriched if c.get("email"))
    with_phone = sum(1 for c in enriched if c.get("phone"))

    print(f"\n=== DONE ===")
    print(f"Companies processed : {total}")
    print(f"Emails found        : {with_email} ({round(with_email/total*100) if total else 0}% coverage)")
    print(f"Phones found        : {with_phone} ({round(with_phone/total*100) if total else 0}% coverage)")
    print(f"Blanks (no email)   : {total - with_email} — honest zeroes, not guesses")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SyncTalents Leads Scraper")
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(pages=args.pages, limit=args.limit)