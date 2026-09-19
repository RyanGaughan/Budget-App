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


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
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


class UploadLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    filename = db.Column(db.String(300))
    num_transactions = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
