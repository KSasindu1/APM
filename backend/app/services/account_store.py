"""Lazy service-role storage. No Supabase Auth sessions or local account fallback."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from app.core.config import settings


class AccountStoreUnavailable(Exception):
    pass


class AccountExists(Exception):
    pass


class InvalidInvitation(Exception):
    pass


@dataclass(frozen=True)
class Account:
    user_id: str
    username: str
    role: Literal["user", "admin"]
    active: bool
    password_hash: str = field(repr=False)

    @classmethod
    def from_row(cls, row: dict) -> Account:
        from app.core.account_security import canonical_username

        user_id = row["user_id"]
        username = row["username"]
        if (
            not isinstance(user_id, str) or str(UUID(user_id)) != user_id
            or canonical_username(username) != username
            or row["role"] not in ("user", "admin")
            or type(row["active"]) is not bool
            or not isinstance(row["password_hash"], str)
        ):
            raise ValueError("Invalid account record.")
        return cls(user_id, username, row["role"], row["active"], row["password_hash"])


class AccountStore:
    def __init__(self):
        self._client = None
        self._configuration = None
        self._lock = threading.Lock()

    def _get_client(self):
        try:
            if not settings.supabase_url or not settings.supabase_secret_key:
                raise ValueError
            configuration = (settings.supabase_url, settings.supabase_secret_key)
            with self._lock:
                if self._client is None or self._configuration != configuration:
                    from supabase import ClientOptions, create_client

                    self._client = create_client(
                        settings.supabase_url, settings.supabase_secret_key,
                        options=ClientOptions(auto_refresh_token=False, persist_session=False),
                    )
                    self._configuration = configuration
                return self._client
        except Exception:
            raise AccountStoreUnavailable() from None

    def find(self, username: str) -> Account | None:
        try:
            rows = self._get_client().table("auth_accounts").select(
                "user_id,username,role,active,password_hash"
            ).eq("username", username).limit(1).execute().data
            return Account.from_row(rows[0]) if rows else None
        except Exception:
            raise AccountStoreUnavailable() from None

    def create_user(self, username: str, password_hash: str) -> Account:
        try:
            rows = self._get_client().rpc("create_user_account", {
                "p_username": username, "p_password_hash": password_hash,
            }).execute().data
            return Account.from_row(rows[0])
        except Exception as exc:
            if getattr(exc, "code", None) == "23505":
                raise AccountExists() from None
            raise AccountStoreUnavailable() from None

    def create_admin(self, username: str, password_hash: str, invitation_hash: str) -> Account:
        try:
            rows = self._get_client().rpc("consume_admin_invitation", {
                "p_username": username, "p_password_hash": password_hash,
                "p_invitation_hash": invitation_hash,
            }).execute().data
            return Account.from_row(rows[0])
        except Exception as exc:
            if getattr(exc, "code", None) == "23505":
                raise AccountExists() from None
            if getattr(exc, "code", None) == "P0001":
                raise InvalidInvitation() from None
            raise AccountStoreUnavailable() from None

    def invite(self, username: str, invitation_hash: str, expires_at: str) -> None:
        try:
            self._get_client().rpc("issue_admin_invitation", {
                "p_username": username, "p_invitation_hash": invitation_hash,
                "p_expires_at": expires_at,
            }).execute()
        except Exception as exc:
            if getattr(exc, "code", None) == "23505":
                raise AccountExists() from None
            raise AccountStoreUnavailable() from None


account_store = AccountStore()


def get_account_store() -> AccountStore:
    return account_store
