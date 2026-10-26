"""Render an Audit as a styled PDF (reportlab, pure Python, standard fonts)."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Flowable, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

INK = colors.HexColor("#150E2B")
INK2 = colors.HexColor("#2B2059")
ORCHID = colors.HexColor("#A78BFF")
ROSE = colors.HexColor("#FF5C8A")
APRICOT = colors.HexColor("#E8893F")
LAGOON = colors.HexColor("#1FA8A0")
TEXT = colors.HexColor("#1B1233")
MUTED = colors.HexColor("#6B6290")
PAPER = colors.HexColor("#F4F0FF")
TRACK = colors.HexColor("#DDD5F7")

PEN = {"high": 18, "medium": 9, "low": 4, "info": 0}
OPEN = {"pending", "rejected", "escalated"}
SEV_COLOR = {"high": ROSE, "medium": APRICOT, "low": ORCHID, "info": MUTED}
DEC_LABEL = {"pending": "Pending", "accepted": "Accepted", "edited": "Edited",
             "rejected": "Kept as written", "escalated": "Escalated to approver"}


def _t(s: object) -> str:
    """Escape for Paragraph markup and drop characters the standard PDF fonts cannot draw."""
    s = str(s or "").replace("\u2011", "-").replace("\u2026", "...")
    s = s.encode("cp1252", errors="replace").decode("cp1252")
    return escape(s).replace("\n", "<br/>")


def compute_score(audit) -> int:
    pen = sum(PEN.get(f.severity, 0) for f in audit.findings
              if f.status != "verified" and f.decision in OPEN)
    return max(0, 100 - pen)


def _band(score: int):
    if score >= 90:
        return "Ready to ship", LAGOON
    if score >= 70:
        return "Nearly there", ORCHID
    if score >= 40:
        return "Needs work", APRICOT
    return "High risk", ROSE


class ScoreRing(Flowable):
    def __init__(self, score: int, color, size: float = 34 * mm):
        super().__init__()
        self.score, self.color, self.size = score, color, size
        self.width = self.height = size

    def draw(self):
        c, s = self.canv, self.size
        m = 5
        c.setLineCap(1)
        c.setLineWidth(8)
        c.setStrokeColor(TRACK)
        c.circle(s / 2, s / 2, s / 2 - m, stroke=1, fill=0)
        if self.score > 0:
            c.setStrokeColor(self.color)
            c.arc(m, m, s - m, s - m, startAng=90, extent=-3.6 * self.score)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 26)
        c.drawCentredString(s / 2, s / 2 - 4, str(self.score))
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        c.drawCentredString(s / 2, s / 2 - 16, "of 100")


def _styles():
    base = dict(fontName="Helvetica", textColor=TEXT, leading=14, fontSize=10, alignment=TA_LEFT)
    return {
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 8.5, "leading": 11, "textColor": MUTED}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold", "fontSize": 14, "leading": 18,
                                      "spaceBefore": 16, "spaceAfter": 6, "textColor": INK2}),
        "title": ParagraphStyle("title", **{**base, "fontName": "Helvetica-Bold", "fontSize": 20, "leading": 24}),
        "verdict": ParagraphStyle("verdict", **{**base, "fontName": "Helvetica-Bold", "fontSize": 17, "leading": 21}),
        "copy": ParagraphStyle("copy", **{**base, "fontSize": 11, "leading": 16}),
        "ftitle": ParagraphStyle("ftitle", **{**base, "fontName": "Helvetica-Bold", "fontSize": 11, "leading": 14}),
    }


def _finding_card(f, st):
    sev = SEV_COLOR.get(f.severity, MUTED)
    kind = "Conflicts with approved source" if f.status == "violation" else "Unverified claim"
    rows = [
        [Paragraph(_t(f.title), st["ftitle"]),
         Paragraph(f'<font color="{sev.hexval()}"><b>{_t(f.severity.upper())}</b></font>', st["small"])],
        [Paragraph(f'<i>"{_t(f.text)}"</i>', st["body"]), ""],
        [Paragraph(_t(f.explanation), st["body"]), ""],
    ]
    if f.citation:
        rows.append([Paragraph(f'<b>Source:</b> {_t(f.citation.doc_name)}, {_t(f.citation.section)}<br/>'
                               f'"{_t(f.citation.quote)}"', st["small"]), ""])
    dec = DEC_LABEL.get(f.decision, f.decision)
    extra = f' - "{_t(f.applied.text)}"' if f.applied else ""
    rows.append([Paragraph(f'<b>Decision:</b> {_t(dec)}{extra}'
                           + (f' ({_t(f.comment)})' if f.comment else ""), st["small"]), ""])
    t = Table(rows, colWidths=[None, 22 * mm])
    t.setStyle(TableStyle([
        ("SPAN", (0, 1), (1, 1)), ("SPAN", (0, 2), (1, 2)),
        *[("SPAN", (0, i), (1, i)) for i in range(3, len(rows))],
        ("BACKGROUND", (0, 0), (-1, -1), PAPER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, sev),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    return KeepTogether([t, Spacer(1, 6)])


def build_pdf(audit, final_text: str, skipped=None) -> bytes:
    st = _styles()
    score = compute_score(audit)
    label, color = _band(score)
    buf = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"TrueMark audit {audit.audit_id}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"TrueMark audit {audit.audit_id}", author="TrueMark")
    W = A4[0] - 36 * mm
    s = audit.summary
    story = []

    head = Table([[Paragraph('<font color="#FFFFFF"><b>TrueMark</b></font>', ParagraphStyle(
        "b", fontName="Helvetica-Bold", fontSize=15, leading=18)),
        Paragraph(f'<font color="#C9BCFF">Audit report &nbsp;|&nbsp; {_t(audit.created_at[:16].replace("T", " "))} UTC'
                  f' &nbsp;|&nbsp; {_t(audit.channel)}</font>',
                  ParagraphStyle("hr", fontName="Helvetica", fontSize=9, alignment=2))]],
        colWidths=[W * 0.4, W * 0.6])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), INK), ("LEFTPADDING", (0, 0), (-1, -1), 12),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 11),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 11), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [head, Spacer(1, 14)]

    status = "Needs manager approval" if audit.needs_approval else "Ready to publish"
    scol = ROSE if audit.needs_approval else LAGOON
    ai = "AI checks were off, so only rules and exact figures were checked." if audit.llm_status.startswith(
        ("degraded", "skipped")) else "AI fact checks ran."
    info = [Paragraph(f'<font color="{scol.hexval()}">{_t(status)}</font>', st["verdict"]),
            Spacer(1, 3), Paragraph(_t(label) + " - brand compliance score", st["body"]), Spacer(1, 6),
            Paragraph(f"{s.high} high, {s.medium} medium, {s.low} low-risk findings &nbsp;|&nbsp; "
                      f"{s.violations} breaches, {s.unverified} unverified, {s.verified} verified", st["small"]),
            Paragraph(_t(ai), st["small"])]
    top = Table([[ScoreRing(score, color), info]], colWidths=[40 * mm, W - 40 * mm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [top]

    story += [Paragraph("Final text", st["h2"])]
    box = Table([[Paragraph(_t(final_text), st["copy"])]], colWidths=[W])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PAPER), ("BOX", (0, 0), (-1, -1), 0.5, TRACK),
                             ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                             ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    story += [box]

    bad = [f for f in audit.findings if f.status != "verified"]
    story += [Paragraph(f"Findings and decisions ({len(bad)})", st["h2"])]
    if not bad:
        story += [Paragraph("No issues were found.", st["body"])]
    story += [_finding_card(f, st) for f in bad]

    ok = [f for f in audit.findings if f.status == "verified"]
    if ok:
        story += [Paragraph("Verified claims", st["h2"])]
        for f in ok:
            q = f' - "{_t(f.citation.quote)}"' if f.citation else ""
            story += [Paragraph(f'<font color="{LAGOON.hexval()}"><b>OK</b></font> &nbsp;{_t(f.text)}{q}', st["small"]),
                      Spacer(1, 3)]

    story += [Paragraph("Original draft", st["h2"]), Paragraph(_t(audit.draft), st["body"])]
    if skipped:
        story += [Paragraph("Edits skipped because they overlapped another edit", st["h2"])]
        story += [Paragraph(f"- {_t(e.text)}", st["small"]) for e in skipped]
    story += [Paragraph("Audit trail", st["h2"])]
    for e in audit.events:
        line = f"{_t(e.ts[:19].replace('T', ' '))} &nbsp; {_t(e.type)}"
        if e.finding_id:
            line += f" [{_t(e.finding_id)}]"
        if e.detail:
            line += f": {_t(e.detail)}"
        story.append(Paragraph(line, st["small"]))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
