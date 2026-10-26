"""Pure-python helpers (no third-party imports) so they are easy to unit test."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ABBREVIATIONS = {
    "e.g", "i.e", "vs", "etc", "inc", "ltd", "mr", "mrs", "ms", "dr", "approx", "no", "co", "corp", "st",
}

# A sentence ends at . ! ? (plus closing quotes/brackets) followed by whitespace or end of text,
# or at a line break. A '.' inside "99.9%" is not followed by whitespace, so it never splits.
_BOUNDARY = re.compile(r"[.!?]+[\"'\u201d\u2019)\]]*(?=\s|$)|\n+")


@dataclass(frozen=True)
class Sentence:
    id: int
    start: int
    end: int
    text: str


def split_sentences(text: str) -> list[Sentence]:
    """Split text into sentences, keeping exact character offsets into `text`."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for m in _BOUNDARY.finditer(text):
        end = m.end()
        chunk = text[cursor:end]
        # Do not split after common abbreviations such as "e.g." or "Inc."
        if m.group().startswith(".") and not m.group().startswith("..."):
            last_word = re.findall(r"([A-Za-z.]+)\.$", chunk.strip())
            if last_word and last_word[-1].lower().strip(".") in _ABBREVIATIONS:
                continue
        spans.append((cursor, end))
        cursor = end
    if cursor < len(text):
        spans.append((cursor, len(text)))

    sentences: list[Sentence] = []
    for start, end in spans:
        raw = text[start:end]
        stripped = raw.strip()
        if not stripped:
            continue
        lead = len(raw) - len(raw.lstrip())
        s = start + lead
        e = s + len(stripped)
        sentences.append(Sentence(id=len(sentences), start=s, end=e, text=text[s:e]))
    return sentences


def sentence_at(sentences: list[Sentence], pos: int) -> Sentence | None:
    for s in sentences:
        if s.start <= pos < s.end or pos == s.end:
            return s
    return sentences[-1] if sentences else None


# ---------- quote verification (guards against hallucinated citations) ----------

_TRANSLATE = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u2011": "-",
    }
)


def normalize(s: str) -> str:
    s = s.translate(_TRANSLATE).lower()
    s = re.sub(r"[*_`#>]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def quote_in_text(quote: str | None, source: str, min_len: int = 8) -> bool:
    """True only if `quote` appears verbatim (modulo whitespace/case/markdown) in `source`."""
    if not quote:
        return False
    q = normalize(quote).strip(" .\u2026")
    if len(q) < min_len:
        return False
    return q in normalize(source)


# ---------- claim detection & numbers ----------

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def extract_numbers(text: str) -> list[str]:
    out: list[str] = []
    for m in _NUM.finditer(text):
        n = m.group().replace(",", "").rstrip(".")
        out.append(n)
    return out


_CLAIM_HINT = re.compile(
    r"\d|%|\$|\u20b9|\u20ac|\u00a3"
    r"|\b(fast|faster|fastest|cheap|cheaper|best|better|most|leading|number one|#1|only|first)\b"
    r"|\b(guarantee\w*|certif\w*|compliant|compliance|soc\s?2|iso|hipaa|fedramp|gdpr|pci)\b"
    r"|\b(uptime|sla|latency|storage|encrypt\w*|secure|security)\b"
    r"|\b(customers?|teams|companies|users|trust\w*)\b"
    r"|\b(price|prices|pricing|plans?|free|trial|cost\w*)\b"
    r"|\b(founded|since|headquarter\w*|regions?|data cent(?:er|re)s?)\b",
    re.IGNORECASE,
)


def looks_like_claim(sentence: str) -> bool:
    return bool(_CLAIM_HINT.search(sentence))


# ---------- applying accepted edits ----------

def apply_edits(draft: str, edits: list[tuple[int, int, str]]) -> tuple[str, list[tuple[int, int, str]]]:
    """Apply non-overlapping (start, end, text) edits. Returns (new_text, skipped_edits).

    Edits that overlap an earlier edit are skipped (and returned) instead of corrupting the text.
    Pure insertions (start == end) are allowed.
    """
    ordered = sorted(edits, key=lambda e: (e[0], -e[1]))
    applied: list[tuple[int, int, str]] = []
    skipped: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, text in ordered:
        start = max(0, min(start, len(draft)))
        end = max(start, min(end, len(draft)))
        if start < last_end:
            skipped.append((start, end, text))
            continue
        applied.append((start, end, text))
        last_end = max(last_end, end)
    out: list[str] = []
    pos = 0
    for start, end, text in applied:
        out.append(draft[pos:start])
        out.append(text)
        pos = end
    out.append(draft[pos:])
    return "".join(out), skipped
