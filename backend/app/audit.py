"""Audit orchestration: rules + grounded claim verification -> findings -> human decisions -> export."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from time import perf_counter

from .config import BASE_DIR, DATA_DIR, Settings
from .llm_client import LLMUnavailable, build_llm
from .retriever import IndexManager
from .rewriter import llm_rewrite
from .rules import RuleHit, load_rules, lint
from .schemas import (
    Audit,
    AuditRequest,
    AuditSummary,
    AppliedEdit,
    Citation,
    DecisionRequest,
    Finding,
    RewriteOption,
    RewriteResponse,
)
from .store import AuditStore, now_iso
from .textutil import apply_edits, split_sentences
from .verifier import Verdict, verify

_SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


class Engine:
    """Process-wide singletons: knowledge index, rules, LLM client, audit store."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.manager = IndexManager(settings)
        self.rules = load_rules(DATA_DIR / "rules.json", self.manager.sample_guideline_chunks)
        self.llm = build_llm(settings)
        log_path = os.environ.get("AUDIT_LOG") or str(BASE_DIR / "audit_log.jsonl")
        self.store = AuditStore(log_path=Path(log_path))


# ------------------------------------------------------------------ building findings


def _finding_from_hit(h: RuleHit) -> Finding:
    f = Finding(
        id="",
        start=h.start,
        end=h.end,
        text=h.text,
        granularity="span",
        sentence_id=h.sentence_id,
        source="rule",
        status="violation",
        severity=h.severity,  # type: ignore[arg-type]
        category=h.category,
        rule_id=h.rule_id,
        title=h.title,
        explanation=h.explanation,
        citation=Citation(doc_type="guideline", **h.citation),
    )
    if h.replacement is not None:
        label = "Remove it" if h.replacement == "" else f"Replace with \"{h.replacement}\""
        f.rewrites.append(
            RewriteOption(
                text=h.replacement,
                rationale=f"{label}. This is the exact fix the guideline specifies.",
                start=h.start,
                end=h.end,
                kind="deterministic",
            )
        )
    elif h.insert_text:
        pos = int(h.extra.get("insert_at", h.end))
        f.rewrites.append(
            RewriteOption(
                text=h.insert_text,
                rationale="Adds the disclaimer the guideline requires. Edit any [placeholder] before accepting.",
                start=pos,
                end=pos,
                kind="deterministic",
            )
        )
    return f


def _finding_from_verdict(v: Verdict, sentence) -> Finding:
    citation = None
    if v.chunk is not None and v.quote:
        citation = Citation(
            doc_id=v.chunk.doc_id,
            doc_name=v.chunk.doc_name,
            doc_type=v.chunk.doc_type,  # type: ignore[arg-type]
            section=v.chunk.section,
            chunk_id=v.chunk.chunk_id,
            quote=v.quote,
        )
    title = {
        "verified": "Figures match the fact sheet" if v.origin == "heuristic" else "Claim verified",
        "unverified_claim": "Unverified claim",
        "violation": "Conflicts with approved source",
    }[v.status]
    source = "guideline" if (v.chunk is not None and v.chunk.doc_type == "guideline") else "fact"
    return Finding(
        id="",
        start=sentence.start,
        end=sentence.end,
        text=sentence.text,
        granularity="sentence",
        sentence_id=sentence.id,
        source=source,  # type: ignore[arg-type]
        status=v.status,  # type: ignore[arg-type]
        severity=v.severity,  # type: ignore[arg-type]
        category="claim",
        title=title,
        explanation=v.explanation,
        citation=citation,
    )


def _summarize(findings: list[Finding]) -> AuditSummary:
    s = AuditSummary(total=len(findings))
    for f in findings:
        if f.status == "violation":
            s.violations += 1
        elif f.status == "unverified_claim":
            s.unverified += 1
        else:
            s.verified += 1
        if f.status != "verified":
            if f.severity == "high":
                s.high += 1
            elif f.severity == "medium":
                s.medium += 1
            elif f.severity == "low":
                s.low += 1
    return s


def compute_needs_approval(findings: list[Finding]) -> bool:
    for f in findings:
        if f.decision == "escalated":
            return True
        if f.status != "verified" and f.severity == "high" and f.decision in ("pending", "rejected"):
            return True
    return False


