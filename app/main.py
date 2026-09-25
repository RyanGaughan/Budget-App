import os
import hashlib
import tempfile
from datetime import datetime
from collections import defaultdict

import pandas as pd
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from . import db
from .models import Transaction, UploadLog, User, Income, FixedCost, CategoryOverride, Account, ACCOUNT_TYPES, ACCOUNT_TYPE_LABELS
from .parsers.pdf_parser import extract_transactions_from_pdf
from .parsers.categorize import categorize_description, ALL_CATEGORIES, IRREGULAR_CATEGORIES
from .parsers.recurring import detect_recurring, recurring_summary_stats

main_bp = Blueprint("main", __name__)

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
FOOD_CATEGORIES = ["Groceries", "Dining & Coffee"]
FREQUENCIES = ["weekly", "biweekly", "monthly", "quarterly", "annual"]
# Money moving between the person's own accounts (paying off a credit card
# from checking, transfers, payroll deposits) isn't spending -- it's already
# counted once, on the statement where the actual purchase happened. Leaving
# it in would double-count every dollar that crosses from a bank account to
# a card.
TRANSFER_CATEGORIES = ["Income/Payments"]


def _dedupe_hash(user_id, date, description, amount, source_file):
    raw = f"{user_id}|{date}|{description}|{amount:.2f}|{source_file}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _user_overrides(user_ids):
    rows = CategoryOverride.query.filter(CategoryOverride.user_id.in_(user_ids)).all()
    return {r.description: r.category for r in rows}


def _scope_member_ids():
    member_ids = current_user.household_member_ids()
    is_combined = request.args.get("scope") == "household" and len(member_ids) > 1
    ids = member_ids if is_combined else [current_user.id]
    return ids, is_combined, len(member_ids) > 1


@main_bp.route("/")
@login_required
def index():
    return redirect(url_for("main.dashboard"))


# ------------------------------------------------------------------ Accounts

@main_bp.route("/accounts/add", methods=["POST"])
@login_required
def add_account():
    name = request.form.get("name", "").strip()
    account_type = request.form.get("account_type", "credit_card")
    balance = request.form.get("balance", "").strip()
    if not name or account_type not in ACCOUNT_TYPES:
        flash("Enter a name and pick an account type.", "error")
        return redirect(url_for("main.statements"))
    acct = Account(user_id=current_user.id, name=name, account_type=account_type)
    if balance:
        try:
            acct.balance = float(balance)
            acct.balance_updated_at = datetime.utcnow()
        except ValueError:
            pass
    db.session.add(acct)
    db.session.commit()
    flash(f"Added {acct.type_label.lower()}: {name}.", "success")
    return redirect(url_for("main.statements"))


@main_bp.route("/accounts/<int:account_id>/delete", methods=["POST"])
@login_required
def delete_account(account_id):
    acct = Account.query.get_or_404(account_id)
    if acct.user_id != current_user.id:
        flash("You can only remove your own accounts.", "error")
        return redirect(url_for("main.statements"))
    Transaction.query.filter_by(account_id=acct.id).update({"account_id": None})
    UploadLog.query.filter_by(account_id=acct.id).update({"account_id": None})
    db.session.delete(acct)
    db.session.commit()
    flash(f"Removed {acct.name}. Its statements are kept, just unassigned from an account now.", "success")
    return redirect(url_for("main.statements"))


@main_bp.route("/accounts/<int:account_id>/balance", methods=["POST"])
@login_required
def update_balance(account_id):
    acct = Account.query.get_or_404(account_id)
    if acct.user_id != current_user.id:
        flash("You can only update your own accounts.", "error")
        return redirect(url_for("main.statements"))
    balance = request.form.get("balance", "").strip()
    try:
        acct.balance = float(balance)
        acct.balance_updated_at = datetime.utcnow()
        db.session.commit()
        flash(f"Updated {acct.name}'s balance.", "success")
    except ValueError:
        flash("Enter a valid balance.", "error")
    return redirect(url_for("main.statements"))


