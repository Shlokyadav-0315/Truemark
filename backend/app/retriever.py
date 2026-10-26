"""Per-workspace knowledge index.

Primary backend: ChromaDB (ephemeral, in-memory) using its default local embedding model.
Fallback backend: a small TF-IDF cosine index in pure python. If Chroma is missing, cannot
download its embedding model, or errors at query time, the workspace silently degrades to
TF-IDF so the product never shows a blank page. The active backend is reported to the UI.

Each workspace (one per browser session) gets its own isolated index, so judges who upload
documents do not see each other's data."""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from .chunking import Chunk, chunk_markdown
from .config import DATA_DIR, SAMPLE_FACTS_ID, SAMPLE_GUIDELINES_ID, Settings

log = logging.getLogger("truemark.retriever")

_STOP = set(
    "a an and are as at be by for from has have in into is it its of on or our that the their this to was we "
    "were will with you your".split()
)


def _tokens(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower()):
        if t in _STOP:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


@dataclass
class Scored:
    chunk: Chunk
    score: float


class TfidfIndex:
    name = "tfidf"

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._vecs: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}

    def rebuild(self, chunks: list[Chunk]) -> None:
        self._chunks = list(chunks)
        docs = [_tokens(c.searchable) for c in chunks]
        df: dict[str, int] = {}
        for toks in docs:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n = max(len(docs), 1)
        self._idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
        self._vecs = [self._vector(toks) for toks in docs]

    def _vector(self, toks: list[str]) -> dict[str, float]:
        tf: dict[str, float] = {}
        for t in toks:
            tf[t] = tf.get(t, 0.0) + 1.0
        vec = {t: (1 + math.log(c)) * self._idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def query(self, text: str, k: int, doc_type: str | None = None) -> list[Scored]:
        q = self._vector(_tokens(text))
        scored = []
        for chunk, vec in zip(self._chunks, self._vecs):
            if doc_type and chunk.doc_type != doc_type:
                continue
            s = sum(w * vec.get(t, 0.0) for t, w in q.items())
            if s > 0:
                scored.append(Scored(chunk, s))
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:k]


class ChromaIndex:
    name = "chroma"

    def __init__(self, client, workspace_id: str) -> None:
        self._client = client
        self._col_name = "ws" + hashlib.sha1(workspace_id.encode()).hexdigest()[:20]
        self._col = None
        self._by_id: dict[str, Chunk] = {}

    def rebuild(self, chunks: list[Chunk]) -> None:
        try:
            self._client.delete_collection(self._col_name)
        except Exception:
            pass
        self._col = self._client.get_or_create_collection(
            name=self._col_name, metadata={"hnsw:space": "cosine"}
        )
        self._by_id = {c.chunk_id: c for c in chunks}
        if chunks:
            self._col.add(
                ids=[c.chunk_id for c in chunks],
                documents=[c.searchable for c in chunks],
                metadatas=[{"doc_id": c.doc_id, "doc_type": c.doc_type} for c in chunks],
            )

    def query(self, text: str, k: int, doc_type: str | None = None) -> list[Scored]:
        if self._col is None or not self._by_id:
            return []
        kwargs = {"query_texts": [text], "n_results": max(1, min(k, len(self._by_id)))}
        if doc_type:
            kwargs["where"] = {"doc_type": doc_type}
        res = self._col.query(**kwargs)
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        out = []
        for cid, dist in zip(ids, dists):
            chunk = self._by_id.get(cid)
            if chunk:
                out.append(Scored(chunk, 1.0 - float(dist)))
        return out


@dataclass
class DocInfo:
    doc_id: str
    name: str
    doc_type: str
    chunk_count: int
    is_sample: bool


@dataclass
class Workspace:
    workspace_id: str
    docs: dict[str, tuple[DocInfo, list[Chunk]]] = field(default_factory=dict)
    index: object | None = None
    backend: str = "tfidf"
    lock: threading.Lock = field(default_factory=threading.Lock)

    def all_chunks(self) -> list[Chunk]:
        return [c for _, chunks in self.docs.values() for c in chunks]

    def chunk_by_id(self, chunk_id: str) -> Chunk | None:
        for c in self.all_chunks():
            if c.chunk_id == chunk_id:
                return c
        return None

    def doc_infos(self) -> list[DocInfo]:
        return [info for info, _ in self.docs.values()]


