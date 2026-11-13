"""Expected request failures, shared without importing their owners."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .validation import ValidationIssue


class AuthoringError(ValueError):
    """A safe, structured conflict (never include document contents in logs)."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


class RegistryValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(issues[0].message)