# ---------------------------------------------------------------- Statements

@main_bp.route("/statements")
@login_required
def statements():
    uploads = (
        UploadLog.query.filter_by(user_id=current_user.id)
        .order_by(UploadLog.uploaded_at.desc()).all()
    )
    accounts = Account.query.filter_by(user_id=current_user.id).order_by(Account.created_at).all()
    statement_accounts = [a for a in accounts if a.account_type in ("credit_card", "bank")]
    return render_template(
        "statements.html", uploads=uploads, accounts=accounts,
        statement_accounts=statement_accounts, account_type_labels=ACCOUNT_TYPE_LABELS,
    )


@main_bp.route("/upload", methods=["POST"])
@login_required
def upload():
    files = request.files.getlist("statements")
    flip_sign = request.form.get("flip_sign") == "on"
    account_id = request.form.get("account_id", "")
    account_id = int(account_id) if account_id.isdigit() else None
    if account_id is not None:
        acct = Account.query.get(account_id)
        if not acct or acct.user_id != current_user.id:
            account_id = None
    if not files or files[0].filename == "":
        flash("Choose at least one PDF.", "error")
        return redirect(url_for("main.statements"))
    if account_id is None:
        flash("Pick which card or account this statement is for.", "error")
        return redirect(url_for("main.statements"))

    overrides = _user_overrides([current_user.id])
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
                category = categorize_description(t["description"], overrides)
                db.session.add(Transaction(
                    user_id=current_user.id,
                    account_id=account_id,
                    date=t["date"],
                    description=t["description"],
                    amount=amount,
                    category=category,
                    account=t["account"],
                    source_file=fname,
                    dedupe_hash=dh,
                ))
                added += 1
            db.session.add(UploadLog(user_id=current_user.id, account_id=account_id, filename=fname, num_transactions=added))
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

    return redirect(url_for("main.statements"))


@main_bp.route("/statements/<int:upload_id>/delete", methods=["POST"])
@login_required
def delete_statement(upload_id):
    log = UploadLog.query.get_or_404(upload_id)
    if log.user_id != current_user.id:
        flash("You can only delete your own statements.", "error")
        return redirect(url_for("main.statements"))
    deleted = Transaction.query.filter_by(user_id=log.user_id, source_file=log.filename).delete()
    db.session.delete(log)
    db.session.commit()
    flash(f"Removed {log.filename} and {deleted} transaction(s).", "success")
    return redirect(url_for("main.statements"))


# ------------------------------------------------------------------ Overview

def _load_scope_df(member_ids):
    txns = Transaction.query.filter(Transaction.user_id.in_(member_ids)).order_by(Transaction.date).all()
    if not txns:
        return None
    owner_names = {u.id: u.username for u in User.query.filter(User.id.in_(member_ids)).all()}
    df = pd.DataFrame([{
        "date": t.date, "description": t.description, "amount": t.amount,
        "category": t.category, "account": t.account, "owner": owner_names.get(t.user_id, "?"),
        "account_id": t.account_id, "account_name": t.account_ref.name if t.account_ref else "Unassigned",
    } for t in txns])
    df["date"] = pd.to_datetime(df["date"])
    return df


