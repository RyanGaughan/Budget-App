"""
Detects recurring charges (subscriptions, memberships, bills) from a
transaction DataFrame.

Approach:
1. Normalize merchant descriptions (strip store numbers, transaction IDs,
   trailing codes like "AMAZON.COM*AB12CD3" -> "AMAZON.COM") so the same
   merchant groups together even when the raw text varies slightly month
   to month.
2. Group by normalized merchant.
3. For each group with >=2 occurrences, look at the gaps between charge
   dates. If gaps cluster around ~7, ~14, ~30, or ~365 days (within
   tolerance), and amounts are reasonably consistent, flag it recurring
   and label its cadence.
4. Flag price increases: if the most recent charge amount is higher than
   the group's earlier median by more than a threshold, surface it.
"""

import re
import numpy as np
import pandas as pd

CADENCE_BUCKETS = [
    ("Weekly", 7, 3),
    ("Bi-weekly", 14, 4),
    ("Monthly", 30, 6),
    ("Quarterly", 91, 10),
    ("Annual", 365, 20),
]


def normalize_merchant(description):
    d = description.upper()
    d = re.sub(r"\*[A-Z0-9]+$", "", d)          # trailing *ABC123
    d = re.sub(r"#\d+", "", d)                   # store numbers #1234
    d = re.sub(r"\b\d{4,}\b", "", d)              # long numeric IDs
    d = re.sub(r"\s{2,}", " ", d)
    d = re.sub(r"[^A-Z0-9 &.'-]", "", d)
    return d.strip()


def _classify_cadence(avg_gap_days):
    for label, target, tolerance in CADENCE_BUCKETS:
        if abs(avg_gap_days - target) <= tolerance:
            return label
    return None


def detect_recurring(df, min_occurrences=2, amount_tolerance_pct=0.15):
    """
    df must have columns: date, description, amount.
    Only considers spending (amount < 0) since recurring income isn't the
    concern here.
    Returns a DataFrame: merchant, cadence, occurrences, avg_amount,
    last_amount, last_date, first_date, price_increase_flag, monthly_est.
    """
    spend = df[df["amount"] < 0].copy()
    spend["merchant_norm"] = spend["description"].apply(normalize_merchant)

    results = []
    for merchant, group in spend.groupby("merchant_norm"):
        if len(group) < min_occurrences or not merchant:
            continue
        group = group.sort_values("date")
        dates = group["date"].tolist()
        amounts = (-group["amount"]).tolist()  # positive spend amounts

        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_gap = float(np.mean(gaps)) if gaps else None
        cadence = _classify_cadence(avg_gap) if avg_gap else None

        amt_median = float(np.median(amounts))
        amt_std = float(np.std(amounts))
        amount_consistent = (amt_std / amt_median) < amount_tolerance_pct if amt_median else False

        is_recurring = cadence is not None and amount_consistent

        last_amount = amounts[-1]
        earlier_median = float(np.median(amounts[:-1])) if len(amounts) > 1 else amt_median
        price_increase = last_amount > earlier_median * 1.05  # >5% bump

        # Rough monthly-equivalent cost for budgeting purposes
        monthly_est = None
        if cadence == "Weekly":
            monthly_est = amt_median * 4.33
        elif cadence == "Bi-weekly":
            monthly_est = amt_median * 2.17
        elif cadence == "Monthly":
            monthly_est = amt_median
        elif cadence == "Quarterly":
            monthly_est = amt_median / 3
        elif cadence == "Annual":
            monthly_est = amt_median / 12

        results.append({
            "merchant": merchant.title(),
            "is_recurring": is_recurring,
            "cadence": cadence or "Irregular",
            "occurrences": len(group),
            "avg_amount": round(amt_median, 2),
            "last_amount": round(last_amount, 2),
            "last_date": dates[-1].date() if hasattr(dates[-1], "date") else dates[-1],
            "first_date": dates[0].date() if hasattr(dates[0], "date") else dates[0],
            "price_increase_flag": price_increase,
            "est_monthly_cost": round(monthly_est, 2) if monthly_est else None,
        })

    result_df = pd.DataFrame(results)
    if not result_df.empty:
        result_df = result_df.sort_values(
            ["is_recurring", "est_monthly_cost"], ascending=[False, False]
        ).reset_index(drop=True)
    return result_df


def recurring_summary_stats(recurring_df):
    if recurring_df is None or recurring_df.empty or "is_recurring" not in recurring_df.columns:
        return {"num_recurring": 0, "total_est_monthly": 0, "num_price_increases": 0}
    rec = recurring_df[recurring_df["is_recurring"]]
    return {
        "num_recurring": len(rec),
        "total_est_monthly": round(rec["est_monthly_cost"].sum(), 2) if not rec.empty else 0,
        "num_price_increases": int(rec["price_increase_flag"].sum()) if not rec.empty else 0,
    }
