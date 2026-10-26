"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import Proof from "../components/Proof";
import Findings from "../components/Findings";
import Library from "../components/Library";
import Score from "../components/Score";

const CHANNELS = [
  ["web", "Website"],
  ["social", "Social post"],
  ["email", "Customer email"],
  ["press", "Press release"],
];

function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
      <rect x="1" y="1" width="24" height="24" rx="8" fill="#A78BFF" />
      <path d="M7 13.5l4 4 8-9" fill="none" stroke="#150E2B" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function Home() {
  const [health, setHealth] = useState(null); // null = connecting
  const [samples, setSamples] = useState([]);
  const [draft, setDraft] = useState("");
  const [channel, setChannel] = useState("web");
  const [useAi, setUseAi] = useState(true);
  const [mode, setMode] = useState("edit");
  const [audit, setAudit] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [finalText, setFinalText] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [notes, setNotes] = useState({});
  const [error, setError] = useState("");
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  // Wake the server (free hosts sleep) and keep trying for about 90 seconds.
  useEffect(() => {
    let stop = false;
    let tries = 0;
    const poll = async () => {
      try {
        const h = await api.health();
        if (!stop) setHealth(h);
      } catch {
        tries += 1;
        if (stop) return;
        if (tries < 30) setTimeout(poll, 3000);
        else setHealth({ status: "down" });
      }
    };
    poll();
    return () => {
      stop = true;
    };
  }, []);

  useEffect(() => {
    if (health?.status !== "ok") return;
    api
      .samples()
      .then((s) => {
        setSamples(s);
        if (s[0]) {
          setDraft((d) => {
            if (!d) setChannel(s[0].channel);
            return d || s[0].text;
          });
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [health?.status]);

  const loadSample = (s) => {
    setDraft(s.text);
    setChannel(s.channel);
    setMode("edit");
    setAudit(null);
    setSelectedId(null);
    setError("");
  };

  const refreshFinal = useCallback(async (id) => {
    try {
      const r = await api.final(id);
      setFinalText(r.final_text);
    } catch {}
  }, []);

  async function runAudit() {
    if (!draft.trim()) return setError("Paste some copy first, or load a sample.");
    setBusy(true);
    setError("");
    try {
      const a = await api.audit(draft, channel, useAi);
      setAudit(a);
      setNotes({});
      setMode("review");
      const first = a.findings.find((f) => f.status !== "verified");
      setSelectedId(first ? first.id : null);
      await refreshFinal(a.audit_id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function onRewrite(fid) {
    setBusyId(fid);
    try {
      const r = await api.rewrite(audit.audit_id, fid);
      setAudit((a) => ({ ...a, findings: a.findings.map((f) => (f.id === fid ? { ...f, rewrites: r.options } : f)) }));
      setNotes((n) => ({
        ...n,
        [fid]: r.llm_status.startsWith("degraded")
          ? "AI rewrites are unavailable right now. You can still edit the text yourself."
          : r.intent
          ? `Kept your intent: ${r.intent}.`
          : "",
      }));
    } catch (e) {
      setNotes((n) => ({ ...n, [fid]: e.message }));
    } finally {
      setBusyId(null);
    }
  }

  async function onDecide(fid, payload) {
    setBusyId(fid);
    try {
      const a = await api.decide(audit.audit_id, fid, payload);
      setAudit(a);
      await refreshFinal(a.audit_id);
      // move on to the next open issue
      const next = a.findings.find((f) => f.status !== "verified" && f.decision === "pending");
      setSelectedId(next ? next.id : null);
    } catch (e) {
      setNotes((n) => ({ ...n, [fid]: e.message }));
    } finally {
      setBusyId(null);
    }
  }

  async function copyFinal() {
    try {
      await navigator.clipboard.writeText(finalText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {}
  }

  const open = audit ? audit.findings.filter((f) => f.status !== "verified" && f.decision === "pending").length : 0;
  const connecting = health === null;
  const down = health?.status === "down";
  const degraded = audit && audit.llm_status.startsWith("degraded");

  return (
    <div className="app">
      <header className="bar">
        <div className="brand">
          <Mark />
          <span className="brand-name">TrueMark</span>
        </div>
        <div className="bar-tools">
          <label className="field">
            <span className="sr">Channel</span>
            <select value={channel} onChange={(e) => setChannel(e.target.value)} aria-label="Where will this be published?">
              {CHANNELS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label className="switch">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
            <span>AI fact checks</span>
          </label>
          <button className="btn btn-ghost" onClick={() => setLibraryOpen(true)}>
            Knowledge base
          </button>
          <span className={`pill ${connecting ? "pill-wait" : down ? "pill-bad" : health?.llm?.live ? "pill-ok" : "pill-warn"}`}>
            {connecting ? "Connecting" : down ? "Server offline" : health?.llm?.live ? "AI ready" : "AI key missing"}
          </span>
        </div>
      </header>

      {connecting && (
        <p className="banner banner-wait" role="status">
          Waking the server. The first visit can take up to a minute; you can start typing meanwhile.
        </p>
      )}
      {down && (
        <p className="banner banner-bad" role="alert">
          The server isn't responding. Refresh in a minute, or check that the API URL is set correctly.
        </p>
      )}

      <main className="main">
        <section className="stage" aria-label="Draft">
          <div className="stage-head">
            <h1 className="stage-title">{mode === "edit" ? "Paste your copy" : "Review"}</h1>
            <div className="stage-actions">
              {mode === "review" ? (
                <button className="btn btn-ghost" onClick={() => setMode("edit")}>
                  Edit draft
                </button>
              ) : null}
              <button className="btn btn-primary btn-lg" onClick={runAudit} disabled={busy}>
                {busy ? "Checking…" : mode === "review" ? "Check again" : "Check this copy"}
              </button>
            </div>
          </div>

          {mode === "review" && audit && <Score findings={audit.findings} degraded={degraded} />}

          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {degraded && mode === "review" && (
            <p className="banner banner-warn" role="status">
              AI checks weren't available for this run, so only the rules and exact figures were checked. Claims
              worded without numbers may have been missed.
            </p>
          )}

          <div className="slip">
            {mode === "review" && audit && (
              <div
                key={`${audit.audit_id}-${audit.needs_approval}`}
                className={`stamp ${audit.needs_approval ? "stamp-warn" : "stamp-ok"}`}
                aria-live="polite"
              >
                <span className="stamp-main">{audit.needs_approval ? "Needs approval" : "Clear to publish"}</span>
                <span className="stamp-sub">
                  {open === 0 ? "no open issues" : `${open} open ${open === 1 ? "issue" : "issues"}`}
                </span>
              </div>
            )}
            {mode === "edit" ? (
              <textarea
                className="draft"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Paste a headline, social post, email or press release…"
                spellCheck
                aria-label="Draft copy"
              />
            ) : (
              <Proof draft={audit.draft} findings={audit.findings} selectedId={selectedId} onSelect={setSelectedId} />
            )}
          </div>

          {mode === "edit" && (
            <div className="samples">
              <p className="samples-lede">No copy handy? Try one of these.</p>
              <div className="samples-row">
                {samples.map((s) => (
                  <button key={s.id} className="sample" onClick={() => loadSample(s)}>
                    {s.title}
                  </button>
                ))}
              </div>
            </div>
          )}

          {mode === "review" && audit && (
            <section className="final" aria-label="Final text">
              <div className="final-head">
                <h2>Final text</h2>
                <div className="final-actions">
                  <button className="btn btn-ghost btn-sm" onClick={copyFinal}>
                    {copied ? "Copied" : "Copy"}
                  </button>
                  <button className="btn btn-outline btn-sm" onClick={() => api.download(audit.audit_id, "pdf").catch((e) => setError(e.message))}>
                    Download PDF
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => api.download(audit.audit_id, "md").catch((e) => setError(e.message))}>
                    Markdown
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => api.download(audit.audit_id, "json").catch((e) => setError(e.message))}>
                    JSON
                  </button>
                </div>
              </div>
              <p className="final-text">{finalText}</p>
              {audit.needs_approval && (
                <p className="final-note">
                  High-risk issues are still open or were sent to an approver. The report lists every decision and who
                  needs to sign off.
                </p>
              )}
            </section>
          )}
        </section>

        <aside className="side" aria-label="Findings">
          {mode === "review" && audit ? (
            <Findings
              audit={audit}
              selectedId={selectedId}
              onSelect={setSelectedId}
              busyId={busyId}
              notes={notes}
              onRewrite={onRewrite}
              onDecide={onDecide}
            />
          ) : (
            <div className="intro">
              <h2>What happens next</h2>
              <p>
                TrueMark reads your copy against the brand guidelines and the approved fact sheet. Every problem it
                finds comes with the exact passage it relied on.
              </p>
              <p>You decide what changes: use a suggested fix, edit it, keep your original, or send it to an approver.</p>
              <p className="intro-meta">
                Your copy is checked against {health?.rules ?? 12} brand rules and a fact sheet of approved figures.
              </p>
            </div>
          )}
        </aside>
      </main>

      <Library open={libraryOpen} onClose={() => setLibraryOpen(false)} />
    </div>
  );
}
