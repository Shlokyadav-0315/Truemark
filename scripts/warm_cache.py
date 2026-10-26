"""Pre-compute every LLM call the demo path makes, so judges get instant, rate-limit-proof results.

Run once with your real key and the SAME RETRIEVER/GEMINI_MODEL you deploy with, then commit llm_cache/:
    python -m scripts.warm_cache
The cache key is the full prompt + model name, so changing either invalidates entries (just re-run)."""
import asyncio
import json

from app.audit import Engine, get_rewrites, run_audit
from app.config import DATA_DIR, get_settings
from app.schemas import AuditRequest


async def main() -> None:
    engine = Engine(get_settings())
    if not engine.llm.live:
        raise SystemExit("No live LLM configured. Set GEMINI_API_KEY first.")
    drafts = json.loads((DATA_DIR / "demo_drafts.json").read_text(encoding="utf-8"))
    for d in drafts:
        audit = await run_audit(engine, "demo", AuditRequest(draft=d["text"], channel=d["channel"]))
        print(f"{d['id']}: {audit.summary.total} findings, llm_status={audit.llm_status}")
        for f in audit.findings:
            if f.status == "verified":
                continue
            r = await get_rewrites(engine, audit, f.id)
            print(f"   {f.id} {f.title}: {len(r.options)} options ({r.llm_status})")
            await asyncio.sleep(4)  # stay under free-tier requests-per-minute


if __name__ == "__main__":
    asyncio.run(main())
