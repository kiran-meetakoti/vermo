"""Bank/credit-card statement PDF parsing, extracted from streamlit_app.py.

No Streamlit dependency, so tests can import it directly (streamlit_app.py
executes login and page rendering at import time — see docs/DEVELOPMENT.md,
"The testability rule"). streamlit_app.py imports extract_pdf_transactions
from here; everything else is an implementation detail of that function.

This parser produced both real duplicate-transaction incidents before it had
tests — treat changes here as high-risk and extend tests/test_statement_parser.py
alongside any change.
"""

from __future__ import annotations

import re
from datetime import date

from pypdf import PdfReader


# ── Expense PDF import (bank / credit card statements) ─────────────────────
EXPENSE_CATEGORY_KEYWORDS: dict[str, str] = {
    "rewe": "Groceries", "edeka": "Groceries", "aldi": "Groceries", "lidl": "Groceries",
    "kaufland": "Groceries", "supermarkt": "Groceries", "penny": "Groceries", "netto": "Groceries",
    "dmart": "Groceries", "bigbasket": "Groceries", "zepto": "Groceries", "blinkit": "Groceries",
    "netflix": "Subscriptions", "spotify": "Subscriptions", "disney": "Subscriptions",
    "amazon prime": "Subscriptions", "youtube premium": "Subscriptions", "icloud": "Subscriptions",
    "hotstar": "Subscriptions", "apple.com/bill": "Subscriptions",
    "uber": "Transport", "ola": "Transport", "bahn": "Transport", "deutsche bahn": "Transport",
    "taxi": "Transport", "tankstelle": "Transport", "shell": "Transport", "aral": "Transport",
    "esso": "Transport", "petrol": "Transport", "fuel": "Transport", "irctc": "Transport",
    "restaurant": "Eating out", "café": "Eating out", "cafe": "Eating out", "mcdonald": "Eating out",
    "burger": "Eating out", "swiggy": "Eating out", "zomato": "Eating out", "starbucks": "Eating out",
    "miete": "Rent", "rent": "Rent", "rdmiete": "Rent",
    "amazon": "Shopping", "zalando": "Shopping", "h&m": "Shopping", "ikea": "Shopping",
    "myntra": "Shopping", "flipkart": "Shopping", "decathlon": "Shopping",
    "versicherung": "Insurance", "insurance": "Insurance", "allianz": "Insurance",
    "flug": "Travel", "airline": "Travel", "hotel": "Travel", "booking.com": "Travel",
    "makemytrip": "Travel", "airbnb": "Travel", "indigo": "Travel", "lufthansa": "Travel",
    "strom": "Utilities", "stadtwerke": "Utilities", "electricity": "Utilities",
    "telekom": "Utilities", "vodafone": "Utilities", "internet": "Utilities", "wifi": "Utilities",
}

# Matches a leading date in DD.MM.YYYY, DD/MM/YYYY, YYYY-MM-DD, or "DD Mon YYYY" form,
# then a description, then a trailing amount (optionally with a currency symbol,
# thousands separators, and a leading or trailing minus sign for debits).
_PDF_LINE_RE = re.compile(
    r"^\s*(?P<date>\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})"
    r"\s+(?P<description>.+?)\s+"
    r"(?P<sign>[-+]?)\s*[€$₹]?\s*(?P<amount>\d+(?:[.,]\d{3})*[.,]\d{2})\s*(?P<trailing_sign>-?)\s*$"
)
_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "mai": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12,
}


def _parse_statement_date(raw: str) -> date | None:
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return date.fromisoformat(raw)
    match = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{2,4})$", raw)
    if match:
        day, month, year = match.groups()
        year_int = int(year) if len(year) == 4 else 2000 + int(year)
        try:
            return date(year_int, int(month), int(day))
        except ValueError:
            return None
    match = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{2,4})$", raw)
    if match:
        day, month_name, year = match.groups()
        month_num = _MONTH_NAMES.get(month_name.lower()[:3])
        if not month_num:
            return None
        year_int = int(year) if len(year) == 4 else 2000 + int(year)
        try:
            return date(year_int, month_num, int(day))
        except ValueError:
            return None
    return None


def _parse_statement_amount(raw: str) -> float | None:
    cleaned = raw.strip()
    if "," in cleaned and "." in cleaned:
        # Whichever separator appears last is the decimal point.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def auto_categorize_expense(description: str) -> str:
    lowered = description.lower()
    for keyword, category in EXPENSE_CATEGORY_KEYWORDS.items():
        if keyword in lowered:
            return category
    return "Other"


