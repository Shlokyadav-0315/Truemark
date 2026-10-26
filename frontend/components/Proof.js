"use client";
import { useEffect, useMemo, useRef } from "react";

const SEV_RANK = { high: 0, medium: 1, low: 2, info: 3 };
const STATUS_RANK = { violation: 0, unverified_claim: 1, verified: 2 };

function buildSegments(text, findings) {
  const points = new Set([0, text.length]);
  for (const f of findings) {
    points.add(Math.max(0, Math.min(f.start, text.length)));
    points.add(Math.max(0, Math.min(f.end, text.length)));
  }
  const sorted = [...points].sort((a, b) => a - b);
  const segments = [];
  for (let i = 0; i < sorted.length - 1; i++) {
    const s = sorted[i];
    const e = sorted[i + 1];
    if (e <= s) continue;
    const cover = findings.filter((f) => f.end > f.start && f.start <= s && f.end >= e);
    segments.push({ s, e, text: text.slice(s, e), cover });
  }
  return segments;
}

function describe(cover) {
  const spans = cover.filter((f) => f.granularity === "span" && f.status !== "verified");
  const sentences = cover.filter((f) => f.granularity === "sentence");
  const mark = spans.length
    ? spans.reduce((a, b) => (SEV_RANK[b.severity] < SEV_RANK[a.severity] ? b : a))
    : null;
  const sentence = sentences.length
    ? sentences.reduce((a, b) => (STATUS_RANK[b.status] < STATUS_RANK[a.status] ? b : a))
    : null;
  const settled = (f) => f.decision === "accepted" || f.decision === "edited";
  const target = spans.length
    ? spans.reduce((a, b) => (b.end - b.start < a.end - a.start ? b : a))
    : sentences.reduce((a, b) => (b.end - b.start < a.end - a.start ? b : a), sentences[0]);
  return {
    markSev: mark ? mark.severity : null,
    markDone: mark ? spans.every(settled) : false,
    markKept: mark ? spans.every((f) => f.decision === "rejected") : false,
    sentenceStatus: sentence ? sentence.status : null,
    sentenceDone: sentence ? settled(sentence) : false,
    target,
  };
}

export default function Proof({ draft, findings, selectedId, onSelect }) {
  const segments = useMemo(() => buildSegments(draft, findings), [draft, findings]);
  const selectedRef = useRef(null);

  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId]);

  return (
    <div className="proof-text" role="document" aria-label="Draft with findings marked">
      {segments.map((seg) => {
        if (!seg.cover.length) return <span key={seg.s}>{seg.text}</span>;
        const d = describe(seg.cover);
        const selected = seg.cover.some((f) => f.id === selectedId);
        const cls = [
          "seg",
          d.markSev && `mk mk-${d.markSev}`,
          d.markDone && "is-done",
          d.markKept && "is-kept",
          d.sentenceStatus && `ss ss-${d.sentenceStatus}`,
          d.sentenceDone && "ss-done",
          selected && "is-selected",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <span
            key={seg.s}
            ref={selected && seg.cover.find((f) => f.id === selectedId) ? selectedRef : null}
            className={cls}
            role="button"
            tabIndex={0}
            onClick={() => d.target && onSelect(d.target.id)}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && d.target) {
                e.preventDefault();
                onSelect(d.target.id);
              }
            }}
          >
            {seg.text}
          </span>
        );
      })}
    </div>
  );
}
