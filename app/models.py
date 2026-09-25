from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db


class Household(db.Model):
    """
    A group of users who can view each other's spending combined (e.g. a
    couple). Each user still has their own separate login and their own
    uploads stay attributed to them -- a household only affects what the
    dashboard's "combined" view is allowed to show.
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    must_change_password = db.Column(db.Boolean, default=True)
    household_id = db.Column(db.Integer, db.ForeignKey("household.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    household = db.relationship("Household", backref=db.backref("members", lazy="dynamic"))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def household_member_ids(self):
        """This user's own id, plus everyone else in their household (if any)."""
        if not self.household_id:
            return [self.id]
        return [u.id for u in User.query.filter_by(household_id=self.household_id).all()]


ACCOUNT_TYPES = ["credit_card", "bank", "investment"]
ACCOUNT_TYPE_LABELS = {"credit_card": "Credit card", "bank": "Bank account", "investment": "Investment / savings"}
ACCOUNT_TYPE_ICONS = {"credit_card": "💳", "bank": "🏦", "investment": "📈"}
# Which side of net worth an account's balance counts toward. A credit
# card's balance is money owed (a liability); bank and investment balances
# are money the person has (an asset).
ACCOUNT_TYPE_IS_LIABILITY = {"credit_card": True, "bank": False, "investment": False}


class Account(db.Model):
    """One of the person's own credit cards, bank accounts, or investment/
    savings accounts. Every uploaded statement is filed under a credit-card
    or bank account, so spending can be shown per-account, and bank-side
    credit-card payments (transfers, not new spending) can be told apart
    from real purchases. Investment/bank accounts also carry a manually
    updated balance so net worth can be tracked (there's no live account
    sync -- the person updates the balance whenever they check it)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    account_type = db.Column(db.String(20), nullable=False, default="credit_card")
    balance = db.Column(db.Float, nullable=True)
    balance_updated_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("accounts", lazy="dynamic"))

    @property
    def type_label(self):
        return ACCOUNT_TYPE_LABELS.get(self.account_type, self.account_type)

    @property
    def icon(self):
        return ACCOUNT_TYPE_ICONS.get(self.account_type, "💳")

    @property
    def is_liability(self):
        return ACCOUNT_TYPE_IS_LIABILITY.get(self.account_type, False)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True, index=True)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(500), nullable=False)
    amount = db.Column(db.Float, nullable=False)  # negative = spend, positive = payment/credit
    category = db.Column(db.String(80), nullable=False)
    account = db.Column(db.String(200))
    source_file = db.Column(db.String(300))
    # dedupe key so re-uploading the same statement doesn't double-count
    dedupe_hash = db.Column(db.String(64), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("transactions", lazy="dynamic"))
    account_ref = db.relationship("Account", backref=db.backref("transactions", lazy="dynamic"))


class UploadLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True, index=True)
    filename = db.Column(db.String(300))
    num_transactions = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    account_ref = db.relationship("Account", backref=db.backref("uploads", lazy="dynamic"))


# Monthly-equivalent multiplier for each supported cadence, used to roll any
# income/fixed-cost entry up into a single "per month" number for the
# cash-flow summary.
FREQUENCY_MONTHLY_FACTOR = {
    "weekly": 52 / 12,
    "biweekly": 26 / 12,
    "monthly": 1,
    "quarterly": 1 / 3,
    "annual": 1 / 12,
}


class Income(db.Model):
    """A recurring source of money coming in (paycheck, side income, etc.)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    frequency = db.Column(db.String(20), nullable=False, default="monthly")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("incomes", lazy="dynamic"))

    @property
    def monthly_amount(self):
        return self.amount * FREQUENCY_MONTHLY_FACTOR.get(self.frequency, 1)


class FixedCost(db.Model):
    """A recurring fixed bill (rent, car payment, insurance, etc.) that the
    person budgets for on top of day-to-day variable spending."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    frequency = db.Column(db.String(20), nullable=False, default="monthly")
    category = db.Column(db.String(80), default="Fixed Cost")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("fixed_costs", lazy="dynamic"))

    @property
    def monthly_amount(self):
        return self.amount * FREQUENCY_MONTHLY_FACTOR.get(self.frequency, 1)


class CategoryOverride(db.Model):
    """A manual re-categorization the person made for a specific merchant
    description, so it sticks for that merchant going forward (new
    transactions from the same merchant get auto-categorized this way too)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    description = db.Column(db.String(500), nullable=False, index=True)
    category = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
