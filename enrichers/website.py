"""
enrichers/website.py — Step 1 of the enrichment chain.

WHAT IT DOES
------------
1. Takes a company name + city
2. Searches DuckDuckGo for their website
3. Scrapes the website's /contact, /about, /team pages
4. Extracts emails and Philippine phone numbers

HARD-BLANK RULE
---------------
If nothing is found, returns empty strings. Never guesses.
A blank result is correct behavior, not a failure.

PH PHONE FORMAT
---------------
Matches: +63-917-123-4567, 09171234567, (02) 8123-4567
Does NOT match: random number strings, foreign numbers
"""

import re
import time

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from enrichers.match import is_plausible_match

# Pages most likely to have contact info — checked in this order
CONTACT_PATHS = ["/contact", "/contact-us", "/about", "/about-us", "/team", "/people"]

# Philippine mobile: 09XX or +639XX
# Philippine landline: (02) or +632 followed by 7-8 digits
PH_PHONE_RE = re.compile(
    r"(?:\+63|0)(?:9\d{9}|2[\s\-]\d{4}[\s\-]\d{4}|[2-9]\d{8,9})"
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def find_website(company_name, city=""):
    """
    Search DuckDuckGo for the company's official website.
    Returns a URL string or "" if nothing credible found.

    Uses is_plausible_match() instead of a static blacklist — checks
    whether each candidate domain's name actually resembles the company
    name, rather than maintaining a growing list of known-bad sites.
    """
    query = f'"{company_name}" {city} official website Philippines'.strip()
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        for r in results:
            url = r.get("href", "")
            if url and is_plausible_match(company_name, url):
                # Return just the base domain
                match = re.match(r"(https?://[^/]+)", url)
                return match.group(1) if match else url
    except Exception:
        pass
    return ""


def _fetch(url, timeout=10):
    """GET a page, return BeautifulSoup or None on failure."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return BeautifulSoup(r.text, "html.parser")
    except Exception:
        pass
    return None


def _extract_contacts(soup):
    """Pull emails and PH phone numbers from a parsed page."""
    text = soup.get_text(" ", strip=True)
    emails = [e for e in EMAIL_RE.findall(text)
              if not e.endswith((".png", ".jpg", ".gif", ".svg"))]
    phones = PH_PHONE_RE.findall(text)
    return emails, phones


def scrape_website(base_url):
    """
    Try the homepage + common contact paths.
    Returns (email, phone) — first credible match wins, blanks if nothing found.
    """
    if not base_url:
        return "", ""

    pages_to_try = [base_url] + [base_url.rstrip("/") + p for p in CONTACT_PATHS]
    seen = set()

    for url in pages_to_try:
        if url in seen:
            continue
        seen.add(url)

        soup = _fetch(url)
        if not soup:
            continue

        emails, phones = _extract_contacts(soup)

        # Prefer non-generic emails (skip noreply@, support@ as first choice)
        good_emails = [e for e in emails if not any(
            e.startswith(p) for p in ("noreply", "no-reply", "donotreply")
        )]

        email = good_emails[0] if good_emails else (emails[0] if emails else "")
        phone = phones[0] if phones else ""

        if email or phone:
            return email, phone

        time.sleep(0.5)  # brief pause between page attempts

    return "", ""


def enrich(company):
    """
    Main entry point called by pipeline.py.

    Input:  company dict (must have 'company_name', optionally 'company_address')
    Output: same dict with 'website', 'email', 'phone', 'email_source' added

    Always returns the dict — blanks if nothing found (hard-blank rule).
    """
    name = company.get("company_name", "")
    # Extract city from address — first segment before the comma
    address = company.get("company_address", "")
    city = address.split(",")[0].strip() if address else ""

    website = find_website(name, city)
    company["website"] = website

    if website:
        email, phone = scrape_website(website)
        company["email"] = email
        company["phone"] = phone
        company["email_source"] = "website" if email else ""
    else:
        company["email"] = ""
        company["phone"] = ""
        company["email_source"] = ""

    return company


# Standalone test
if __name__ == "__main__":
    test = {
        "company_name": "Jollibee Foods Corporation",
        "company_address": "Pasig City, Metro Manila",
    }
    print("Testing website enricher...")
    result = enrich(test)
    print(f"Website:  {result['website']}")
    print(f"Email:    {result['email'] or '(blank — hard-blank rule)'}")
    print(f"Phone:    {result['phone'] or '(blank — hard-blank rule)'}")
    print(f"Source:   {result['email_source'] or '(none)'}")