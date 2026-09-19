"""
Creates or resets household user accounts.

Usage:
    python3 seed_users.py alice bob                  # separate, unlinked accounts
    python3 seed_users.py --household "The Smiths" alice bob   # linked household

Generates a random temporary password for each username given (skips
usernames that already exist), prints them once, and sets
must_change_password=True so each person picks their own password on
first login. With --household, the given usernames can see a combined
"household" view of everyone's spending together, in addition to their
own private view -- they still each have a separate login and password.

Run this once locally, or via Render's Shell tab for the deployed service.
"""
import sys
import argparse
import secrets
from app import create_app, db
from app.models import User, Household


def make_password():
    return secrets.token_urlsafe(9)  # ~12 char random password


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("usernames", nargs="+")
    ap.add_argument("--household", default=None, help="Name a household to link these accounts together for a combined view")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        household = None
        if args.household:
            household = Household.query.filter_by(name=args.household).first()
            if not household:
                household = Household(name=args.household)
                db.session.add(household)
                db.session.commit()
            print(f'Household "{args.household}" (id={household.id})\n')

        print(f"{'username':<20} {'temporary password':<20}")
        print("-" * 40)
        for uname in args.usernames:
            existing = User.query.filter_by(username=uname).first()
            if existing:
                if household and existing.household_id != household.id:
                    existing.household_id = household.id
                    db.session.commit()
                    print(f"{uname:<20} (already exists, linked to household)")
                else:
                    print(f"{uname:<20} (already exists, skipped)")
                continue
            pw = make_password()
            user = User(username=uname, must_change_password=True,
                        household_id=household.id if household else None)
            user.set_password(pw)
            db.session.add(user)
            db.session.commit()
            print(f"{uname:<20} {pw:<20}")
        print("\nShare each password with its person over a secure channel. "
              "They'll be forced to set their own password on first login.")


if __name__ == "__main__":
    main()
