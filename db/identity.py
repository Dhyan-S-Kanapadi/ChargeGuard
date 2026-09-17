"""Durable memberships and local session revocation. Never stores access tokens."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import HTTPException

from core.runtime import RuntimeConfigurationError
from db.migrate import connect, STORE_LOCK


@dataclass(frozen=True)
class Principal:
    user_id: str
    session_id: str
    aal: str
    platform_admin: bool
    memberships: dict[str, str]


class IdentityStore:
    def __init__(self, repository):
        self.repository = repository

    @contextmanager
    def transaction(self):
        try:
            with connect(self.repository.database_url) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (STORE_LOCK,))
                self.repository._check_environment(connection)
                yield connection
        except psycopg.IntegrityError:
            raise HTTPException(409, "Identity or membership conflict.") from None
        except psycopg.Error:
            raise RuntimeConfigurationError("Identity storage is unavailable; verify state before retrying.") from None

    @staticmethod
    def _audit(connection, actor, action, result, merchant=None, session=None, target=None):
        connection.execute(
            "INSERT INTO access_audit(actor_id,session_id,merchant_id,action,result,target_user_id) VALUES (%s,%s,%s,%s,%s,%s)",
            (actor, session, merchant, action, result, target))

    def authenticate(self, claims):
        with self.transaction() as connection:
            user = connection.execute("SELECT * FROM app_users WHERE user_id=%s AND issuer=%s",
                                      (claims["sub"], claims["iss"])).fetchone()
            if not user or not user["active"]:
                raise HTTPException(403, "Account is not provisioned or is disabled.")
            session = connection.execute("SELECT * FROM app_sessions WHERE session_id=%s", (claims["session_id"],)).fetchone()
            now = datetime.now(timezone.utc)
            if session and (str(session["user_id"]) != claims["sub"] or session["revoked_at"]
                            or now - session["first_seen_at"] >= timedelta(hours=8)
                            or now - session["last_seen_at"] >= timedelta(minutes=30)):
                raise HTTPException(401, "Session expired or revoked; sign in again.")
            if not session:
                connection.execute("INSERT INTO app_sessions(session_id,user_id) VALUES (%s,%s)",
                                   (claims["session_id"], claims["sub"]))
                self._audit(connection, claims["sub"], "session_started", "allowed", session=claims["session_id"])
            else:
                connection.execute("UPDATE app_sessions SET last_seen_at=now() WHERE session_id=%s", (claims["session_id"],))
            memberships = {row["merchant_id"]: row["role"] for row in connection.execute(
                "SELECT merchant_id,role FROM merchant_memberships WHERE user_id=%s", (claims["sub"],))}
            return Principal(claims["sub"], claims["session_id"], claims["aal"], user["platform_admin"], memberships)

    def audit(self, principal, action, result, merchant=None):
        with self.transaction() as connection:
            self._audit(connection, principal.user_id, action, result, merchant, principal.session_id)

    def revoke_session(self, principal):
        with self.transaction() as connection:
            connection.execute("UPDATE app_sessions SET revoked_at=now() WHERE session_id=%s AND user_id=%s",
                               (principal.session_id, principal.user_id))
            self._audit(connection, principal.user_id, "session_revoked", "completed", session=principal.session_id)

    def _current_actor(self, connection, principal):
        actor = connection.execute("SELECT * FROM app_users WHERE user_id=%s AND active", (principal.user_id,)).fetchone()
        session = connection.execute("SELECT revoked_at FROM app_sessions WHERE session_id=%s AND user_id=%s",
                                     (principal.session_id, principal.user_id)).fetchone()
        if not actor or not session or session["revoked_at"] or principal.aal != "aal2":
            raise HTTPException(403, "Active account and MFA required.")
        return actor

    def provision_user(self, principal, user_id, issuer, active):
        with self.transaction() as connection:
            actor = self._current_actor(connection, principal)
            if not actor["platform_admin"] or principal.user_id == user_id:
                raise HTTPException(403, "Separate platform administration required.")
            existing = connection.execute("SELECT * FROM app_users WHERE user_id=%s", (user_id,)).fetchone()
            if existing and (existing["issuer"] != issuer or existing["platform_admin"]):
                raise HTTPException(409, "Platform administrators require the offline administration procedure.")
            connection.execute("INSERT INTO app_users(user_id,issuer,active) VALUES (%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET active=EXCLUDED.active",
                               (user_id, issuer, active))
            if not active:
                connection.execute("UPDATE app_sessions SET revoked_at=now() WHERE user_id=%s", (user_id,))
            self._audit(connection, principal.user_id, "account_enabled" if active else "account_disabled", "completed", target=user_id)

    def membership(self, principal, merchant, user_id, role):
        with self.transaction() as connection:
            actor = self._current_actor(connection, principal)
            membership = connection.execute("SELECT role FROM merchant_memberships WHERE merchant_id=%s AND user_id=%s",
                                            (merchant, principal.user_id)).fetchone()
            if not actor["platform_admin"] and (not membership or membership["role"] != "owner"):
                raise HTTPException(403, "Merchant owner required.")
            target = connection.execute("SELECT * FROM app_users WHERE user_id=%s AND active", (user_id,)).fetchone()
            if not target or target["platform_admin"]:
                raise HTTPException(409, "Provision an active non-administrator account first.")
            previous = connection.execute("SELECT role FROM merchant_memberships WHERE merchant_id=%s AND user_id=%s", (merchant, user_id)).fetchone()
            owners = connection.execute("SELECT count(*) AS n FROM merchant_memberships WHERE merchant_id=%s AND role='owner'", (merchant,)).fetchone()["n"]
            if previous and previous["role"] == "owner" and role != "owner" and owners <= 1:
                raise HTTPException(409, "Cannot remove the last merchant owner.")
            if role is None:
                connection.execute("DELETE FROM merchant_memberships WHERE merchant_id=%s AND user_id=%s", (merchant, user_id))
            else:
                connection.execute("INSERT INTO merchant_memberships(merchant_id,user_id,role) VALUES (%s,%s,%s) ON CONFLICT(merchant_id,user_id) DO UPDATE SET role=EXCLUDED.role", (merchant, user_id, role))
            self._audit(connection, principal.user_id, "membership_removed" if role is None else "membership_" + role, "completed", merchant, target=user_id)