# ------------------------------------------------------------------ run audit


async def run_audit(engine: Engine, workspace_id: str, req: AuditRequest, allow_llm: bool = True) -> Audit:
    t0 = perf_counter()
    ws = engine.manager.get(workspace_id)
    draft = req.draft
    sentences = split_sentences(draft)
    by_id = {s.id: s for s in sentences}

    hits = lint(draft, req.channel, engine.rules, sentences)
    rule_findings = [_finding_from_hit(h) for h in hits]
    claim_rule_sentences = {f.sentence_id for f in rule_findings if f.category == "claim"}

    effective_llm = req.use_llm and allow_llm
    verdicts, llm_status = await verify(
        engine.llm, engine.manager, ws, sentences, effective_llm, engine.settings.max_claim_sentences
    )
    if req.use_llm and not allow_llm:
        llm_status = "degraded: too many AI audits in a short time, showing rule and numeric checks only"

    claim_findings: list[Finding] = []
    for v in verdicts:
        sentence = by_id.get(v.sentence_id)
        if sentence is None:
            continue
        # A rule finding already explains an unsupported superlative/guarantee; do not double-flag it.
        if v.status == "unverified_claim" and v.sentence_id in claim_rule_sentences:
            continue
        claim_findings.append(_finding_from_verdict(v, sentence))

    findings = rule_findings + claim_findings
    findings.sort(key=lambda f: (f.start, _SEV_ORDER.get(f.severity, 9), f.end))
    for i, f in enumerate(findings, start=1):
        f.id = f"F{i}"

    audit = Audit(
        audit_id=uuid.uuid4().hex[:12],
        created_at=now_iso(),
        workspace_id=workspace_id,
        channel=req.channel,
        draft=draft,
        findings=findings,
        summary=_summarize(findings),
        llm_status=llm_status,
        retrieval_backend=ws.backend,
        elapsed_ms=int((perf_counter() - t0) * 1000),
        needs_approval=compute_needs_approval(findings),
    )
    engine.store.put(audit)
    engine.store.log(
        audit, "audit_created", detail=f"{audit.summary.total} findings, llm={llm_status}, backend={ws.backend}"
    )
    return audit


# ------------------------------------------------------------------ rewrites


def _find(audit: Audit, finding_id: str) -> Finding:
    for f in audit.findings:
        if f.id == finding_id:
            return f
    raise KeyError(finding_id)


async def get_rewrites(engine: Engine, audit: Audit, finding_id: str) -> RewriteResponse:
    f = _find(audit, finding_id)
    llm_opts = [o for o in f.rewrites if o.kind == "llm"]
    if llm_opts:
        return RewriteResponse(finding_id=f.id, options=f.rewrites, llm_status="ok")
    if f.status == "verified":
        return RewriteResponse(finding_id=f.id, options=[], llm_status="ok")

    sentences = split_sentences(audit.draft)
    sentence = next((s for s in sentences if s.id == f.sentence_id), None)
    if sentence is None:
        return RewriteResponse(finding_id=f.id, options=f.rewrites, llm_status="degraded: sentence not found")
    issues = [g for g in audit.findings if g.sentence_id == f.sentence_id and g.status != "verified"]
    ws = engine.manager.get(audit.workspace_id)

    status, intent = "ok", ""
    try:
        intent, options = await llm_rewrite(
            engine.llm, engine.manager, ws, engine.rules, sentence, issues, audit.channel
        )
        f.rewrites.extend(options)
        engine.store.log(audit, "rewrites_generated", f.id, f"{len(options)} options")
    except LLMUnavailable as exc:
        status = f"degraded: {exc}"
        engine.store.log(audit, "rewrites_unavailable", f.id, str(exc))
    return RewriteResponse(finding_id=f.id, intent=intent, options=f.rewrites, llm_status=status)


# ------------------------------------------------------------------ human decisions


def _is_insertion(f: Finding) -> bool:
    return any(o.start == o.end for o in f.rewrites)


