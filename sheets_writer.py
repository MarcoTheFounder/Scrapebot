"""
sheets_writer.py — takes the deduplicated company list and writes it to Google Sheets.

HOW IT WORKS
------------
1. Logs in to Google using the credentials.json service account key
2. Opens the Google Sheet by ID
3. Clears the existing data (so we don't get duplicates on re-runs)
4. Writes a header row + one row per company
"""

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1gSHiQUEK3O2PNAtccHxzOvqUhfae5OoyNBqBTsKGVlI"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Company Name",
    "Company ID",
    "Industry",
    "Employment Size",
    "Address",
    "# of Job Postings",
    "Sample Positions",
    "Company URL",
    "Email",
    "Contact Name",
    "Contact Role",
    "Phone",
    "Email Source",
]


def get_sheet():
    """Authenticate and return the first sheet of the spreadsheet."""
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet.sheet1


def write_leads(companies):
    """
    Write deduplicated company list to Google Sheets.
    Clears existing data first so re-runs don't stack up duplicates.
    """
    sheet = get_sheet()
    sheet.clear()
    sheet.append_row(HEADERS)

    rows = []
    for c in companies:
        rows.append([
            c.get("company_name", ""),
            c.get("company_id", ""),
            c.get("industry", ""),
            c.get("employment_size", ""),
            c.get("company_address", ""),
            c.get("job_count", 0),
            c.get("sample_positions", ""),
            f'=HYPERLINK("{c.get("company_url", "")}","View Profile")' if c.get("company_url") else "",
            c.get("email", ""),
            c.get("contact_name", ""),
            c.get("contact_role", ""),
            "'" + c.get("phone", "") if c.get("phone") else "",
            c.get("email_source", ""),
        ])

    if rows:
        sheet.append_rows(rows, value_input_option="USER_ENTERED")

    print(f"Written {len(rows)} companies to Google Sheets.")


if __name__ == "__main__":
    print("Testing Google Sheets connection...")
    sheet = get_sheet()
    print(f"Connected! Sheet title: {sheet.title}")
    print("Connection works. Ready to write leads.")