"""
A tiny setup page for creating user accounts through the browser, for hosts
(like Render's free tier) that don't offer shell/SSH access to the running
service. Protected by a secret key set as the SETUP_KEY environment
variable -- without that env var set, this route refuses everything.
"""
import os
import secrets
from flask import Blueprint, request, Response

from . import db
from .models import User, Household

admin_bp = Blueprint("admin", __name__)


def _make_password():
    return secrets.token_urlsafe(9)


@admin_bp.route("/setup")
def setup():
    setup_key = os.environ.get("SETUP_KEY")
    if not setup_key:
        return Response("Setup is disabled (no SETUP_KEY configured).", status=404)
    if request.args.get("key") != setup_key:
        return Response("Invalid or missing key.", status=403)

    usernames_raw = request.args.get("users", "")
    usernames = [u.strip() for u in usernames_raw.split(",") if u.strip()]
    if not usernames:
        return Response(
            "Usage: /setup?key=...&users=ryan,ellie&household=Optional+Household+Name",
            mimetype="text/plain",
        )

    household_name = request.args.get("household", "").strip()
    household = None
    if household_name:
        household = Household.query.filter_by(name=household_name).first()
        if not household:
            household = Household(name=household_name)
            db.session.add(household)
            db.session.commit()

    lines = []
    if household:
        lines.append(f'Household: "{household_name}" (id={household.id})')
    lines.append(f"{'username':<20} {'temporary password':<20}")
    lines.append("-" * 40)

    for uname in usernames:
        existing = User.query.filter_by(username=uname).first()
        if existing:
            if household and existing.household_id != household.id:
                existing.household_id = household.id
                db.session.commit()
                lines.append(f"{uname:<20} (already existed, linked to household)")
            else:
                lines.append(f"{uname:<20} (already exists, skipped)")
            continue
        pw = _make_password()
        user = User(username=uname, must_change_password=True,
                    household_id=household.id if household else None)
        user.set_password(pw)
        db.session.add(user)
        db.session.commit()
        lines.append(f"{uname:<20} {pw:<20}")

    lines.append("")
    lines.append("Save these passwords now -- they won't be shown again.")

    return Response("\n".join(lines), mimetype="text/plain")