# N26 (and similar EU neobank) statements lay each transaction out across several
# lines rather than one — description line(s), then "<card> • <N26 category>",
# then optional FX conversion lines, then "Wertstellung DD.MM.YYYY", then the
# actual "DD.MM.YYYY ±amount€" line that ends the block. This block parser
# handles that shape and reuses N26's own category labels when present, which
# is more reliable than guessing from the merchant name alone.
N26_CATEGORY_MAP = {
    "Auto": "Transport",
    "Bars & Restaurants": "Eating out",
    "Berufsausgaben": "Other",
    "Freizeit": "Other",
    "Geldautomat": "Other",
    "Gesundheit & Drogerien": "Shopping",
    "Lebensmittel": "Groceries",
    "Medien & Telekom": "Utilities",
    "Shopping": "Shopping",
    "Sonstiges": "Other",
    "Transport": "Transport",
    "Versicherung": "Insurance",
    "Wohnen & Energie": "Utilities",
}

_STATEMENT_BLOCK_TERMINATOR_RE = re.compile(
    r"Wertstellung\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}\s*\n"
    r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})\s+([+-]?\d[\d.,]*)\s*€"
)
# .*? not .+?: the card line usually carries an account-name prefix
# ("Hauptkonto Mastercard • …") but must also be recognized when it starts
# with "Mastercard" directly — otherwise it leaks into the description.
_CARD_CATEGORY_LINE_RE = re.compile(r"^.*?\bMastercard\b(?:\s*[•·]\s*(.+))?$")


def _strip_statement_page_boilerplate(page_text: str) -> str:
    """Drop the repeated header/footer N26 prints on every page so they don't
    get glued onto the description of whichever transaction sits next to a
    page break once all pages are concatenated for block parsing."""
    lines = page_text.split("\n")
    for i, line in enumerate(lines):
        if "Beschreibung" in line and "Betrag" in line:
            lines = lines[i + 1:]
            break
    for i, line in enumerate(lines):
        if line.strip().startswith("Kontoauszug Nr."):
            lines = lines[:i]
            break
    return "\n".join(lines)


def _extract_statement_blocks(full_text: str) -> list[dict]:
    rows = []
    prev_end = 0
    for match in _STATEMENT_BLOCK_TERMINATOR_RE.finditer(full_text):
        block_text = full_text[prev_end:match.start()]
        prev_end = match.end()
        amount_raw = match.group(2).strip()
        if amount_raw.startswith("+"):
            continue  # incoming payment/refund — this tracks expenses, not all transactions
        parsed_date = _parse_statement_date(match.group(1))
        amount = _parse_statement_amount(amount_raw)
        if parsed_date is None or amount is None:
            continue
        amount = abs(amount)
        if amount <= 0:
            continue

        category = None
        description_lines = []
        for line in (l.strip() for l in block_text.split("\n")):
            if not line or line.startswith("Ursprungsbetrag") or line.startswith("Wechselkurs"):
                continue
            card_match = _CARD_CATEGORY_LINE_RE.match(line)
            if card_match:
                if card_match.group(1):
                    category = N26_CATEGORY_MAP.get(card_match.group(1).strip())
                continue
            description_lines.append(line)
        description = re.sub(r"\s+", " ", " ".join(description_lines)).strip()
        if not description:
            continue
        rows.append({
            "date": parsed_date,
            "description": description,
            "amount": round(amount, 2),
            "category": category or auto_categorize_expense(description),
        })
    return rows


def extract_pdf_transactions(uploaded_file) -> list[dict]:
    """Extract expense rows from a bank/credit-card statement PDF.

    Tries the N26-style multi-line block format first (description, card +
    category, "Wertstellung" value-date, then the actual dated amount line),
    falling back to a simpler single-line "date ... description ... amount"
    parser for statements laid out that way instead. Either way this is
    meant to be reviewed/edited by the user before import, not trusted blind.
    """
    reader = PdfReader(uploaded_file)
    page_texts = [_strip_statement_page_boilerplate(page.extract_text() or "") for page in reader.pages]
    full_text = "\n".join(page_texts)

    block_rows = _extract_statement_blocks(full_text)
    if block_rows:
        return block_rows

    rows = []
    for page_text in page_texts:
        for line in page_text.split("\n"):
            match = _PDF_LINE_RE.match(line)
            if not match:
                continue
            parsed_date = _parse_statement_date(match.group("date"))
            amount = _parse_statement_amount(match.group("amount"))
            if parsed_date is None or amount is None or amount <= 0:
                continue
            description = re.sub(r"\s+", " ", match.group("description")).strip()
            if not description:
                continue
            if match.group("sign") == "+":
                continue  # incoming payment/refund — this tracks expenses, not all transactions
            rows.append(
                {
                    "date": parsed_date,
                    "description": description,
                    "amount": round(amount, 2),
                    "category": auto_categorize_expense(description),
                }
            )
    return rows
