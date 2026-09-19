"""
Parses credit card / bank statement PDFs into a clean transaction list.

Design notes:
- Statement layouts vary a LOT between issuers (Chase, Amex, BofA, Wells Fargo, etc.)
  so instead of hard-coding one exact layout, this uses a flexible line-based
  regex that looks for the pattern: DATE ... DESCRIPTION ... AMOUNT
  This will work for most statements but may need small tweaks per-issuer.
- Extracts raw text with pdfplumber (works even without a text layer being
  perfectly tabular, unlike trying to parse tables directly).
- Every transaction is tagged with the source filename and an assumed
  account name (derived from the filename) so multiple cards/accounts can be
  merged later and traced back to their source.
"""

import re
import os
from datetime import datetime
import pdfplumber
import pandas as pd

# Matches lines like:
#   01/15  AMAZON.COM*AB123          -45.67
#   01/15/2026 STARBUCKS #1234        12.50
#   Jan 15   COSTCO WHOLESALE        123.45
DATE_PATTERNS = [
    r"(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)",          # 01/15 or 01/15/2026
    r"(?P<date>\d{1,2}-\d{1,2}(?:-\d{2,4})?)",          # 01-15
    r"(?P<date>[A-Za-z]{3}\s+\d{1,2})",                  # Jan 15
]

AMOUNT_PATTERN = r"(?P<amount>-?\$?\(?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?)"

# Build a combined line regex: DATE  ...DESCRIPTION...  AMOUNT (end of line)
LINE_REGEXES = [
    re.compile(rf"^\s*{dp}\s+(?P<desc>.+?)\s+{AMOUNT_PATTERN}\s*$")
    for dp in DATE_PATTERNS
]

# Lines that are clearly not transactions (headers/footers/totals)
SKIP_LINE_KEYWORDS = [
    "previous balance", "payment due", "minimum payment", "total fees",
    "total interest", "new balance", "credit limit", "available credit",
    "page", "customer service", "statement date", "account summary",
    "total payments", "total credits", "beginning balance", "ending balance",
]


def _clean_amount(raw):
    """Convert '$1,234.56', '(45.67)', '45.67-' etc. into a signed float."""
    s = raw.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.endswith("-"):
        negative = True
        s = s[:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    s = s.replace("$", "").replace(",", "")
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if negative else val


def _normalize_date(raw, statement_year_hint=None):
    """Best-effort parse of a variety of date formats found on statements."""
    raw = raw.strip()
    fmts = ["%m/%d/%Y", "%m/%d/%y", "%m/%d", "%m-%d-%Y", "%m-%d-%y", "%m-%d", "%b %d"]
    for fmt in fmts:
        try:
            dt = datetime.strptime(raw, fmt)
            if "%Y" not in fmt and "%y" not in fmt:
                year = statement_year_hint or datetime.now().year
                dt = dt.replace(year=year)
            return dt.date()
        except ValueError:
            continue
    return None


def _guess_statement_year(text):
    """Look for a 4-digit year near 'statement' text to anchor dates like '01/15'."""
    m = re.search(r"(20\d{2})", text)
    if m:
        return int(m.group(1))
    return datetime.now().year


def extract_transactions_from_pdf(pdf_path, account_name=None):
    """
    Returns a list of dicts: {date, description, amount, account, source_file}
    amount convention: negative = money out (charge/debit), positive = payment/credit in.
    NOTE: many statements print charges as positive and payments as negative
    (or vice versa) -- see `flip_sign` handling in load_all_statements().
    """
    account_name = account_name or os.path.splitext(os.path.basename(pdf_path))[0]
    transactions = []

    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        year_hint = _guess_statement_year(full_text)

        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                low = line.lower()
                if any(kw in low for kw in SKIP_LINE_KEYWORDS):
                    continue
                for regex in LINE_REGEXES:
                    m = regex.match(line)
                    if m:
                        date = _normalize_date(m.group("date"), year_hint)
                        amount = _clean_amount(m.group("amount"))
                        desc = m.group("desc").strip()
                        if date and amount is not None and desc:
                            transactions.append({
                                "date": date,
                                "description": desc,
                                "amount": amount,
                                "account": account_name,
                                "source_file": os.path.basename(pdf_path),
                            })
                        break

    return transactions


def load_all_statements(pdf_dir, flip_sign_for_files=None):
    """
    Parses every PDF in pdf_dir into one combined DataFrame.

    flip_sign_for_files: optional list of filenames (or substrings) where
    charges are printed as POSITIVE numbers (common on credit card statements,
    where a charge appears as e.g. "45.67" not "-45.67"). For those files,
    signs are flipped so the whole dataset uses one convention:
        negative = spending (money out), positive = payment/refund/credit in.
    """
    flip_sign_for_files = flip_sign_for_files or []
    all_txns = []

    for fname in sorted(os.listdir(pdf_dir)):
        if not fname.lower().endswith(".pdf"):
            continue
        path = os.path.join(pdf_dir, fname)
        txns = extract_transactions_from_pdf(path)
        should_flip = any(tag.lower() in fname.lower() for tag in flip_sign_for_files)
        if should_flip:
            for t in txns:
                t["amount"] = -t["amount"]
        all_txns.extend(txns)

    df = pd.DataFrame(all_txns)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


if __name__ == "__main__":
    import sys
    pdf_dir = sys.argv[1] if len(sys.argv) > 1 else "../sample_data"
    df = load_all_statements(pdf_dir)
    print(f"Extracted {len(df)} transactions from {pdf_dir}")
    print(df.head(20))
