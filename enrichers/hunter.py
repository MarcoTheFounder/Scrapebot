"""
enrichers/hunter.py — Step 2 of the enrichment chain.

WHAT IT DOES
------------
Only called when website.py found no email.
Takes the company website domain, sends it to Hunter.io's API,
and returns the most relevant email found.

HARD-BLANK RULE
---------------
If Hunter returns nothing, or the API call fails, returns "".
Never guesses. Never infers. Empty string = honest result.

QUOTA
-----
Hunter.io free tier: 25 searches/month.
Pipeline only calls this when website scraper already failed —
so it's used sparingly, not on every company.
"""

import os
import re

import requests
from dotenv import load_dotenv

from enrichers.match import is_plausible_match

load_dotenv()

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
HUNTER_URL = "https://api.hunter.io/v2/domain-search"

# Preferred email types from Hunter results — in priority order
PREFERRED_TYPES = ["hr", "human resources", "info", "admin", "careers", "recruitment"]


def _extract_domain(url):
    """Strip protocol and path from a URL to get just the domain."""
    url = url.lower().strip()
    url = re.sub(r"^https?://", "", url)
    url = url.split("/")[0]
    return url


def _pick_best_email(emails):
    """
    From Hunter's list of emails, pick the most useful one.
    Prefers HR/info/admin over random staff emails.
    Falls back to first result if none match preferred types.
    """
    if not emails:
        return ""

    for preferred in PREFERRED_TYPES:
        for e in emails:
            value = e.get("value", "")
            dept = (e.get("department") or "").lower()
            local = value.split("@")[0].lower()
            if preferred in dept or preferred in local:
                return value

    # Fall back to first email Hunter returned
    return emails[0].get("value", "")


def enrich(company):
    """
    Main entry point called by pipeline.py.

    Only runs if company['email'] is still blank after website enricher.
    Input:  company dict (must have 'website')
    Output: same dict with 'email' and 'email_source' updated if found.

    Always returns the dict — hard-blank rule applies.
    """
    # Skip if we already have an email
    if company.get("email"):
        return company

    # Skip if no API key configured
    if not HUNTER_API_KEY:
        print("  [!] HUNTER_API_KEY not set in .env — skipping Hunter enrichment.")
        return company

    website = company.get("website", "")
    if not website:
        return company

    domain = _extract_domain(website)
    if not domain:
        return company

    company_name = company.get("company_name", "")
    if not is_plausible_match(company_name, domain):
        print(f"  [hunter] Domain {domain} doesn't match company name "
              f"{company_name!r} — skipping, leaving blank.")
        return company

    try:
        r = requests.get(
            HUNTER_URL,
            params={"domain": domain, "api_key": HUNTER_API_KEY},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"  [!] Hunter API returned {r.status_code} for {domain}")
            return company

        data = r.json().get("data", {})
        emails = data.get("emails", [])
        email = _pick_best_email(emails)

        if email:
            company["email"] = email
            company["email_source"] = "hunter"
            print(f"  [hunter] Found: {email}")
        else:
            print(f"  [hunter] No emails found for {domain} — leaving blank.")

    except Exception as e:
        print(f"  [!] Hunter request failed for {domain}: {e}")

    return company


# Standalone test
if __name__ == "__main__":
    test = {
        "company_name": "Ayala Corporation",
        "website": "https://www.ayala.com.ph",
        "email": "",  # blank so Hunter actually runs
    }
    print("Testing Hunter.io enricher...")
    if not HUNTER_API_KEY:
        print("ERROR: HUNTER_API_KEY not found in .env")
    else:
        result = enrich(test)
        print(f"Email:  {result['email'] or '(blank — hard-blank rule)'}")
        print(f"Source: {result['email_source'] or '(none)'}")