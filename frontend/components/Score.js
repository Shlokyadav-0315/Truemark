const PEN = { high: 18, medium: 9, low: 4, info: 0 };
const OPEN = ["pending", "rejected", "escalated"];
const C = 2 * Math.PI * 44; // ring circumference

function band(score) {
  if (score >= 90) return ["Ready to ship", "good"];
  if (score >= 70) return ["Nearly there", "ok"];
  if (score >= 40) return ["Needs work", "warn"];
  return ["High risk", "bad"];
}

export default function Score({ findings, degraded }) {
  const openF = findings.filter((f) => f.status !== "verified" && OPEN.includes(f.decision));
  const score = Math.max(0, 100 - openF.reduce((t, f) => t + (PEN[f.severity] || 0), 0));
  const [label, tone] = band(score);
  const n = (s) => openF.filter((f) => f.severity === s).length;
  const fixed = findings.filter((f) => ["accepted", "edited"].includes(f.decision)).length;
  return (
    <div className={`score score-${tone}`} role="status" aria-live="polite">
      <div className="score-ring" role="img" aria-label={`Brand compliance score ${score} out of 100`}>
        <svg viewBox="0 0 100 100" aria-hidden="true">
          <defs>
            <linearGradient id="scoreGrad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#a78bff" />
              <stop offset="1" style={{ stopColor: "currentColor" }} />
            </linearGradient>
          </defs>
          <circle className="ring-bg" cx="50" cy="50" r="44" />
          <circle
            className="ring-fg"
            cx="50"
            cy="50"
            r="44"
            stroke="url(#scoreGrad)"
            strokeDasharray={`${(score / 100) * C} ${C}`}
          />
        </svg>
        <div className="ring-num">
          <span>{score}</span>
          <small>of 100</small>
        </div>
      </div>
      <div className="score-body">
        <strong>{label}</strong>
        <p className="score-sub">
          {n("high")} high · {n("medium")} medium · {n("low")} low open
          {fixed ? ` · ${fixed} fixed` : ""}
          {degraded ? " · rules only, AI checks off" : ""}
        </p>
      </div>
    </div>
  );
}
