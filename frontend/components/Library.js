"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

export default function Library({ open, onClose }) {
  const [docs, setDocs] = useState([]);
  const [docType, setDocType] = useState("facts");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef(null);

  const load = () => api.documents().then(setDocs).catch((e) => setError(e.message));
  useEffect(() => {
    if (open) load();
  }, [open]);

  async function upload(e) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return setError("Choose a .md, .txt or .pdf file first.");
    setBusy(true);
    setError("");
    try {
      await api.ingest(file, docType);
      fileRef.current.value = "";
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id) {
    try {
      await api.deleteDoc(id);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  if (!open) return null;
  return (
    <div className="scrim" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-label="Knowledge base" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <h2>Knowledge base</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            Close
          </button>
        </header>
        <p className="drawer-lede">
          Every finding is checked against these documents. The Lumora Cloud samples are always loaded; add your own to
          test other material. Uploads stay in your browser session only.
        </p>
        <ul className="docs">
          {docs.map((d) => (
            <li key={d.doc_id} className="doc">
              <div>
                <p className="doc-name">{d.name}</p>
                <p className="doc-meta">
                  {d.doc_type === "guideline" ? "Brand guidelines" : "Fact sheet"}, {d.chunk_count} passages
                  {d.is_sample ? " (built-in sample)" : ""}
                </p>
              </div>
              {!d.is_sample && (
                <button className="btn btn-ghost btn-sm" onClick={() => remove(d.doc_id)}>
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
        <form className="upload" onSubmit={upload}>
          <h3>Add a document</h3>
          <div className="seg-toggle" role="radiogroup" aria-label="Document type">
            {[
              ["facts", "Fact sheet"],
              ["guideline", "Brand guidelines"],
            ].map(([v, label]) => (
              <button
                type="button"
                key={v}
                role="radio"
                aria-checked={docType === v}
                className={`chip ${docType === v ? "chip-on" : ""}`}
                onClick={() => setDocType(v)}
              >
                {label}
              </button>
            ))}
          </div>
          <input ref={fileRef} type="file" accept=".md,.markdown,.txt,.pdf" className="file" />
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Reading document…" : "Add to knowledge base"}
          </button>
          {error && <p className="error" role="alert">{error}</p>}
          <p className="hint">
            Uploaded guidelines improve source matching. The built-in rule checks always use the sample guidelines.
          </p>
        </form>
      </aside>
    </div>
  );
}
