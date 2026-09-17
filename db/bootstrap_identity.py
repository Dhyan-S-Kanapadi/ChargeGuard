"""Explicit offline provisioning of the first platform administrator (never automatic)."""
import argparse
import os
from uuid import UUID

from core.identity import supabase_url
from core.runtime import runtime_environment
from db.identity import IdentityStore
from db.postgres import PostgresStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id", type=UUID, help="Existing, operator-verified Supabase user UUID")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Dry run: would provision the first platform administrator; no writes or provider calls.")
        return
    try:
        repository = PostgresStore(os.environ["DATABASE_URL"], environment=runtime_environment())
        with IdentityStore(repository).transaction() as connection:
            if connection.execute("SELECT 1 FROM app_users WHERE platform_admin").fetchone():
                raise ValueError("An administrator already exists")
            connection.execute("INSERT INTO app_users(user_id,issuer,platform_admin) VALUES (%s,%s,true)",
                               (str(args.user_id), supabase_url() + "/auth/v1"))
            IdentityStore._audit(connection, str(args.user_id), "offline_admin_bootstrap", "completed")
        print("Administrator provisioned. MFA is required. No merchant access was granted.")
    except Exception:
        parser.exit(1, "Bootstrap failed; verify environment, migrations, account UUID and existing administrator.\n")


if __name__ == "__main__":
    main()
