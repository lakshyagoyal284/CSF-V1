"""Secure bootstrap script for provisioning admin / bank-officer accounts.

Usage:
    python scripts/bootstrap_admin.py --email banker@agritrust.ai --name "Priya Nair" --password "YourStrongPassword123"
    python scripts/bootstrap_admin.py --email banker@agritrust.ai --generate-password

This replaces hardcoded seeding of bank-officer credentials in database initialization (C1).
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import get_settings
from backend.portal_db import get_session_factory, init_portal_db
from backend.portal_models import AuditLog, User
from backend.auth import hash_password
from sqlalchemy import select


def generate_strong_password(length: int = 24) -> str:
    """Generate a high-entropy password suitable for administrative accounts."""
    import string
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    # Ensure at least one lowercase, uppercase, digit, and symbol
    pwd = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    pwd += [secrets.choice(alphabet) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(pwd)
    return "".join(pwd)


def bootstrap_user(
    email: str,
    password: str,
    full_name: str = "Bank Officer",
    role: str = "banker",
    phone: str | None = None,
) -> User:
    """Provision or update a bank-officer / admin user in the database."""
    init_portal_db(seed=False)
    session_factory = get_session_factory()

    with session_factory() as db:
        existing = db.execute(
            select(User).where(User.email == email.lower())
        ).scalar_one_or_none()

        pw_hash = hash_password(password)

        if existing is not None:
            existing.password_hash = pw_hash
            existing.role = role
            existing.full_name = full_name
            if phone:
                existing.phone = phone
            existing.is_active = True
            user = existing
            action = "bootstrap_user_updated"
        else:
            user = User(
                email=email.lower(),
                password_hash=pw_hash,
                role=role,
                full_name=full_name,
                phone=phone or "+91 90000 11111",
                is_active=True,
            )
            db.add(user)
            action = "bootstrap_user_created"

        db.flush()
        db.add(
            AuditLog(
                application_id=None,
                actor_id=user.id,
                actor_role="admin",
                action=action,
                details={"email": user.email, "role": role},
            )
        )
        db.commit()
        db.refresh(user)
        return user


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision an admin or bank-officer account securely."
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Email address for the bank officer or administrator.",
    )
    parser.add_argument(
        "--name",
        default="Bank Officer",
        help="Full name of the user (default: 'Bank Officer').",
    )
    parser.add_argument(
        "--password",
        help="Password for the user. If omitted and --generate-password not passed, will prompt.",
    )
    parser.add_argument(
        "--generate-password",
        action="store_true",
        help="Automatically generate a cryptographically strong password.",
    )
    parser.add_argument(
        "--role",
        default="banker",
        choices=["banker", "borrower"],
        help="Role to assign (default: banker).",
    )
    parser.add_argument(
        "--phone",
        default=None,
        help="Contact phone number.",
    )

    args = parser.parse_args()

    password = args.password
    if args.generate_password or not password:
        if args.generate_password:
            password = generate_strong_password()
        else:
            import getpass
            password = getpass.getpass("Enter password for new user: ")
            if not password:
                print("Error: password cannot be empty.", file=sys.stderr)
                return 1

    try:
        user = bootstrap_user(
            email=args.email,
            password=password,
            full_name=args.name,
            role=args.role,
            phone=args.phone,
        )
        print(f"Successfully provisioned {user.role} user: {user.email} (id: {user.id})")
        if args.generate_password or args.password:
            print(f"Password: {password}")
        return 0
    except Exception as exc:
        print(f"Failed to provision user: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
