"""Pydantic I/O models for every MCP tool (DD §6.4, §6.5).

Input models forbid unknown fields (``additionalProperties: false``). Optional
arguments default to ``None`` and are omitted from results when unset, so the
published schemas stay flat. Output models describe ``structuredContent``; the
first field is always a speakable ``summary``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

OpenStage = Literal["discovery", "demo", "proposal", "negotiation", "closing"]
Segment = Literal["smb", "mid", "enterprise"]
BandName = Literal["at_risk", "watch", "healthy"]
ActionType = Literal["create_task", "reassign_owner", "notify_channel", "draft_followup"]

DealId = Annotated[
    str,
    Field(pattern=r"^dl_[0-9A-Za-z]{1,40}$", description="A deal_id from get_risk_briefing or lookup_deal."),
]
Channel = Annotated[
    str, Field(pattern=r"^#[a-z0-9][a-z0-9_-]{0,79}$", description="Slack channel name, e.g. #deals-at-risk.")
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- inputs


class BriefingIn(_In):
    limit: Annotated[
        int, Field(ge=1, le=5, description="Number of deals. Defaults to the user's preference (3).")
    ] = None  # type: ignore[assignment]
    segment: Annotated[
        Literal["smb", "mid", "enterprise", "all"],
        Field(description="Filter by customer segment. Defaults to the user's focus segment."),
    ] = None  # type: ignore[assignment]


class ExplainIn(_In):
    deal_id: DealId


class LookupIn(_In):
    name_query: Annotated[
        str, Field(min_length=2, max_length=80, description="Company or deal name as the user said it.")
    ]
    owner_name: Annotated[
        str, Field(max_length=80, description="Optional deal owner's name to narrow the match.")
    ] = None  # type: ignore[assignment]


class StartTriageIn(_In):
    deal_id: DealId


class GetTriageIn(_In):
    job_id: Annotated[
        str, Field(pattern=r"^job_[0-9A-Za-z]{1,40}$", description="A job_id from start_deep_triage.")
    ] = None  # type: ignore[assignment]
    deal_id: Annotated[
        str, Field(pattern=r"^dl_[0-9A-Za-z]{1,40}$", description="Returns the latest review for this deal.")
    ] = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _one_required(self) -> GetTriageIn:
        if not self.job_id and not self.deal_id:
            raise ValueError("provide job_id or deal_id")
        return self


class ProposeIn(_In):
    deal_id: DealId
    action_type: ActionType
    assignee_name: Annotated[
        str, Field(max_length=80, description="create_task: teammate first name or full name.")
    ] = None  # type: ignore[assignment]
    title: Annotated[
        str, Field(min_length=3, max_length=120, description="create_task: what needs doing.")
    ] = None  # type: ignore[assignment]
    due_date: Annotated[date, Field(description="create_task: ISO date in the user's timezone.")] = None  # type: ignore[assignment]
    new_owner_name: Annotated[str, Field(max_length=80, description="reassign_owner: teammate name.")] = None  # type: ignore[assignment]
    message: Annotated[
        str, Field(min_length=3, max_length=300, description="notify_channel: message to post.")
    ] = None  # type: ignore[assignment]
    intent: Annotated[
        str, Field(min_length=3, max_length=200, description="draft_followup: what the email should achieve.")
    ] = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _per_type_fields(self) -> ProposeIn:
        need = {
            "create_task": ("assignee_name", "title"),
            "reassign_owner": ("new_owner_name",),
            "notify_channel": ("message",),
            "draft_followup": ("intent",),
        }[self.action_type]
        missing = [f for f in need if not getattr(self, f)]
        if missing:
            raise ValueError(f"{self.action_type} requires: {', '.join(missing)}")
        return self


class ConfirmIn(_In):
    confirmation_token: Annotated[
        str,
        Field(
            min_length=10,
            max_length=100,
            description="The confirmation_token from propose_action or log_deal_update.",
        ),
    ]


class SetRuleIn(_In):
    min_amount_usd: Annotated[
        int, Field(ge=0, le=100_000_000, description="Only deals at or above this amount in US dollars.")
    ] = None  # type: ignore[assignment]
    quiet_days: Annotated[
        int, Field(ge=1, le=60, description="Flag deals with no reply from the buyer for this many days.")
    ] = None  # type: ignore[assignment]
    stages: Annotated[
        list[OpenStage], Field(min_length=1, max_length=5, description="Only deals in these stages.")
    ] = None  # type: ignore[assignment]
    min_score: Annotated[
        int, Field(ge=1, le=100, description="Only deals with a RIGS risk score at or above this.")
    ] = None  # type: ignore[assignment]
    segment: Annotated[Segment, Field(description="Only deals in this customer segment.")] = None  # type: ignore[assignment]
    channel: Channel = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _at_least_one_condition(self) -> SetRuleIn:
        if all(
            getattr(self, f) is None
            for f in ("min_amount_usd", "quiet_days", "stages", "min_score", "segment")
        ):
            raise ValueError("a standing order needs at least one condition")
        return self


class ListRulesIn(_In):
    pass


class RemoveRuleIn(_In):
    rule_id: Annotated[
        str, Field(pattern=r"^rule_[0-9A-Za-z]{1,40}$", description="A rule_id from list_standing_orders.")
    ]


class LogUpdateIn(_In):
    deal_id: DealId
    note: Annotated[str, Field(max_length=1000, description="What happened, in the user's words.")] = None  # type: ignore[assignment]
    next_step: Annotated[str, Field(max_length=200, description="The agreed next step.")] = None  # type: ignore[assignment]
    next_step_date: Annotated[date, Field(description="ISO date of the next step.")] = None  # type: ignore[assignment]
    close_date: Annotated[
        date, Field(description="New expected close or decision date. Needs confirmation.")
    ] = None  # type: ignore[assignment]
    amount_usd: Annotated[
        int, Field(ge=0, le=100_000_000, description="New deal amount in US dollars. Needs confirmation.")
    ] = None  # type: ignore[assignment]
    stakeholders_mentioned: Annotated[
        list[Annotated[str, Field(max_length=80)]],
        Field(max_length=5, description="People named in the update."),
    ] = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _something_to_log(self) -> LogUpdateIn:
        fields = ("note", "next_step", "next_step_date", "close_date", "amount_usd", "stakeholders_mentioned")
        if all(getattr(self, f) in (None, "", []) for f in fields):
            raise ValueError("nothing to log: provide a note, next step, date or amount")
        return self


class PrefsIn(_In):
    briefing_limit: Annotated[int, Field(ge=1, le=5, description="How many deals a briefing includes.")] = (
        None  # type: ignore[assignment]
    )
    include_amounts: Annotated[bool, Field(description="Whether briefings say deal amounts.")] = None  # type: ignore[assignment]
    focus_segment: Annotated[
        Literal["smb", "mid", "enterprise", "all"],
        Field(description="Default segment for briefings; 'all' clears it."),
    ] = None  # type: ignore[assignment]
    alert_channel: Channel = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _at_least_one(self) -> PrefsIn:
        if all(
            getattr(self, f) is None
            for f in ("briefing_limit", "include_amounts", "focus_segment", "alert_channel")
        ):
            raise ValueError("provide at least one preference to change")
        return self


class TimelineIn(_In):
    deal_id: DealId
    days: Annotated[int, Field(ge=1, le=90)] = 90


class HistoryIn(_In):
    deal_id: DealId


class AccountContextIn(_In):
    account_id: Annotated[str, Field(pattern=r"^ac_[0-9A-Za-z]{1,40}$")]


class PriorTriageIn(_In):
    deal_id: DealId
    limit: Annotated[int, Field(ge=1, le=3)] = 3


class PlaybookIn(_In):
    pass


# ---------------------------------------------------------------- outputs


class BriefingDeal(_Out):
    deal_id: str
    name: str
    account: str
    amount_cents: int | None = None
    amount_display: str | None = None
    stage: str
    owner: str
    score: Annotated[int, Field(ge=0, le=100)]
    band: BandName
    top_reason: str
    new_since_last_briefing: bool


class TriggeredRule(_Out):
    rule_id: str
    description: str
    deal_ids: list[str]


class BriefingOut(_Out):
    summary: str
    all_clear: bool
    generated_at: str
    new_count: int
    attention_count: int
    triggered_rules: list[TriggeredRule]
    deals: Annotated[list[BriefingDeal], Field(max_length=5)]


class DealCard(_Out):
    deal_id: str
    account_id: str
    name: str
    account: str
    amount_cents: int | None = None
    amount_display: str | None = None
    stage: str
    owner: str
    close_date: str
    close_date_display: str
    next_step: str | None = None
    next_step_date: str | None = None


class RiskBlock(_Out):
    score: int
    band: BandName
    band_since: str
    components: dict[str, float]
    weights: dict[str, float]
    model_version: str
    computed_at: str


class ReasonOut(_Out):
    code: str
    factor: Literal["R", "I", "G", "S"]
    contribution: float
    text: str
    evidence_ids: list[str]


class FindingOut(_Out):
    code: str
    factor: Literal["R", "I", "G", "S"]
    text: str


class ExplainOut(_Out):
    summary: str
    deal: DealCard
    risk: RiskBlock
    reasons: list[ReasonOut]
    other_findings: list[FindingOut]
    trend_14d: list[int]


class Candidate(_Out):
    deal_id: str
    name: str
    account: str
    amount_display: str
    owner: str
    stage: str
    match_score: float


class LookupOut(_Out):
    summary: str
    candidates: Annotated[list[Candidate], Field(max_length=3)]


class StartTriageOut(_Out):
    summary: str
    job_id: str
    status: Literal["queued", "running", "done"]
    eta_seconds: int
    deduplicated: bool


class DriverOut(_Out):
    factor: Literal["R", "I", "G", "S"]
    claim: str
    evidence_ids: list[str]


class RecommendedActionOut(_Out):
    action_type: ActionType
    params: dict[str, str]
    rationale: str
    play_id: str
    confidence: float


class TriageResultOut(_Out):
    summary: str
    job_id: str
    deal_id: str
    status: Literal["queued", "running", "done", "failed", "timed_out"]
    voice_summary: str | None = None
    drivers: list[DriverOut] = []
    recommended_actions: list[RecommendedActionOut] = []
    finished_at: str | None = None


class ProposalOut(_Out):
    summary: str
    requires_confirmation: bool
    proposal_id: str
    confirmation_token: str
    expires_at: str
    preview: dict[str, object]


class ConfirmOut(_Out):
    summary: str
    status: Literal["executed", "already_executed"]
    action_type: str
    result: dict[str, object]
    audit_id: str
    side_effects: list[str]


class RuleOut(_Out):
    rule_id: str
    description: str
    created_at: str
    last_triggered_at: str | None = None


class SetRuleOut(_Out):
    summary: str
    rule_id: str
    description: str
    channel: str


class ListRulesOut(_Out):
    summary: str
    rules: list[RuleOut]


class RemoveRuleOut(_Out):
    summary: str
    removed: bool
    description: str


class LogUpdateOut(_Out):
    summary: str
    applied_fields: list[str]
    score_before: int
    score_after: int
    band_after: BandName
    pending_proposal: ProposalOut | None = None


class PreferencesBlock(_Out):
    briefing_limit: int
    include_amounts: bool
    focus_segment: Segment | None = None
    alert_channel: str


class PrefsOut(_Out):
    summary: str
    preferences: PreferencesBlock


class TimelineItem(_Out):
    activity_id: str
    type: str
    direction: str
    occurred_at: str
    summary: str
    body: str | None = None


class TimelineOut(_Out):
    summary: str
    deal_id: str
    activities: list[TimelineItem]


class HistoryItem(_Out):
    history_id: str
    field: str
    old_value: str | None = None
    new_value: str | None = None
    changed_at: str


class HistoryOut(_Out):
    summary: str
    deal_id: str
    changes: list[HistoryItem]


class ContactOut(_Out):
    contact_id: str
    name: str
    title: str | None = None
    is_economic_buyer: bool


class OtherDeal(_Out):
    deal_id: str
    name: str
    stage: str
    amount_display: str


class AccountContextOut(_Out):
    summary: str
    account_id: str
    name: str
    segment: str
    contacts: list[ContactOut]
    open_deals: list[OtherDeal]
    won_count: int
    lost_count: int


class PriorTriageItem(_Out):
    job_id: str
    finished_at: str | None = None
    voice_summary: str | None = None
    recommended_play_ids: list[str]
    outcomes: list[str]


class PriorTriageOut(_Out):
    summary: str
    deal_id: str
    reviews: list[PriorTriageItem]


class Play(_Out):
    id: str
    name: str
    triggers: list[str]
    guidance: str
    guardrail: str | None = None
    action_types: list[str]


class PlaybookOut(_Out):
    summary: str
    plays: list[Play]
