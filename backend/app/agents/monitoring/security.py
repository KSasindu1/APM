"""Project-scoped authorization for HTTP and direct agent calls."""

import hashlib
import secrets

from .schemas import MonitoringPrincipal


class MonitoringAccessDenied(PermissionError):
    pass


def require_access(principal: MonitoringPrincipal, project_id: str, permission: str) -> None:
    if project_id not in principal.project_ids or permission not in principal.permissions:
        raise MonitoringAccessDenied("Insufficient monitoring project access")


class MonitoringTokenAuthenticator:
    """Small standalone adapter; replace with the group's verified identity dependency."""

    def __init__(self, identities: dict[str, MonitoringPrincipal]):
        if not identities or any(len(token) < 32 for token in identities):
            raise ValueError("Configure at least one bearer token of 32 or more characters")
        self._identities = [
            (hashlib.sha256(token.encode()).digest(), principal)
            for token, principal in identities.items()
        ]

    def authenticate(self, token: str) -> MonitoringPrincipal | None:
        digest = hashlib.sha256(token.encode()).digest()
        for expected, principal in self._identities:
            if secrets.compare_digest(digest, expected):
                return principal.model_copy(deep=True)
        return None
