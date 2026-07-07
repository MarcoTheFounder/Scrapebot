"""
enrichers/match.py — shared logic for verifying a domain actually belongs
to the company we're enriching.

WHY THIS EXISTS
---------------
DuckDuckGo and Hunter.io both sometimes return domains that mention a
company without BEING that company's website — business directories
(info-clipper, EMIS, ZoomInfo), marketplace listings (Lazada, Shopee),
news articles, etc. A static blacklist of bad domains doesn't scale —
we'd be adding names to it forever, one bad match at a time.

Instead: check whether the domain's name plausibly matches the company's
name. No AI involved — this is pure string comparison (difflib), the
same category of logic as a spell-checker. It cannot "decide" to invent
a match; it can only measure how similar two strings already are.

USED BY
-------
- enrichers/website.py — filters DuckDuckGo search results
- enrichers/hunter.py  — filters which Hunter-found emails to trust
"""

import re
from difflib import SequenceMatcher

# Suffixes that don't help identify a company — stripped before comparing
LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "llc", "phils", "philippines", "ph",
}

# Domains that are NEVER a company's own site, regardless of name match —
# kept small and only for cases where name-similarity alone isn't enough
# (e.g. "Lazada Philippines Store" really would look similar to lazada.com)
HARD_SKIP = {
    "facebook.com", "linkedin.com", "twitter.com", "instagram.com",
    "youtube.com", "tiktok.com",
    "jobstreet.com", "indeed.com", "philjobnet.gov.ph", "kalibrr.com",
    "bossjob.ph", "onlinejobs.ph", "jobsdb.com",
    "lazada.com.ph", "shopee.ph", "carousell.ph",
    "wikipedia.org", "google.com", "bing.com",
}

# Minimum similarity score (0.0–1.0) to accept a domain as the real company site.
# Tuned against real false-positives: generic multi-word company names can
# coincidentally share ~0.35 letter-overlap with unrelated domains
# (e.g. "Abenson Ventures" vs "marketinsidedata" scored 0.39). 0.5 is the
# lowest threshold that still passes all known-good cases while rejecting
# known-bad ones — see enrichers/match.py test cases below.
SIMILARITY_THRESHOLD = 0.5


def _normalize(text):
    """Lowercase, strip legal suffixes, keep only letters/numbers."""
    text = text.lower()
    words = re.findall(r"[a-z0-9]+", text)
    words = [w for w in words if w not in LEGAL_SUFFIXES]
    return "".join(words)


def _domain_root(url_or_domain):
    """Extract just the domain name, no protocol/path/TLD."""
    d = url_or_domain.lower().strip()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    d = d.split("/")[0]
    d = re.sub(r"\.(com|ph|net|org|co|io|info)(\.ph)?$", "", d)
    return d


def is_plausible_match(company_name, domain_or_url):
    """
    Returns True if the domain plausibly belongs to this company.

    Examples:
        is_plausible_match("Optum Global Solutions", "optum.com")        -> True
        is_plausible_match("Homemaker Furniture Corp", "lazada.com.ph")  -> False
        is_plausible_match("UDMC Weapons Inc", "udmc-weapons.com")       -> True
        is_plausible_match("ABC Trading Co", "info-clipper.com")        -> False

    No AI — pure string similarity (difflib.SequenceMatcher).
    Cannot hallucinate a match; can only measure existing overlap.
    """
    if not company_name or not domain_or_url:
        return False

    full_domain = domain_or_url.lower()
    for skip in HARD_SKIP:
        if skip in full_domain:
            return False

    clean_name = _normalize(company_name)
    clean_domain = _normalize(_domain_root(domain_or_url))

    if not clean_name or not clean_domain:
        return False

    # Direct containment is a strong, cheap signal (e.g. "udmcweapons" in domain)
    if clean_domain in clean_name or clean_name in clean_domain:
        return True

    # Acronym check: many PH SMEs use initials as their domain
    # (e.g. "United Defense Manufacturing Corp" -> "UDMC" -> udmc-weapons.com)
    words = re.findall(r"[a-z0-9]+", company_name.lower())
    words = [w for w in words if w not in LEGAL_SUFFIXES]
    if len(words) >= 2:
        acronym = "".join(w[0] for w in words)
        if len(acronym) >= 3 and clean_domain.startswith(acronym):
            return True

    score = SequenceMatcher(None, clean_name, clean_domain).ratio()
    return score >= SIMILARITY_THRESHOLD


def match_score(company_name, domain_or_url):
    """Debug helper — returns the raw similarity score (0.0-1.0)."""
    clean_name = _normalize(company_name)
    clean_domain = _normalize(_domain_root(domain_or_url))
    if not clean_name or not clean_domain:
        return 0.0
    return SequenceMatcher(None, clean_name, clean_domain).ratio()


# Standalone test
if __name__ == "__main__":
    cases = [
        ("Optum Global Solutions (Philippines), Inc.", "optum.com", True),
        ("Homemaker Furniture Corporation", "lazada.com.ph", False),
        ("UDMC Weapons Inc", "udmc-weapons.com", True),
        ("ABC Trading Co", "info-clipper.com", False),
        ("Jollibee Foods Corporation", "jollibeegroup.com", True),
        ("Dames International Corporation", "lazada.com.ph", False),
        ("Abenson Ventures, Inc.", "marketinsidedata.com", False),
        ("United Defense Manufacturing Corp", "udmc-weapons.com", True),
    ]
    print("Testing domain match logic...\n")
    for name, domain, expected in cases:
        result = is_plausible_match(name, domain)
        score = match_score(name, domain)
        status = "PASS" if result == expected else "FAIL"
        print(f"  {status}  {name!r} vs {domain!r}")
        print(f"        -> match={result} (expected {expected}), score={score:.2f}")