"""Turn guideline / fact-sheet documents into citable chunks.

Each chunk keeps its section path so a finding can say "Guidelines, section 2.1"
and carries a stable chunk_id the verifier must cite."""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHUNK_CHARS = 900


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    doc_name: str
    doc_type: str  # "guideline" | "facts"
    section: str
    text: str

    @property
    def searchable(self) -> str:
        return f"{self.section}\n{self.text}"

    @property
    def citable(self) -> str:
        """Everything a quote is allowed to come from (heading + body)."""
        return f"{self.section}\n{self.text}"


_HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")
_NUMBERED_LINE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+([A-Z][^\n]{2,80})$")


def plain_to_markdown(text: str) -> str:
    """PDFs and .txt lose '#' headings. Treat short numbered lines such as
    '2.1 Banned Buzzwords' as headings so sections survive."""
    out = []
    for line in text.splitlines():
        m = _NUMBERED_LINE.match(line.strip())
        if m and not line.strip().endswith((".", ",", ";", ":")):
            depth = min(m.group(1).count(".") + 2, 4)
            out.append("#" * depth + " " + line.strip())
        else:
            out.append(line)
    return "\n".join(out)


def _split_long(body: str) -> list[str]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]
    parts, cur = [], ""
    for line in body.splitlines():
        if cur and len(cur) + len(line) + 1 > MAX_CHUNK_CHARS:
            parts.append(cur.strip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts


def chunk_markdown(text: str, doc_id: str, doc_name: str, doc_type: str) -> list[Chunk]:
    sections: list[tuple[str, list[str]]] = []
    stack: list[str] = []
    title_seen = False
    current_lines: list[str] = []
    current_path = doc_name

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_path, [body]))

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1 and not title_seen:
                title_seen = True
                flush()
                current_lines = []
                current_path = title
                stack = []
                continue
            flush()
            current_lines = []
            stack = stack[: max(level - 2, 0)]
            stack.append(title)
            current_path = " > ".join(stack)
        else:
            current_lines.append(line)
    flush()

    chunks: list[Chunk] = []
    for path, bodies in sections:
        for piece in _split_long(bodies[0]):
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}::{len(chunks):03d}",
                    doc_id=doc_id,
                    doc_name=doc_name,
                    doc_type=doc_type,
                    section=path,
                    text=piece,
                )
            )
    return chunks


def extract_text(filename: str, data: bytes) -> str:
    """Return plain text for .md/.txt/.pdf uploads. Raises ValueError on unsupported/empty input."""
    name = filename.lower()
    if name.endswith((".md", ".markdown", ".txt")):
        text = data.decode("utf-8", errors="replace")
        return text if name.endswith((".md", ".markdown")) else plain_to_markdown(text)
    if name.endswith(".pdf"):
        try:
            from io import BytesIO

            from pypdf import PdfReader  # lazy import: only needed for PDF uploads
        except ImportError as exc:  # pragma: no cover
            raise ValueError("PDF support is not installed on this server (pypdf missing).") from exc
        try:
            reader = PdfReader(BytesIO(data))
            pages = [(p.extract_text() or "") for p in reader.pages]
        except Exception as exc:  # malformed PDFs are a Round 3 hardening item
            raise ValueError(f"Could not read this PDF: {exc}") from exc
        text = "\n\n".join(pages).strip()
        if len(text) < 20:
            raise ValueError("No extractable text found. Scanned PDFs are not supported yet.")
        return plain_to_markdown(text)
    raise ValueError("Unsupported file type. Upload .md, .txt, or .pdf.")
