"""Core pipeline tests. Run with:  python -m pytest -q   (or: python -m unittest discover -s tests)

No network and no API key needed: the LLM is replaced by a deterministic fake."""
from __future__ import annotations

import asyncio
import json
import unittest

from app.audit import Engine, decide, export_markdown, final_text, get_rewrites, run_audit
from app.chunking import chunk_markdown
from app.config import DATA_DIR, SAMPLE_FACTS_ID, SAMPLE_GUIDELINES_ID, get_settings
from app.llm_client import LLMUnavailable, parse_json_loose
from app.retriever import IndexManager
from app.rules import lint, load_rules
from app.schemas import AuditRequest, DecisionRequest
from app.textutil import apply_edits, extract_numbers, quote_in_text, split_sentences
from app.verifier import heuristic_verdicts, validate_verdicts

DRAFTS = {d["id"]: d for d in json.loads((DATA_DIR / "demo_drafts.json").read_text(encoding="utf-8"))}


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """Deterministic stand-in. mode='good' cites real quotes, 'liar' fabricates them, 'down' is unavailable."""

    name = "fake"
    live = True

    def __init__(self, mode: str = "good") -> None:
        self.mode = mode
        self.calls = 0

    async def generate_json(self, system: str, prompt: str):
        self.calls += 1
        if self.mode == "down":
            raise LLMUnavailable("The model is busy or rate limited.")
        if system.startswith("You are a compliance fact-checker"):
            evidence_part, sentences_part = prompt.split("\n\nSENTENCES:\n")
            evidence = json.loads(evidence_part.removeprefix("EVIDENCE:\n"))
            results = []
            for s in json.loads(sentences_part):
                cid = s["candidate_chunk_ids"][0] if s["candidate_chunk_ids"] else None
                if cid is None:
                    continue
                if self.mode == "liar":
                    quote = "this sentence was invented by the model and exists nowhere"
                else:
                    quote = evidence[cid]["text"].splitlines()[0].lstrip("- ").strip()
                results.append({"id": s["id"], "status": "verified", "chunk_id": cid, "quote": quote,
                                "severity": "info", "explanation": "Supported."})
            return {"results": results}
        # rewriter
        req = json.loads(prompt)
        base = req["sentence"]
        return {"intent": "announce the product", "options": [
            {"text": "LumoraSync syncs files simply and reliably.", "rationale": "Removed hype."},
            {"text": "Meet LumoraSync, built for dependable file sync.", "rationale": "Plain claim."},
            {"text": base, "rationale": "unchanged (should be dropped)"},
        ]}


def make_engine(llm=None) -> Engine:
    e = Engine(get_settings())
    e.llm = llm or FakeLLM()
    return e


