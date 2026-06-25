from __future__ import annotations

import argparse
import json
import os
from typing import Sequence

from sqlalchemy.orm import Session

from .auth import upsert_user
from .db import create_session_factory
from .models import User
from .settings import get_settings

INITIAL_ROLES = ("admin", "submitter", "reviewer")
DEFAULT_BOOTSTRAP_DISPLAY_NAME = "Alfi"


def bootstrap_first_user(
    session: Session,
    *,
    email: str,
    pin: str,
    display_name: str = DEFAULT_BOOTSTRAP_DISPLAY_NAME,
    roles: Sequence[str] = INITIAL_ROLES,
) -> User:
    return upsert_user(
        session,
        email=email,
        display_name=display_name,
        pin=pin,
        roles=roles,
        must_change_pin=True,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Symgov application users.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap-first-user", help="Create or update the initial Alfi account.")
    bootstrap.add_argument("--email", default=os.environ.get("SYMGOV_BOOTSTRAP_EMAIL"))
    bootstrap.add_argument("--pin", default=os.environ.get("SYMGOV_BOOTSTRAP_PIN"))
    bootstrap.add_argument("--display-name", default=os.environ.get("SYMGOV_BOOTSTRAP_DISPLAY_NAME", DEFAULT_BOOTSTRAP_DISPLAY_NAME))
    bootstrap.add_argument("--role", action="append", dest="roles", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "bootstrap-first-user":
        if not args.email:
            raise SystemExit("Missing --email or SYMGOV_BOOTSTRAP_EMAIL.")
        if not args.pin:
            raise SystemExit("Missing --pin or SYMGOV_BOOTSTRAP_PIN.")
        settings = get_settings()
        session_factory = create_session_factory(env_file=settings.db_env_file)
        with session_factory() as session:
            user = bootstrap_first_user(
                session,
                email=args.email,
                pin=args.pin,
                display_name=args.display_name,
                roles=tuple(args.roles or INITIAL_ROLES),
            )
            session.commit()
            print(
                json.dumps(
                    {
                        "id": str(user.id),
                        "email": user.email,
                        "displayName": user.display_name,
                        "roles": sorted(role.role for role in user.roles),
                        "mustChangePin": bool(user.must_change_pin),
                    },
                    indent=2,
                )
            )
            return
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
