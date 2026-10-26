"""Deterministic rule linter. No LLM, no network: it always works, even when the
LLM is rate limited, and it finishes in a few milliseconds.

Every rule cites a real guideline chunk. `load_rules` refuses to start if a rule's
quote cannot be found verbatim in the guideline document, so a hallucinated or
stale citation cannot ship."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .chunking import Chunk
from .textutil import Sentence, quote_in_text, sentence_at


@dataclass
class Rule:
    id: str
    type: str
    category: str
    severity: str
    title: str
    explanation: str
    channels: list[str] | None
    pattern: re.Pattern[str] | None
    max_count: int | None
    replacement: str | None
    trigger: re.Pattern[str] | None
    required: re.Pattern[str] | None
    insert: str | None
    citation: dict[str, str]  # chunk_id, doc_id, doc_name, section, quote


@dataclass
class RuleHit:
    rule_id: str
    start: int
    end: int
    text: str
    severity: str
    category: str
    title: str
    explanation: str
    citation: dict[str, str]
    rule_type: str
    replacement: str | None = None  # replace [start:end] with this
    insert_text: str | None = None  # insert after the sentence containing the hit
    sentence_id: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _flags(s: str) -> int:
    f = 0
    if "i" in s:
        f |= re.IGNORECASE
    return f | re.UNICODE


def _resolve_citation(section_no: str, quote: str, chunks: list[Chunk]) -> dict[str, str]:
    for ch in chunks:
        last = ch.section.split(" > ")[-1]
        if last.startswith(section_no + " ") and quote_in_text(quote, ch.citable):
            return {
                "chunk_id": ch.chunk_id,
                "doc_id": ch.doc_id,
                "doc_name": ch.doc_name,
                "section": ch.section,
                "quote": quote,
            }
    raise ValueError(f"Rule citation not found in guidelines: section {section_no!r}, quote {quote!r}")


def load_rules(path: Path, guideline_chunks: list[Chunk]) -> list[Rule]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules: list[Rule] = []
    for r in raw:
        g = r["guideline"]
        flags = _flags(r.get("flags", ""))
        rules.append(
            Rule(
                id=r["id"],
                type=r["type"],
                category=r.get("category", "tone"),
                severity=r["severity"],
                title=r["title"],
                explanation=r["explanation"],
                channels=r.get("channels"),
                pattern=re.compile(r["pattern"], flags) if r.get("pattern") else None,
                max_count=r.get("max"),
                replacement=r.get("replacement"),
                trigger=re.compile(r["trigger"], flags) if r.get("trigger") else None,
                required=re.compile(re.escape(r["required"]), re.IGNORECASE) if r.get("required") else None,
                insert=r.get("insert"),
                citation=_resolve_citation(g["section"], g["quote"], guideline_chunks),
            )
        )
    return rules


def lint(draft: str, channel: str, rules: list[Rule], sentences: list[Sentence]) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for rule in rules:
        if rule.channels and channel not in rule.channels:
            continue

        def make(start: int, end: int, **kw: Any) -> RuleHit:
            sent = sentence_at(sentences, start)
            return RuleHit(
                rule_id=rule.id,
                start=start,
                end=end,
                text=draft[start:end],
                severity=rule.severity,
                category=rule.category,
                title=rule.title,
                explanation=rule.explanation,
                citation=rule.citation,
                rule_type=rule.type,
                sentence_id=sent.id if sent else 0,
                **kw,
            )

        if rule.type == "term_regex" and rule.pattern:
            for m in rule.pattern.finditer(draft):
                if m.end() > m.start():
                    hits.append(make(m.start(), m.end(), replacement=rule.replacement))
        elif rule.type == "max_count" and rule.pattern and rule.max_count is not None:
            for i, m in enumerate(rule.pattern.finditer(draft)):
                if i >= rule.max_count and m.end() > m.start():
                    hits.append(make(m.start(), m.end(), replacement=rule.replacement))
        elif rule.type == "required_disclaimer" and rule.trigger and rule.required:
            first = rule.trigger.search(draft)
            if first and not rule.required.search(draft):
                sent = sentence_at(sentences, first.start())
                hit = make(first.start(), first.end(), insert_text=rule.insert)
                if sent:
                    hit.extra["insert_at"] = sent.end
                hits.append(hit)

    return _dedupe(hits)


def _dedupe(hits: list[RuleHit]) -> list[RuleHit]:
    """Drop later hits that cover exactly the same span (e.g. '!!' matches two rules)."""
    seen: set[tuple[int, int]] = set()
    out: list[RuleHit] = []
    for h in sorted(hits, key=lambda h: (h.start, h.end)):
        key = (h.start, h.end)
        if h.rule_type != "required_disclaimer" and key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out