class TextUtilTests(unittest.TestCase):
    def test_sentence_offsets_roundtrip(self):
        text = DRAFTS["launch-announcement"]["text"]
        for s in split_sentences(text):
            self.assertEqual(text[s.start:s.end], s.text)

    def test_no_split_inside_numbers_or_abbreviations(self):
        sents = split_sentences("Uptime is 99.9% monthly, e.g. for Business plans. Next one.")
        self.assertEqual(len(sents), 2)

    def test_quote_check(self):
        src = 'Never promise "100%" uptime, "zero downtime".'
        self.assertTrue(quote_in_text("Never promise \u201c100%\u201d uptime", src))
        self.assertFalse(quote_in_text("Always promise 100% uptime", src))
        self.assertFalse(quote_in_text("short", "short text"))  # below min length

    def test_apply_edits(self):
        out, skipped = apply_edits("Hello brave world", [(6, 11, "kind"), (0, 5, "Howdy")])
        self.assertEqual(out, "Howdy kind world")
        self.assertEqual(skipped, [])
        out, skipped = apply_edits("abc def", [(0, 7, "X"), (0, 3, "Y")])  # larger region wins
        self.assertEqual(out, "X")
        self.assertEqual(len(skipped), 1)
        out, _ = apply_edits("Price $5.", [(9, 9, " Taxes extra.")])  # pure insertion
        self.assertEqual(out, "Price $5. Taxes extra.")

    def test_numbers(self):
        self.assertEqual(extract_numbers("Over 8,000 teams at 99.9% for $24."), ["8000", "99.9", "24"])

    def test_json_loose(self):
        self.assertEqual(parse_json_loose('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_loose('Sure! {"a": [1, 2]} done'), {"a": [1, 2]})
        with self.assertRaises(LLMUnavailable):
            parse_json_loose("no json here")


class KnowledgeAndRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = get_settings()
        cls.manager = IndexManager(cls.settings)
        cls.rules = load_rules(DATA_DIR / "rules.json", cls.manager.sample_guideline_chunks)

    def test_chunks_have_sections_and_unique_ids(self):
        chunks = self.manager.sample_guideline_chunks + self.manager.sample_fact_chunks
        ids = [c.chunk_id for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(c.section.endswith("2.1 Banned Buzzwords") for c in chunks))

    def test_every_rule_citation_is_real(self):
        # load_rules raises if any quote is missing from its guideline chunk; assert all resolved.
        self.assertGreaterEqual(len(self.rules), 12)
        for r in self.rules:
            self.assertTrue(r.citation["chunk_id"].startswith(SAMPLE_GUIDELINES_ID))

    def test_launch_draft_triggers_expected_rules(self):
        d = DRAFTS["launch-announcement"]
        hits = lint(d["text"], d["channel"], self.rules, split_sentences(d["text"]))
        ids = {h.rule_id for h in hits}
        for expected in ("R-001", "R-002", "R-003", "R-004", "R-005", "R-006", "R-009", "R-010", "R-011"):
            self.assertIn(expected, ids, f"{expected} missing from {sorted(ids)}")
        spans = [(h.start, h.end) for h in hits if h.rule_type != "required_disclaimer"]
        self.assertEqual(len(spans), len(set(spans)), "duplicate spans should be de-duplicated")

    def test_clean_social_post_has_no_rule_hits(self):
        d = DRAFTS["clean-social"]
        self.assertEqual(lint(d["text"], d["channel"], self.rules, split_sentences(d["text"])), [])

    def test_emoji_rule_depends_on_channel(self):
        text = "Start your free trial today \u2601\ufe0f"
        web = lint(text, "web", self.rules, split_sentences(text))
        social = lint(text, "social", self.rules, split_sentences(text))
        self.assertIn("R-007", {h.rule_id for h in web})
        self.assertNotIn("R-007", {h.rule_id for h in social})

    def test_disclaimer_present_suppresses_rule(self):
        text = "The Starter plan costs $12 per user per month. Prices exclude applicable taxes."
        hits = lint(text, "web", self.rules, split_sentences(text))
        self.assertNotIn("R-011", {h.rule_id for h in hits})

    def test_price_rule_highlights_whole_amount(self):
        text = "The Business plan costs $1,200.50 per year."
        hit = next(h for h in lint(text, "web", self.rules, split_sentences(text)) if h.rule_id == "R-011")
        self.assertEqual(hit.text, "$1,200.50")

    def test_retrieval_finds_relevant_chunk(self):
        ws = self.manager.get("test-workspace-1")
        top = self.manager.query(ws, "What is the uptime SLA?", 2, "facts")
        self.assertTrue(top)
        self.assertIn("Reliability", top[0].chunk.section)

    def test_workspaces_are_isolated(self):
        a, b = self.manager.get("workspace-aaaa"), self.manager.get("workspace-bbbb")
        self.manager.add_document(a, "mine.md", "facts", "# Mine\n## 1. Extra\n- Our founder is Asha Rao.\n")
        self.assertEqual(len(a.docs), len(b.docs) + 1)


class VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = IndexManager(get_settings())
        cls.facts = cls.manager.sample_fact_chunks

    def test_heuristic_catches_retired_and_false_cert(self):
        text = DRAFTS["launch-announcement"]["text"]
        sents = split_sentences(text)
        verdicts = {v.sentence_id: v for v in heuristic_verdicts(sents, self.facts)}
        by_text = {sents[i].text: v for i, v in verdicts.items()}
        price = next(v for t, v in by_text.items() if "$8" in t)
        self.assertEqual(price.status, "violation")
        self.assertIn("Retired", price.chunk.section)
        customers = next(v for t, v in by_text.items() if "8,000" in t)
        self.assertEqual(customers.status, "violation")
        fedramp = next(v for t, v in by_text.items() if "FedRAMP" in t)
        self.assertEqual(fedramp.status, "violation")
        for v in verdicts.values():  # every heuristic citation must be a real quote
            if v.chunk:
                self.assertTrue(quote_in_text(v.quote, v.chunk.citable))

    def test_heuristic_verifies_correct_numbers(self):
        sents = split_sentences("The Business plan costs $24 per user per month with a 99.9% uptime SLA.")
        (v,) = heuristic_verdicts(sents, self.facts)
        self.assertEqual(v.status, "verified")

    def test_heuristic_is_honest_about_scope(self):
        sents = split_sentences("Business plan: 5 TB of storage and a 99.9% uptime SLA.")
        (v,) = heuristic_verdicts(sents, self.facts)
        self.assertEqual(v.status, "verified")
        self.assertIn("5, 99.9", v.explanation)
        self.assertIn("not checked", v.explanation)

    def test_validate_downgrades_fabricated_quotes(self):
        chunk = self.facts[1]
        cand = {0: [chunk]}
        real = chunk.text.splitlines()[0].lstrip("- ")
        good = validate_verdicts({"results": [{"id": 0, "status": "verified", "chunk_id": chunk.chunk_id,
                                               "quote": real, "severity": "info", "explanation": "ok"}]}, {0}, cand)
        self.assertEqual(good[0].status, "verified")
        fake = validate_verdicts({"results": [{"id": 0, "status": "violation", "chunk_id": chunk.chunk_id,
                                               "quote": "totally made up policy text", "severity": "high",
                                               "explanation": "bad"}]}, {0}, cand)
        self.assertEqual(fake[0].status, "unverified_claim")
        self.assertIsNone(fake[0].chunk)
        wrong_chunk = validate_verdicts({"results": [{"id": 0, "status": "verified", "chunk_id": "nope::999",
                                                      "quote": real}]}, {0}, cand)
        self.assertEqual(wrong_chunk[0].status, "unverified_claim")
        skipped = validate_verdicts({"results": [{"id": 0, "status": "not_a_claim"}, {"id": 7, "status": "verified"}]},
                                    {0}, cand)
        self.assertEqual(skipped, [])
        self.assertEqual(validate_verdicts("garbage", {0}, cand), [])


class EndToEndTests(unittest.TestCase):
    def audit(self, draft_id, engine=None, **kw):
        engine = engine or make_engine()
        d = DRAFTS[draft_id]
        req = AuditRequest(draft=d["text"], channel=d["channel"], **kw)
        return engine, run(run_audit(engine, "demo", req))

    def test_launch_audit_produces_cited_findings(self):
        _, audit = self.audit("launch-announcement")
        self.assertGreater(audit.summary.high, 3)
        self.assertTrue(audit.needs_approval)
        for f in audit.findings:
            if f.status != "verified" and f.source == "rule":
                self.assertIsNotNone(f.citation)
            if f.citation:
                self.assertTrue(quote_in_text(f.citation.quote, f.citation.quote))
        self.assertEqual([f.id for f in audit.findings], [f"F{i}" for i in range(1, len(audit.findings) + 1)])

    def test_lying_llm_never_produces_unproven_citation(self):
        engine, audit = self.audit("subtle-claims", make_engine(FakeLLM("liar")))
        ws = engine.manager.get("demo")
        for f in audit.findings:
            if f.source != "rule" and f.citation:
                chunk = ws.chunk_by_id(f.citation.chunk_id)
                self.assertTrue(quote_in_text(f.citation.quote, chunk.citable))
            self.assertNotEqual(f.status, "verified", "fabricated quote must not yield 'verified'")

    def test_llm_down_degrades_gracefully(self):
        _, audit = self.audit("launch-announcement", make_engine(FakeLLM("down")))
        self.assertTrue(audit.llm_status.startswith("degraded"))
        self.assertGreater(audit.summary.violations, 3)  # rules + numeric checks still work

    def test_llm_can_be_switched_off(self):
        engine, audit = self.audit("launch-announcement", use_llm=False)
        self.assertEqual(audit.llm_status, "skipped")
        self.assertEqual(engine.llm.calls, 0)

    def test_clean_post_passes(self):
        _, audit = self.audit("clean-social")
        self.assertFalse(audit.needs_approval)
        self.assertEqual(audit.summary.violations + audit.summary.unverified, 0)

    def test_human_workflow_and_export(self):
        engine, audit = self.audit("launch-announcement")
        # 1) deterministic fix: product name
        name = next(f for f in audit.findings if f.rule_id == "R-009")
        decide(engine, audit, name.id, DecisionRequest(decision="accepted", rewrite_index=0))
        # 2) LLM rewrite of the hype sentence, then accept option 0
        hype = next(f for f in audit.findings if f.rule_id == "R-001")
        resp = run(get_rewrites(engine, audit, hype.id))
        llm_opts = [o for o in resp.options if o.kind == "llm"]
        self.assertEqual(len(llm_opts), 2, "unchanged option must be dropped")
        self.assertEqual(resp.intent, "announce the product")
        idx = resp.options.index(llm_opts[0])
        decide(engine, audit, hype.id, DecisionRequest(decision="accepted", rewrite_index=idx))
        # 3) escalate the competitor mention, reject another with a comment
        comp = next(f for f in audit.findings if f.rule_id == "R-002")
        decide(engine, audit, comp.id, DecisionRequest(decision="escalated", comment="Legal please confirm"))
        text, _ = final_text(audit)
        self.assertIn("LumoraSync syncs files simply and reliably.", text)
        self.assertNotIn("revolutionary", text)
        self.assertTrue(audit.needs_approval)
        md = export_markdown(audit)
        self.assertIn("NEEDS MANAGER APPROVAL", md)
        self.assertIn("Legal please confirm", md)
        self.assertIn("decision_escalated", md)

    def test_accept_without_rewrite_is_rejected(self):
        engine, audit = self.audit("launch-announcement", use_llm=False)
        claim = next(f for f in audit.findings if f.granularity == "sentence" and f.status != "verified")
        with self.assertRaises(ValueError):
            decide(engine, audit, claim.id, DecisionRequest(decision="accepted"))

    def test_disclaimer_insertion(self):
        engine, audit = self.audit("subtle-claims")
        f = next(f for f in audit.findings if f.rule_id == "R-011")
        decide(engine, audit, f.id, DecisionRequest(decision="accepted", rewrite_index=0))
        text, _ = final_text(audit)
        self.assertIn("Prices exclude applicable taxes.", text)


if __name__ == "__main__":
    unittest.main()
