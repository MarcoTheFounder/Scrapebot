"""
enrichers/website.py — Step 1 of the enrichment chain.

WHAT IT DOES
------------
1. Takes a company name + city
2. Searches Google (via Serper API) for their website
3. Scrapes the website's /contact, /about, /team pages
4. Extracts emails and Philippine phone numbers

WHY SERPER INSTEAD OF DUCKDUCKGO
--------------------------------
DuckDuckGo blocks headless servers (like the Raspberry Pi) by IP.
Serper is an API — it works from any server, returns clean JSON,
and has a free tier of 2,500 searches/month. Same job as DDG
(find the company website), just through a reliable channel.

HARD-BLANK RULE
---------------
If nothing is found, returns empty strings. Never guesses.
A blank result is correct behavior, not a failure.
"""

import os
import re
import time

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from enrichers.match import is_plausible_match

load_dotenv()

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL = "https://google.serper.dev/search"

CONTACT_PATHS = ["/contact", "/contact-us", "/about", "/about-us", "/team", "/people"]

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
    Search Google (via Serper) for the company's official website.
    Returns a URL string or "" if nothing credible found.

    Uses is_plausible_match() to reject results whose domain doesn't
    resemble the company name (directories, marketplaces, etc.).
    """
    if not SERPER_API_KEY:
        print("  [!] SERPER_API_KEY not set in .env — skipping website search.")
        return ""

    query = f'"{company_name}" {city} official website Philippines'.strip()
    try:
        r = requests.post(
            SERPER_URL,
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "gl": "ph", "num": 5},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"  [!] Serper returned {r.status_code} for {company_name}")
            return ""

        results = r.json().get("organic", [])
        for result in results:
            url = result.get("link", "")
            if url and is_plausible_match(company_name, url):
                match = re.match(r"(https?://[^/]+)", url)
                return match.group(1) if match else url
    except Exception as e:
        print(f"  [!] Serper search failed for {company_name}: {e}")
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

        good_emails = [e for e in emails if not any(
            e.startswith(p) for p in ("noreply", "no-reply", "donotreply")
        )]

        email = good_emails[0] if good_emails else (emails[0] if emails else "")
        phone = phones[0] if phones else ""

        if email or phone:
            return email, phone

        time.sleep(0.5)

    return "", ""


def enrich(company):
    """
    Main entry point called by pipeline.py.
    Always returns the dict — blanks if nothing found (hard-blank rule).
    """
    name = company.get("company_name", "")
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


if __name__ == "__main__":
    test = {
        "company_name": "Jollibee Foods Corporation",
        "company_address": "Pasig City, Metro Manila",
    }
    print("Testing website enricher (Serper)...")
    result = enrich(test)
    print(f"Website:  {result['website']}")
    print(f"Email:    {result['email'] or '(blank — hard-blank rule)'}")
    print(f"Phone:    {result['phone'] or '(blank — hard-blank rule)'}")
    print(f"Source:   {result['email_source'] or '(none)'}")