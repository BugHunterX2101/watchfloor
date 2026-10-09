"""Business errors returned as tool results with ``isError: true`` (DD §5.5).

Protocol misuse is a JSON-RPC error and transport rejections are HTTP statuses;
this module covers only the third layer: outcomes the model can recover from.
Every error carries a next step, because voice responses must never dead-end.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    DEAL_NOT_FOUND = "DEAL_NOT_FOUND"
    AMBIGUOUS_DEAL = "AMBIGUOUS_DEAL"
    AMBIGUOUS_ASSIGNEE = "AMBIGUOUS_ASSIGNEE"
    UNKNOWN_ASSIGNEE = "UNKNOWN_ASSIGNEE"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_ALREADY_USED = "TOKEN_ALREADY_USED"
    TOKEN_MISMATCH = "TOKEN_MISMATCH"
    INVALID_DATE = "INVALID_DATE"
    INVALID_ACTION = "INVALID_ACTION"
    RULE_LIMIT_REACHED = "RULE_LIMIT_REACHED"
    RULE_NOT_FOUND = "RULE_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_LIMIT_REACHED = "JOB_LIMIT_REACHED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"


class WatchfloorError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        recovery: str | None = None,
        suggestions: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery
        self.suggestions = suggestions or []

    def to_payload(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code.value, "message": self.message}
        if self.recovery:
            err["recovery"] = self.recovery
        if self.suggestions:
            err["suggestions"] = self.suggestions
        return {"summary": self.message + (f" {self.recovery}" if self.recovery else ""), "error": err}
