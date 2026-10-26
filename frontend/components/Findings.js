"use client";
import { useState } from "react";

const SEV_LABEL = { high: "High risk", medium: "Medium", low: "Minor", info: "Verified" };
const DECISION_LABEL = {
  accepted: "Fixed",
  edited: "Edited",
  rejected: "Kept as is",
  escalated: "Sent for approval",
};

function snippet(text, max = 46) {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
}

function optionLabel(o) {
  if (o.kind === "deterministic" && o.start === o.end) return `Add: ${o.text.trim()}`;
  if (o.text === "") return "Remove it";
  return o.text;
}

function Option({ option, busy, onUse, onEdit }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(option.text);
  return (
    <li className="opt">
      <div className="opt-tag">{option.kind === "deterministic" ? "Guideline fix" : "Suggested rewrite"}</div>
      {editing ? (
        <>
          <textarea
            className="opt-edit"
            value={value}
            rows={3}
            onChange={(e) => setValue(e.target.value)}
            aria-label="Edit the replacement text"
          />
          <div className="opt-actions">
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onEdit(value)}>
              Apply my edit
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="opt-text">{optionLabel(option)}</p>
          {option.rationale && <p className="opt-why">{option.rationale}</p>}
          <div className="opt-actions">
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={onUse}>
              Use this
            </button>
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => setEditing(true)}>
              Edit first
            </button>
          </div>
        </>
      )}
    </li>
  );
}

function Detail({ finding: f, busy, note, onRewrite, onDecide }) {
  const [comment, setComment] = useState("");
  const hasLlm = f.rewrites.some((o) => o.kind === "llm");
  const actionable = f.status !== "verified";
  return (
    <div className="detail">
      <p className="detail-why">{f.explanation}</p>

      {f.citation ? (
        <figure className="source">
          <blockquote>{f.citation.quote}</blockquote>
          <figcaption>
            {f.citation.doc_name}, section {f.citation.section.split(" > ").slice(-1)[0]}
          </figcaption>
        </figure>
      ) : (
        actionable && <p className="detail-nosrc">No approved source covers this claim. Add proof, or remove the claim.</p>
      )}

      {actionable && (
        <>
          {f.rewrites.length > 0 && (
            <ul className="opts">
              {f.rewrites.map((o, i) => (
                <Option
                  key={i}
                  option={o}
                  busy={busy}
                  onUse={() => onDecide(f.id, { decision: "accepted", rewrite_index: i })}
                  onEdit={(text) => onDecide(f.id, { decision: "edited", rewrite_index: i, final_text: text })}
                />
              ))}
            </ul>
          )}
          {!hasLlm && (
            <button className="btn btn-outline btn-sm" disabled={busy} onClick={() => onRewrite(f.id)}>
              {busy ? "Writing options…" : f.rewrites.length ? "Suggest more rewrites" : "Suggest rewrites"}
            </button>
          )}
          {note && <p className="detail-note">{note}</p>}

          <div className="decide">
            <input
              className="comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Add a note for the approver (optional)"
              aria-label="Note for the approver"
            />
            <div className="decide-row">
              <button
                className="btn btn-ghost btn-sm"
                disabled={busy}
                onClick={() => onDecide(f.id, { decision: "rejected", comment: comment || undefined })}
              >
                Keep original
              </button>
              <button
                className="btn btn-escalate btn-sm"
                disabled={busy}
                onClick={() => onDecide(f.id, { decision: "escalated", comment: comment || undefined })}
              >
                Send to approver
              </button>
            </div>
          </div>
        </>
      )}
      {f.decision !== "pending" && (
        <p className="detail-decision">
          {DECISION_LABEL[f.decision]}
          {f.comment ? `: ${f.comment}` : ""}
        </p>
      )}
    </div>
  );
}

export default function Findings({ audit, selectedId, onSelect, busyId, notes, onRewrite, onDecide }) {
  const [filter, setFilter] = useState("attention");
  const all = audit.findings;
  const attention = all.filter((f) => f.status !== "verified");
  const verified = all.filter((f) => f.status === "verified");
  const shown = filter === "attention" ? attention : filter === "verified" ? verified : all;

  return (
    <section className="findings" aria-label="Findings">
      <div className="chips" role="tablist">
        {[
          ["attention", `Needs attention ${attention.length}`],
          ["verified", `Verified ${verified.length}`],
          ["all", "Everything"],
        ].map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={filter === key}
            className={`chip ${filter === key ? "chip-on" : ""}`}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <p className="empty">
          {filter === "attention" ? "Nothing needs attention. This copy matches the guidelines and the fact sheet." : "Nothing here yet."}
        </p>
      ) : (
        <ul className="rows">
          {shown.map((f) => {
            const open = f.id === selectedId;
            const tone = f.status === "verified" ? "info" : f.severity;
            const settled = f.decision !== "pending";
            return (
              <li key={f.id} className={`row ${open ? "row-open" : ""} ${settled ? "row-settled" : ""}`}>
                <button className="row-head" aria-expanded={open} onClick={() => onSelect(open ? null : f.id)}>
                  <span className={`dot dot-${tone}`} aria-hidden="true" />
                  <span className="row-main">
                    <span className="row-title">{f.title}</span>
                    <span className="row-text">“{snippet(f.text)}”</span>
                  </span>
                  <span className={`badge ${settled ? "badge-" + f.decision : "badge-" + tone}`}>
                    {settled ? DECISION_LABEL[f.decision] : SEV_LABEL[tone]}
                  </span>
                </button>
                {open && (
                  <Detail
                    finding={f}
                    busy={busyId === f.id}
                    note={notes[f.id]}
                    onRewrite={onRewrite}
                    onDecide={onDecide}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