@main_bp.route("/dashboard")
@login_required
def dashboard():
    member_ids, is_combined, has_household = _scope_member_ids()
    df = _load_scope_df(member_ids)

    if df is None:
        return render_template("dashboard.html", has_data=False, has_household=has_household, is_combined=is_combined)

    is_spend = (df["amount"] < 0) & (~df["category"].isin(TRANSFER_CATEGORIES))

    person_totals = []
    if is_combined:
        person_spend = df[is_spend].groupby("owner")["amount"].sum().abs().sort_values(ascending=False)
        person_totals = [{"name": n, "total": v} for n, v in person_spend.items()]

    spend = df[is_spend].copy()
    spend["month"] = spend["date"].dt.to_period("M").astype(str)

    total_spend = float(-spend["amount"].sum()) if not spend.empty else 0.0
    num_months = spend["month"].nunique() if not spend.empty else 0
    avg_monthly = total_spend / num_months if num_months else 0.0

    monthly_totals = spend.groupby("month")["amount"].sum().abs().sort_index()
    months_sorted = list(monthly_totals.index)
    latest_month = months_sorted[-1] if months_sorted else None
    latest_spend = float(monthly_totals.iloc[-1]) if len(monthly_totals) else 0.0
    prev_spend = float(monthly_totals.iloc[-2]) if len(monthly_totals) >= 2 else None
    spend_delta = (latest_spend - prev_spend) if prev_spend is not None else None

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
    monthly_cat_display = monthly_cat.reindex(columns=display_cats, fill_value=0).reset_index()

    months = monthly_cat_display["month"].tolist()
    series_data = {cat: monthly_cat_display[cat].round(2).tolist() for cat in display_cats}
    color_map = {cat: PALETTE[i % len(PALETTE)] for i, cat in enumerate(display_cats)}

    # Monthly cost by category table, with a "regularity" read: is this a
    # consistent everyday category (shows up almost every month, similar
    # amount) or an irregular/occasional one (a trip, a big one-off)?
    category_table = []
    for cat in [c for c in monthly_cat.columns if c not in ("Other",)]:
        values = [round(v, 2) for v in monthly_cat[cat].tolist()]
        months_present = sum(1 for v in values if v > 0)
        nonzero = [v for v in values if v > 0]
        avg = round(sum(nonzero) / len(nonzero), 2) if nonzero else 0
        max_val = max(values) if values else 0
        is_irregular = cat in IRREGULAR_CATEGORIES or (num_months and months_present <= max(1, num_months // 3))
        category_table.append({
            "category": cat, "values": values, "avg": avg, "max_val": max_val,
            "months_present": months_present, "irregular": is_irregular,
        })
    category_table.sort(key=lambda r: -r["avg"])

    # Insights: consistent big spenders (candidates to cut back on) vs.
    # irregular/occasional spend (trips, one-offs) that shouldn't be judged
    # against a "normal month".
    consistent_rows = [r for r in category_table if not r["irregular"] and r["category"] != "Income/Payments"]
    irregular_rows = [r for r in category_table if r["irregular"] and r["category"] != "Income/Payments"]
    top_cut_candidates = sorted(consistent_rows, key=lambda r: -r["avg"])[:3]
    irregular_total = sum(sum(r["values"]) for r in irregular_rows)

    food_this_month = 0.0
    food_avg = 0.0
    if not spend.empty:
        food_spend = spend[spend["category"].isin(FOOD_CATEGORIES)]
        if latest_month is not None:
            food_this_month = float(food_spend[food_spend["month"] == latest_month]["amount"].sum() * -1)
        food_by_month = food_spend.groupby("month")["amount"].sum().abs()
        food_avg = float(food_by_month.mean()) if len(food_by_month) else 0.0

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

    # Cash flow: manually-entered income & fixed costs, rolled up to a
    # monthly-equivalent number, set against actual variable spending.
    incomes = Income.query.filter(Income.user_id.in_(member_ids)).all()
    fixed_costs = FixedCost.query.filter(FixedCost.user_id.in_(member_ids)).all()
    income_monthly = sum(i.monthly_amount for i in incomes)
    fixed_monthly = sum(f.monthly_amount for f in fixed_costs)
    leftover_monthly = income_monthly - fixed_monthly - avg_monthly
    has_budget_setup = bool(incomes or fixed_costs)

    # Net worth: manually-tracked account balances (bank + investment assets
    # minus credit card balances owed). Purely manual since there's no bank
    # sync -- the person updates balances on the Statements/Accounts page.
    accounts = Account.query.filter(Account.user_id.in_(member_ids)).all()
    assets = sum(a.balance or 0 for a in accounts if not a.is_liability)
    liabilities = sum(a.balance or 0 for a in accounts if a.is_liability)
    net_worth = assets - liabilities
    has_net_worth_setup = any(a.balance is not None for a in accounts)

    return render_template(
        "dashboard.html",
        has_data=True,
        date_range=(str(df["date"].min().date()), str(df["date"].max().date())),
        num_months=num_months,
        total_transactions=len(df),
        total_spend=total_spend,
        avg_monthly=avg_monthly,
        latest_month=latest_month,
        latest_spend=latest_spend,
        spend_delta=spend_delta,
        rec_stats=rec_stats,
        recurring_rows=recurring_rows[:6],
        recurring_count=len(recurring_rows),
        top_merchant_rows=top_merchant_rows,
        max_merchant=max_merchant,
        months=months,
        series_data=series_data,
        color_map=color_map,
        uncategorized_count=uncategorized_count,
        has_household=has_household,
        is_combined=is_combined,
        person_totals=person_totals,
        category_table=category_table,
        top_cut_candidates=top_cut_candidates,
        irregular_total=irregular_total,
        food_this_month=food_this_month,
        food_avg=food_avg,
        income_monthly=income_monthly,
        fixed_monthly=fixed_monthly,
        leftover_monthly=leftover_monthly,
        has_budget_setup=has_budget_setup,
        net_worth=net_worth,
        assets=assets,
        liabilities=liabilities,
        has_net_worth_setup=has_net_worth_setup,
    )


# ------------------------------------------------------------------ Recurring

@main_bp.route("/recurring")
@login_required
def recurring():
    member_ids, is_combined, has_household = _scope_member_ids()
    df = _load_scope_df(member_ids)
    if df is None:
        return render_template("recurring.html", has_data=False, has_household=has_household, is_combined=is_combined)

    recurring_df = detect_recurring(df)
    rec_stats = recurring_summary_stats(recurring_df)
    rows = recurring_df[recurring_df["is_recurring"] == True].to_dict("records") if not recurring_df.empty else []  # noqa: E712
    rows.sort(key=lambda r: -r.get("est_monthly_cost", r.get("avg_amount", 0)))
    annual_total = sum(r.get("est_monthly_cost", r.get("avg_amount", 0)) for r in rows) * 12

    return render_template(
        "recurring.html", has_data=True, rows=rows, rec_stats=rec_stats,
        annual_total=annual_total, has_household=has_household, is_combined=is_combined,
    )


# --------------------------------------------------------------- Transactions

@main_bp.route("/transactions")
@login_required
def transactions():
    member_ids, is_combined, has_household = _scope_member_ids()
    category = request.args.get("category", "")
    month = request.args.get("month", "")
    q = request.args.get("q", "").strip()

    query = Transaction.query.filter(Transaction.user_id.in_(member_ids))
    if category:
        query = query.filter(Transaction.category == category)
    if q:
        query = query.filter(Transaction.description.ilike(f"%{q}%"))
    txns = query.order_by(Transaction.date.desc()).all()
    if month:
        txns = [t for t in txns if t.date.strftime("%Y-%m") == month]

    owner_names = {u.id: u.username for u in User.query.filter(User.id.in_(member_ids)).all()} if is_combined else {}
    all_months = sorted({t.date.strftime("%Y-%m") for t in Transaction.query.filter(Transaction.user_id.in_(member_ids)).all()}, reverse=True)

    rows = [{
        "id": t.id, "date": t.date, "description": t.description, "amount": t.amount,
        "category": t.category, "owner": owner_names.get(t.user_id),
    } for t in txns][:500]

    return render_template(
        "transactions.html", rows=rows, categories=ALL_CATEGORIES,
        category=category, month=month, q=q, all_months=all_months,
        has_household=has_household, is_combined=is_combined, total_count=len(txns),
    )


@main_bp.route("/transactions/recategorize", methods=["POST"])
@login_required
def recategorize():
    description = request.form.get("description", "")
    category = request.form.get("category", "")
    redirect_args = {k: v for k, v in request.form.items() if k in ("month", "q", "scope")}
    if request.form.get("filter_category"):
        redirect_args["category"] = request.form.get("filter_category")
    if not description or category not in ALL_CATEGORIES:
        flash("Couldn't apply that category.", "error")
        return redirect(url_for("main.transactions", **redirect_args))

    updated = Transaction.query.filter_by(user_id=current_user.id, description=description).update({"category": category})
    existing = CategoryOverride.query.filter_by(user_id=current_user.id, description=description).first()
    if existing:
        existing.category = category
    else:
        db.session.add(CategoryOverride(user_id=current_user.id, description=description, category=category))
    db.session.commit()
    flash(f"Recategorized {updated} transaction(s) from \"{description}\" as {category}.", "success")
    return redirect(url_for("main.transactions", **redirect_args))


@main_bp.route("/uncategorized")
@login_required
def uncategorized():
    return redirect(url_for("main.transactions", category="Uncategorized"))


# --------------------------------------------------------------------- Budget

@main_bp.route("/budget")
@login_required
def budget():
    incomes = Income.query.filter_by(user_id=current_user.id).order_by(Income.created_at).all()
    fixed_costs = FixedCost.query.filter_by(user_id=current_user.id).order_by(FixedCost.created_at).all()
    income_monthly = sum(i.monthly_amount for i in incomes)
    fixed_monthly = sum(f.monthly_amount for f in fixed_costs)
    return render_template(
        "budget.html", incomes=incomes, fixed_costs=fixed_costs, frequencies=FREQUENCIES,
        income_monthly=income_monthly, fixed_monthly=fixed_monthly,
    )


@main_bp.route("/budget/income/add", methods=["POST"])
@login_required
def add_income():
    name = request.form.get("name", "").strip()
    amount = request.form.get("amount", "")
    frequency = request.form.get("frequency", "monthly")
    try:
        amount = float(amount)
    except ValueError:
        flash("Enter a valid amount.", "error")
        return redirect(url_for("main.budget"))
    if not name or frequency not in FREQUENCIES:
        flash("Enter a name and pick how often you're paid.", "error")
        return redirect(url_for("main.budget"))
    db.session.add(Income(user_id=current_user.id, name=name, amount=amount, frequency=frequency))
    db.session.commit()
    flash(f"Added income: {name}.", "success")
    return redirect(url_for("main.budget"))


@main_bp.route("/budget/income/<int:income_id>/delete", methods=["POST"])
@login_required
def delete_income(income_id):
    row = Income.query.get_or_404(income_id)
    if row.user_id == current_user.id:
        db.session.delete(row)
        db.session.commit()
    return redirect(url_for("main.budget"))


@main_bp.route("/budget/fixed/add", methods=["POST"])
@login_required
def add_fixed():
    name = request.form.get("name", "").strip()
    amount = request.form.get("amount", "")
    frequency = request.form.get("frequency", "monthly")
    try:
        amount = float(amount)
    except ValueError:
        flash("Enter a valid amount.", "error")
        return redirect(url_for("main.budget"))
    if not name or frequency not in FREQUENCIES:
        flash("Enter a name and how often this bill comes due.", "error")
        return redirect(url_for("main.budget"))
    db.session.add(FixedCost(user_id=current_user.id, name=name, amount=amount, frequency=frequency))
    db.session.commit()
    flash(f"Added fixed cost: {name}.", "success")
    return redirect(url_for("main.budget"))


@main_bp.route("/budget/fixed/<int:fixed_id>/delete", methods=["POST"])
@login_required
def delete_fixed(fixed_id):
    row = FixedCost.query.get_or_404(fixed_id)
    if row.user_id == current_user.id:
        db.session.delete(row)
        db.session.commit()
    return redirect(url_for("main.budget"))
