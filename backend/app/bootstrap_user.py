"""Create the first administrator account from an operator-provided secret file."""

import argparse
from pathlib import Path

from sqlalchemy import func, select

from .auth import Credentials, hash_password
from .db import SessionLocal
from .models import User


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password-file", required=True)
    args = parser.parse_args()
    password = Path(args.password_file).read_text(encoding="utf-8").rstrip("\r\n")
    credentials = Credentials(email=args.email, password=password)
    with SessionLocal() as db:
        if db.scalar(select(func.count(User.id))) != 0:
            raise SystemExit("Refusing bootstrap: an account already exists")
        db.add(
            User(
                email=credentials.email,
                password_hash=hash_password(credentials.password),
                is_active=True,
            )
        )
        db.commit()
    print(f"Initial account created for {credentials.email}; sign in and create a workspace.")


if __name__ == "__main__":
    main()
