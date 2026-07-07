"""Tests for statement_parser.py — the bank/credit-card PDF statement parser.

This logic caused both real duplicate/corrupt-transaction incidents before it
was extracted from streamlit_app.py and testable at all. The fixtures below
are synthetic statement text shaped like the real formats (N26 multi-line
blocks and generic single-line "date description amount" statements) — no real
statements are committed, ever.
"""

from datetime import date

import pytest

import statement_parser as sp


# ── Date parsing ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("03.05.2026", date(2026, 5, 3)),
        ("3/5/2026", date(2026, 5, 3)),
        ("03.05.26", date(2026, 5, 3)),          # 2-digit year → 20xx
        ("2026-05-03", date(2026, 5, 3)),
        ("3 May 2026", date(2026, 5, 3)),
        ("12 Okt 2025", date(2025, 10, 12)),      # German month abbreviation
        ("12 December 2025", date(2025, 12, 12)),  # full month name, first 3 letters match
    ],
)
def test_parse_date_formats(raw, expected):
    assert sp._parse_statement_date(raw) == expected


@pytest.mark.parametrize("raw", ["31.02.2026", "not a date", "5 Xyz 2026", ""])
def test_parse_date_invalid_returns_none(raw):
    assert sp._parse_statement_date(raw) is None


# ── Amount parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", 1234.56),   # German: dot thousands, comma decimal
        ("1,234.56", 1234.56),   # US: comma thousands, dot decimal
        ("12,34", 12.34),        # comma-only → decimal comma
        ("12.34", 12.34),
        ("999", 999.0),
    ],
)
def test_parse_amount_formats(raw, expected):
    assert sp._parse_statement_amount(raw) == expected


def test_parse_amount_invalid_returns_none():
    assert sp._parse_statement_amount("abc") is None


# ── Auto-categorization ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("description", "category"),
    [
        ("REWE Markt Berlin", "Groceries"),
        ("Netflix.com", "Subscriptions"),
        ("Deutsche Bahn ticket", "Transport"),
        ("Some Unknown Merchant", "Other"),
    ],
)
def test_auto_categorize(description, category):
    assert sp.auto_categorize_expense(description) == category


def test_amazon_prime_beats_generic_amazon():
    # "amazon prime" (Subscriptions) must win over plain "amazon" (Shopping).
    assert sp.auto_categorize_expense("AMAZON PRIME Video") == "Subscriptions"


# ── N26-style block extraction ──────────────────────────────────────────────

N26_BLOCK = """REWE Markt GmbH
Hauptkonto Mastercard • Lebensmittel
Wertstellung 04.05.2026
03.05.2026 -23,45€
Uber BV
Hauptkonto Mastercard • Transport
Ursprungsbetrag 12,00 USD
Wechselkurs 1,09
Wertstellung 06.05.2026
05.05.2026 -11,01€
Gehalt Mai
Wertstellung 28.05.2026
28.05.2026 +3.000,00€
Mystery Shop
Hauptkonto Mastercard
Wertstellung 30.05.2026
29.05.2026 -9,99€
"""


def test_extract_blocks_parses_expenses_and_skips_income():
    rows = sp._extract_statement_blocks(N26_BLOCK)
    assert [r["description"] for r in rows] == ["REWE Markt GmbH", "Uber BV", "Mystery Shop"]
    # Incoming +3.000,00€ salary is excluded — this tracks expenses only.

    rewe, uber, mystery = rows
    assert rewe == {"date": date(2026, 5, 3), "description": "REWE Markt GmbH", "amount": 23.45, "category": "Groceries"}
    # N26's own category label wins over keyword guessing.
    assert uber["category"] == "Transport"
    # FX lines (Ursprungsbetrag/Wechselkurs) never leak into descriptions.
    assert "USD" not in uber["description"]
    assert uber["amount"] == 11.01
    # Card line without a category falls back to keyword categorization.
    assert mystery["category"] == "Other"


def test_card_line_without_account_prefix_still_recognized():
    # Same block but the card line starts with "Mastercard" directly — it must
    # still be treated as a card/category line, not leak into the description.
    block = (
        "REWE Markt GmbH\nMastercard • Lebensmittel\n"
        "Wertstellung 04.05.2026\n03.05.2026 -23,45€\n"
    )
    rows = sp._extract_statement_blocks(block)
    assert len(rows) == 1
    assert rows[0]["description"] == "REWE Markt GmbH"
    assert rows[0]["category"] == "Groceries"


def test_boilerplate_stripping_removes_page_furniture():
    page = (
        "N26 Bank AG\nDatum Beschreibung Betrag\n"
        "REWE Markt GmbH\nHauptkonto Mastercard • Lebensmittel\n"
        "Wertstellung 04.05.2026\n03.05.2026 -23,45€\n"
        "Kontoauszug Nr. 5/2026\nSeite 1 von 3\n"
    )
    stripped = sp._strip_statement_page_boilerplate(page)
    assert "Beschreibung" not in stripped
    assert "Kontoauszug" not in stripped
    assert "REWE" in stripped
    rows = sp._extract_statement_blocks(stripped)
    assert len(rows) == 1
    assert rows[0]["description"] == "REWE Markt GmbH"


# ── Single-line fallback format ─────────────────────────────────────────────

def line_rows(text: str) -> list[dict]:
    """Run only the single-line fallback (block format won't match this text)."""
    assert sp._extract_statement_blocks(text) == []
    rows = []
    for line in text.split("\n"):
        match = sp._PDF_LINE_RE.match(line)
        if not match:
            continue
        parsed_date = sp._parse_statement_date(match.group("date"))
        amount = sp._parse_statement_amount(match.group("amount"))
        if parsed_date is None or amount is None or amount <= 0:
            continue
        if match.group("sign") == "+":
            continue
        rows.append({"date": parsed_date, "amount": amount, "description": match.group("description")})
    return rows


def test_single_line_statement_rows():
    text = (
        "01.05.2026 REWE SAGT DANKE 23,45\n"
        "02.05.2026 Salary May +3.000,00\n"
        "2026-05-03 Netflix.com € 12,99\n"
        "some junk line without numbers\n"
    )
    rows = line_rows(text)
    assert [(r["date"], r["amount"]) for r in rows] == [
        (date(2026, 5, 1), 23.45),
        (date(2026, 5, 3), 12.99),
    ]
