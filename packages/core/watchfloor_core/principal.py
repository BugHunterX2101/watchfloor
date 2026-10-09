"""The authenticated caller: a user, or a deep-triage job acting for one."""

from __future__ import annotations

from dataclasses import dataclass, field


class Scope:
    READ = "watchfloor.read"
    WRITE = "watchfloor.write"
    AGENT_READ = "watchfloor.agent.read"


USER_SCOPES = frozenset({Scope.READ, Scope.WRITE})
ALL_SCOPES = frozenset({Scope.READ, Scope.WRITE, Scope.AGENT_READ})


@dataclass(frozen=True)
class Principal:
    user_id: str
    workspace_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    job_id: str | None = None
    subject: str = ""

    def is_agent(self) -> bool:
        return self.job_id is not None or Scope.AGENT_READ in self.scopes

    def can(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def rate_subject(self) -> str:
        return self.subject or self.user_id
