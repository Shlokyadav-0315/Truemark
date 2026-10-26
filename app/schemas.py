"""API contract shared by the backend and the Next.js UI."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Channel = Literal["web", "social", "email", "press"]
DocType = Literal["guideline", "facts"]
Status = Literal["verified", "unverified_claim", "violation"]
Severity = Literal["info", "low", "medium", "high"]
Source = Literal["rule", "guideline", "fact"]
DecisionValue = Literal["pending", "accepted", "edited", "rejected", "escalated"]


class Citation(BaseModel):
    doc_id: str
    doc_name: str
    doc_type: DocType
    section: str
    chunk_id: str
    quote: str


class RewriteOption(BaseModel):
    text: str
    rationale: str = ""
    # Region of the draft this option replaces. Sentence-level for LLM rewrites,
    # span-level for deterministic fixes, zero-width for inserted disclaimers.
    start: int
    end: int
    kind: Literal["deterministic", "llm"] = "llm"


class AppliedEdit(BaseModel):
    start: int
    end: int
    text: str


class Finding(BaseModel):
    id: str
    start: int
    end: int
    text: str
    granularity: Literal["span", "sentence"] = "span"
    sentence_id: int = 0
    source: Source
    status: Status
    severity: Severity
    category: str = ""
    rule_id: Optional[str] = None
    title: str
    explanation: str
    citation: Optional[Citation] = None
    rewrites: list[RewriteOption] = Field(default_factory=list)
    decision: DecisionValue = "pending"
    applied: Optional[AppliedEdit] = None
    comment: Optional[str] = None
    decided_at: Optional[str] = None


class AuditEvent(BaseModel):
    ts: str
    type: str
    finding_id: Optional[str] = None
    detail: str = ""


class AuditSummary(BaseModel):
    total: int = 0
    violations: int = 0
    unverified: int = 0
    verified: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class Audit(BaseModel):
    audit_id: str
    created_at: str
    workspace_id: str
    channel: Channel
    draft: str
    findings: list[Finding]
    summary: AuditSummary
    # "ok", "skipped" (user disabled), or "degraded: <reason>" when the LLM was unavailable
    llm_status: str = "ok"
    retrieval_backend: str = "tfidf"
    elapsed_ms: int = 0
    needs_approval: bool = False
    events: list[AuditEvent] = Field(default_factory=list)


class AuditRequest(BaseModel):
    draft: str = Field(min_length=1, max_length=50000)
    channel: Channel = "web"
    use_llm: bool = True


class DecisionRequest(BaseModel):
    decision: Literal["accepted", "edited", "rejected", "escalated"]
    rewrite_index: Optional[int] = None
    final_text: Optional[str] = Field(default=None, max_length=3000)
    comment: Optional[str] = Field(default=None, max_length=500)


class RewriteResponse(BaseModel):
    finding_id: str
    intent: str = ""
    options: list[RewriteOption]
    llm_status: str = "ok"


class DocumentInfo(BaseModel):
    doc_id: str
    name: str
    doc_type: DocType
    chunk_count: int
    is_sample: bool


class DemoDraft(BaseModel):
    id: str
    title: str
    channel: Channel
    text: str
