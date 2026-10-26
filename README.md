# TrueMark backend

Fact-grounded brand governance API. Content goes in, it is checked against brand rules and an approved
fact sheet, every issue comes back with an exact citation, and a human accepts, edits, rejects or escalates.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add GEMINI_API_KEY (free key from Google AI Studio)
set -a && source .env && set +a
uvicorn app.main:app --reload --port 8000     # docs at http://localhost:8000/docs
```
No key? It still runs: rules and numeric fact checks work, and the UI shows an "AI checks unavailable" banner.
Set `RETRIEVER=tfidf` to skip the embedding-model download.

## Tests (no network, no key)
```bash
python -m pytest -q        # or: python -m unittest discover -s tests
```

## How an audit works
1. **Rule linter** (`rules.py`): regex rules from `data/rules.json`. Each rule's citation is checked against the
   guideline text at startup; the server refuses to boot if a quote does not exist.
2. **Claim verifier** (`verifier.py`): claim-like sentences are matched to evidence (Chroma, TF-IDF fallback) and
   the LLM returns a strict JSON verdict. **Code then verifies the citation**: the chunk must be one we offered and
   the quote must appear verbatim in it. Otherwise the verdict is downgraded to "unverified".
3. **Rewriter** (`rewriter.py`): deterministic fixes where the guideline dictates one; otherwise 2-3 LLM options,
   each re-linted so an option that still breaks the rule is discarded.
4. **Human decision** (`audit.py`): accept / edit / reject / escalate per finding, all logged. Unresolved or
   escalated high-risk findings set `needs_approval`.
5. **Export**: Markdown (final text + findings + audit trail), JSON, or plain text.

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status; also used as the keep-warm ping |
| GET | `/samples` | demo drafts for the one-click flow |
| GET/POST/DELETE | `/documents`, `/ingest` | list / upload (.md .txt .pdf) / delete knowledge docs |
| POST | `/audit` | `{draft, channel, use_llm}` -> findings |
| POST | `/audit/{id}/findings/{fid}/rewrite` | 2-3 rewrite options |
| POST | `/audit/{id}/findings/{fid}/decision` | accept / edit / reject / escalate |
| GET | `/audit/{id}/export?format=md\|json\|txt` | download result |

Send `X-Workspace: <random id>` from the browser (any 6-40 chars of `A-Za-z0-9_-`) to get a private copy of the
knowledge base per visitor; without it requests use the shared `demo` workspace.

## Before you deploy
- Run `python -m scripts.warm_cache` with your real key and commit `llm_cache/` so the demo path never waits on the LLM.
- Free-tier Gemini may use prompts to improve Google's models: demo data only, never real corporate documents.
- Uploaded guideline docs feed semantic retrieval only. Rules come from `data/rules.json` (auto-extracting rules from
  uploads is a Round 3 item).