class IndexManager:
    """Owns all workspaces (bounded LRU) and the shared sample knowledge base."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._workspaces: "OrderedDict[str, Workspace]" = OrderedDict()
        self._lock = threading.Lock()
        self._chroma_client = None
        self._chroma_failed = settings.retriever != "chroma"

        guide_text = (DATA_DIR / "brand_guidelines.md").read_text(encoding="utf-8")
        facts_text = (DATA_DIR / "fact_sheet.md").read_text(encoding="utf-8")
        self.sample_guideline_chunks = chunk_markdown(
            guide_text, SAMPLE_GUIDELINES_ID, "Lumora Cloud Brand Guidelines", "guideline"
        )
        self.sample_fact_chunks = chunk_markdown(
            facts_text, SAMPLE_FACTS_ID, "Lumora Cloud Fact Sheet", "facts"
        )

    # ----- chroma bootstrap -----
    def _get_chroma(self):
        if self._chroma_failed:
            return None
        if self._chroma_client is None:
            try:
                import chromadb

                self._chroma_client = chromadb.EphemeralClient()
            except Exception as exc:
                log.warning("ChromaDB unavailable (%s). Using TF-IDF fallback.", exc)
                self._chroma_failed = True
                return None
        return self._chroma_client

    def _build_index(self, ws: Workspace) -> None:
        chunks = ws.all_chunks()
        client = self._get_chroma()
        if client is not None:
            try:
                idx = ChromaIndex(client, ws.workspace_id)
                idx.rebuild(chunks)
                ws.index, ws.backend = idx, "chroma"
                return
            except Exception as exc:
                log.warning("Chroma index build failed (%s). Falling back to TF-IDF.", exc)
                self._chroma_failed = True
        idx2 = TfidfIndex()
        idx2.rebuild(chunks)
        ws.index, ws.backend = idx2, "tfidf"

    # ----- workspace lifecycle -----
    def get(self, workspace_id: str) -> Workspace:
        with self._lock:
            ws = self._workspaces.get(workspace_id)
            if ws is not None:
                self._workspaces.move_to_end(workspace_id)
                return ws
            ws = Workspace(workspace_id=workspace_id)
            for chunks, name, dtype, did in (
                (self.sample_guideline_chunks, "Lumora Cloud Brand Guidelines", "guideline", SAMPLE_GUIDELINES_ID),
                (self.sample_fact_chunks, "Lumora Cloud Fact Sheet", "facts", SAMPLE_FACTS_ID),
            ):
                ws.docs[did] = (DocInfo(did, name, dtype, len(chunks), True), list(chunks))
            self._build_index(ws)
            self._workspaces[workspace_id] = ws
            while len(self._workspaces) > self.settings.max_workspaces:
                old_id, _ = self._workspaces.popitem(last=False)
                if old_id != "demo":
                    self._drop_collection(old_id)
            return ws

    def _drop_collection(self, workspace_id: str) -> None:
        if self._chroma_client is not None:
            try:
                name = "ws" + hashlib.sha1(workspace_id.encode()).hexdigest()[:20]
                self._chroma_client.delete_collection(name)
            except Exception:
                pass

    def add_document(self, ws: Workspace, name: str, doc_type: str, text: str) -> DocInfo:
        with ws.lock:
            if len(ws.docs) >= self.settings.max_docs_per_workspace:
                raise ValueError(f"Document limit reached ({self.settings.max_docs_per_workspace}). Delete one first.")
            doc_id = "doc-" + hashlib.sha1(f"{name}{len(text)}{len(ws.docs)}".encode()).hexdigest()[:8]
            chunks = chunk_markdown(text, doc_id, name, doc_type)
            if not chunks:
                raise ValueError("No usable text found in this document.")
            info = DocInfo(doc_id, name, doc_type, len(chunks), False)
            ws.docs[doc_id] = (info, chunks)
            self._build_index(ws)
            return info

    def remove_document(self, ws: Workspace, doc_id: str) -> None:
        with ws.lock:
            entry = ws.docs.get(doc_id)
            if entry is None:
                raise KeyError(doc_id)
            if entry[0].is_sample:
                raise ValueError("The built-in sample documents cannot be deleted.")
            del ws.docs[doc_id]
            self._build_index(ws)

    def query(self, ws: Workspace, text: str, k: int, doc_type: str | None = None) -> list[Scored]:
        try:
            return ws.index.query(text, k, doc_type)  # type: ignore[union-attr]
        except Exception as exc:
            log.warning("Retrieval error on %s (%s). Rebuilding with TF-IDF.", ws.backend, exc)
            self._chroma_failed = True
            with ws.lock:
                idx = TfidfIndex()
                idx.rebuild(ws.all_chunks())
                ws.index, ws.backend = idx, "tfidf"
            return ws.index.query(text, k, doc_type)
