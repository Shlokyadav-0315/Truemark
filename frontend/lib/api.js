// Thin client for the TrueMark API. Every request carries a per-browser workspace id so
// uploads and audits stay private to this visitor.
const BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export function workspaceId() {
  if (typeof window === "undefined") return "demo";
  try {
    let id = window.localStorage.getItem("truemark-ws");
    if (!id) {
      id = "w" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
      window.localStorage.setItem("truemark-ws", id);
    }
    return id;
  } catch {
    return "demo";
  }
}

async function request(path, { method = "GET", body, form, raw = false } = {}) {
  const headers = { "X-Workspace": workspaceId() };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(BASE + path, { method, headers, body: payload });
  } catch {
    throw new ApiError("Can't reach the TrueMark server. Check that it is running and NEXT_PUBLIC_API_URL is correct.", 0);
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status}).`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (j.detail) msg = "The server could not accept that request.";
    } catch {}
    throw new ApiError(msg, res.status);
  }
  if (raw) return res;
  return res.status === 204 ? null : res.json();
}

export const api = {
  health: () => request("/health"),
  samples: () => request("/samples"),
  documents: () => request("/documents"),
  ingest: (file, docType) => {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", docType);
    return request("/ingest", { method: "POST", form });
  },
  deleteDoc: (id) => request(`/documents/${id}`, { method: "DELETE" }),
  audit: (draft, channel, useLlm) => request("/audit", { method: "POST", body: { draft, channel, use_llm: useLlm } }),
  rewrite: (auditId, fid) => request(`/audit/${auditId}/findings/${fid}/rewrite`, { method: "POST" }),
  decide: (auditId, fid, payload) => request(`/audit/${auditId}/findings/${fid}/decision`, { method: "POST", body: payload }),
  final: (auditId) => request(`/audit/${auditId}/final`),
  async download(auditId, format) {
    const res = await request(`/audit/${auditId}/export?format=${format}`, { raw: true });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `truemark-audit-${auditId}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
