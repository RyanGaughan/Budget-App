import os
import hashlib
import tempfile
from datetime import datetime
from collections import defaultdict

import pandas as pd
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from . import db
from .models import Transaction, UploadLog
from .parsers.pdf_parser import extract_transactions_from_pdf
from .parsers.categorize import categorize_description
from .parsers.recurring import detect_recurring, recurring_summary_stats

main_bp = Blueprint("main", __name__)

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


def _dedupe_hash(user_id, date, description, amount, source_file):
    raw = f"{user_id}|{date}|{description}|{amount:.2f}|{source_file}"
    return hashlib.sha256(raw.encode()).hexdigest()


@main_bp.route("/")
@login_required
def index():
    return redirect(url_for("main.dashboard"))


@main_bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        files = request.files.getlist("statements")
        flip_sign = request.form.get("flip_sign") == "on"
        if not files or files[0].filename == "":
            flash("Choose at least one PDF.", "error")
            return redirect(url_for("main.upload"))

        total_added = 0
        for f in files:
            if not f.filename.lower().endswith(".pdf"):
                flash(f"Skipped {f.filename} (not a PDF).", "error")
                continue
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                f.save(tmp.name)
                tmp_path = tmp.name
            try:
                fname = secure_filename(f.filename)
                txns = extract_transactions_from_pdf(tmp_path, account_name=fname)
                added = 0
                for t in txns:
                    amount = -t["amount"] if flip_sign else t["amount"]
                    dh = _dedupe_hash(current_user.id, t["date"], t["description"], amount, fname)
                    if Transaction.query.filter_by(user_id=current_user.id, dedupe_hash=dh).first():
                        continue  # already imported this exact transaction
                    category = categorize_description(t["description"])
                    db.session.add(Transaction(
                        user_id=current_user.id,
                        date=t["date"],
                        description=t["description"],
                        amount=amount,
                        category=category,
                        account=t["account"],
                        source_file=fname,
                        dedupe_hash=dh,
                    ))
                    added += 1
                db.session.add(UploadLog(user_id=current_user.id, filename=fname, num_transactions=added))
                db.session.commit()
                total_added += added
                if added == 0 and txns:
                    flash(f"{fname}: parsed {len(txns)} lines but all were already imported.", "success")
                elif added == 0:
                    flash(f"{fname}: couldn't extract any transactions — the layout may not match. "
                          f"Send this one to be reviewed.", "error")
                else:
                    flash(f"{fname}: added {added} transactions.", "success")
            finally:
                os.unlink(tmp_path)

        return redirect(url_for("main.dashboard"))

    return render_template("upload.html")


@main_bp.route("/dashboard")
@login_required
def dashboard():
    txns = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.date).all()
    if not txns:
        return render_template("dashboard.html", has_data=False)

    df = pd.DataFrame([{
        "date": t.date, "description": t.description, "amount": t.amount,
        "category": t.category, "account": t.account,
    } for t in txns])
    df["date"] = pd.to_datetime(df["date"])

    spend = df[df["amount"] < 0].copy()
    spend["month"] = spend["date"].dt.to_period("M").astype(str)

    total_spend = float(-spend["amount"].sum()) if not spend.empty else 0.0
    num_months = spend["month"].nunique() if not spend.empty else 0
    avg_monthly = total_spend / num_months if num_months else 0.0

    monthly_cat = (
        spend.groupby(["month", "category"])["amount"].sum().abs().reset_index()
        .pivot(index="month", columns="category", values="amount").fillna(0)
    )
    cat_totals = monthly_cat.sum().sort_values(ascending=False)
    top_cats = list(cat_totals.index[:8])
    other_cats = [c for c in monthly_cat.columns if c not in top_cats]
    if other_cats:
        monthly_cat["Other"] = monthly_cat[other_cats].sum(axis=1)
    display_cats = top_cats + (["Other"] if other_cats else [])
    monthly_cat = monthly_cat.reindex(columns=display_cats, fill_value=0).reset_index()

    months = monthly_cat["month"].tolist()
    series_data = {cat: monthly_cat[cat].round(2).tolist() for cat in display_cats}
    color_map = {cat: PALETTE[i % len(PALETTE)] for i, cat in enumerate(display_cats)}

    recurring_df = detect_recurring(df)
    rec_stats = recurring_summary_stats(recurring_df)
    recurring_rows = recurring_df[recurring_df["is_recurring"] == True].to_dict("records") if not recurring_df.empty else []  # noqa: E712

    top_merchants = (
        spend.groupby("description")["amount"].sum().abs()
        .sort_values(ascending=False).head(10).reset_index()
    )
    top_merchants.columns = ["merchant", "total"]
    top_merchant_rows = top_merchants.to_dict("records")
    max_merchant = top_merchant_rows[0]["total"] if top_merchant_rows else 1

    uncategorized_count = int((df["category"] == "Uncategorized").sum())

    return render_template(
        "dashboard.html",
        has_data=True,
        date_range=(str(df["date"].min().date()), str(df["date"].max().date())),
        num_months=num_months,
        total_transactions=len(df),
        total_spend=total_spend,
        avg_monthly=avg_monthly,
        rec_stats=rec_stats,
        recurring_rows=recurring_rows,
        top_merchant_rows=top_merchant_rows,
        max_merchant=max_merchant,
        months=months,
        series_data=series_data,
        color_map=color_map,
        uncategorized_count=uncategorized_count,
    )


@main_bp.route("/uncategorized")
@login_required
def uncategorized():
    txns = Transaction.query.filter_by(user_id=current_user.id, category="Uncategorized").all()
    grouped = defaultdict(lambda: {"count": 0, "total": 0.0})
    for t in txns:
        grouped[t.description]["count"] += 1
        grouped[t.description]["total"] += t.amount
    rows = sorted(
        ({"description": k, **v} for k, v in grouped.items()),
        key=lambda r: -r["count"]
    )
    return render_template("uncategorized.html", rows=rows)
