"""
Creates or resets household user accounts.

Usage:
    python3 seed_users.py alice bob carol

Generates a random temporary password for each username given (skips
usernames that already exist), prints them once, and sets
must_change_password=True so each person picks their own password on
first login. Run this once locally or via Render's shell after deploy.
"""
import sys
import secrets
from app import create_app, db
from app.models import User


def make_password():
    return secrets.token_urlsafe(9)  # ~12 char random password


def main(usernames):
    app = create_app()
    with app.app_context():
        print(f"{'username':<20} {'temporary password':<20}")
        print("-" * 40)
        for uname in usernames:
            existing = User.query.filter_by(username=uname).first()
            if existing:
                print(f"{uname:<20} (already exists, skipped)")
                continue
            pw = make_password()
            user = User(username=uname, must_change_password=True)
            user.set_password(pw)
            db.session.add(user)
            db.session.commit()
            print(f"{uname:<20} {pw:<20}")
        print("\nShare each password with its person over a secure channel. "
              "They'll be forced to set their own password on first login.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 seed_users.py <username1> [username2] ...")
        sys.exit(1)
    main(sys.argv[1:])
