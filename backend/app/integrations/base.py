from __future__ import annotations


class NotConfigured(RuntimeError):
    """Raised when a real external integration is asked to do work it has no
    credentials for. Never swallowed silently: it surfaces as a typed
    ``not_configured`` API error so the UI can name the missing key."""

    def __init__(self, integration: str, missing: list[str], detail: str = "") -> None:
        self.integration = integration
        self.missing = missing
        self.detail = detail
        keys = ", ".join(missing) if missing else "credentials"
        super().__init__(f"{integration} is not configured (missing: {keys}). {detail}".strip())

    def as_dict(self) -> dict:
        return {
            "error": "not_configured",
            "integration": self.integration,
            "missing": self.missing,
            "detail": self.detail,
        }