def decide(engine: Engine, audit: Audit, finding_id: str, req: DecisionRequest) -> Finding:
    f = _find(audit, finding_id)
    if f.status == "verified":
        raise ValueError("Verified claims need no decision.")

    if req.decision == "accepted":
        idx = req.rewrite_index if req.rewrite_index is not None else 0
        if not f.rewrites or not (0 <= idx < len(f.rewrites)):
            raise ValueError("No suggested rewrite to accept. Generate suggestions or edit the text instead.")
        opt = f.rewrites[idx]
        f.applied = AppliedEdit(start=opt.start, end=opt.end, text=opt.text)
    elif req.decision == "edited":
        if req.final_text is None:
            raise ValueError("final_text is required when editing.")
        if req.rewrite_index is not None:
            if not (0 <= req.rewrite_index < len(f.rewrites)):
                raise ValueError("rewrite_index out of range.")
            base = f.rewrites[req.rewrite_index]
            f.applied = AppliedEdit(start=base.start, end=base.end, text=req.final_text)
        else:
            f.applied = AppliedEdit(start=f.start, end=f.end, text=req.final_text)
    else:  # rejected / escalated leave the original text untouched
        f.applied = None

    f.decision = req.decision
    f.comment = req.comment
    f.decided_at = now_iso()
    engine.store.log(audit, f"decision_{req.decision}", f.id, req.comment or "")

    # A sentence-level replacement also resolves other open issues inside that sentence.
    if f.applied and f.applied.end > f.applied.start and req.decision in ("accepted", "edited"):
        for g in audit.findings:
            if (
                g.id != f.id
                and g.decision == "pending"
                and g.status != "verified"
                and not _is_insertion(g)
                and g.start >= f.applied.start
                and g.end <= f.applied.end
            ):
                g.decision = "accepted"
                g.applied = None
                g.comment = f"Resolved by the change to {f.id}"
                g.decided_at = f.decided_at
                engine.store.log(audit, "decision_auto_resolved", g.id, f"covered by {f.id}")

    audit.needs_approval = compute_needs_approval(audit.findings)
    return f


# ------------------------------------------------------------------ finalize & export


def final_text(audit: Audit) -> tuple[str, list[AppliedEdit]]:
    edits = [(f.applied.start, f.applied.end, f.applied.text) for f in audit.findings if f.applied]
    text, skipped = apply_edits(audit.draft, edits)
    return text, [AppliedEdit(start=s, end=e, text=t) for s, e, t in skipped]


def export_markdown(audit: Audit) -> str:
    text, skipped = final_text(audit)
    status = "NEEDS MANAGER APPROVAL" if audit.needs_approval else "Ready to publish (no unresolved high-risk issues)"
    s = audit.summary
    lines = [
        f"# TrueMark Audit {audit.audit_id}",
        "",
        f"- Created: {audit.created_at}",
        f"- Channel: {audit.channel}",
        f"- Status: **{status}**",
        f"- Findings: {s.violations} violations, {s.unverified} unverified claims, {s.verified} verified",
        f"- AI status: {audit.llm_status}; retrieval: {audit.retrieval_backend}",
        "",
        "## Final text",
        "",
        text,
        "",
        "## Original draft",
        "",
        audit.draft,
        "",
        "## Findings and decisions",
        "",
    ]
    for f in audit.findings:
        if f.status == "verified":
            continue
        lines.append(f"### {f.id} - {f.title} ({f.severity})")
        lines.append(f"- Text: `{f.text}`")
        lines.append(f"- Why: {f.explanation}")
        if f.citation:
            lines.append(
                f"- Source: {f.citation.doc_name}, {f.citation.section} - \"{f.citation.quote}\""
            )
        lines.append(f"- Decision: **{f.decision}**" + (f" ({f.comment})" if f.comment else ""))
        if f.applied:
            lines.append(f"- Applied change: `{f.applied.text}`")
        lines.append("")
    verified = [f for f in audit.findings if f.status == "verified"]
    if verified:
        lines += ["## Verified claims", ""]
        for f in verified:
            q = f" - \"{f.citation.quote}\"" if f.citation else ""
            lines.append(f"- {f.text}{q}")
        lines.append("")
    if skipped:
        lines += ["## Edits skipped because they overlapped another edit", ""]
        lines += [f"- `{e.text}`" for e in skipped]
        lines.append("")
    lines += ["## Audit trail", ""]
    lines += [f"- {e.ts} {e.type}" + (f" [{e.finding_id}]" if e.finding_id else "") + (f": {e.detail}" if e.detail else "") for e in audit.events]
    return "\n".join(lines) + "\n"
