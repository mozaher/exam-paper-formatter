"""Deterministic exam-paper PDF builder.

Pure-Python rendering with ReportLab: no LaTeX, no subprocess, no shell, no
network — the same inputs always produce the same layout. Template values are
data poured into fixed slots; every piece of user text is XML-escaped before
it reaches the layout engine, so content can never carry markup or
executable anything.

Two outputs from one paper:
- the QUESTION PAPER (what candidates see), and
- the MARKING SCHEME (adds correct answers and marking guides, clearly
  labelled as staff-only).
"""
import io
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONTS = {
    "serif": {"base": "Times-Roman", "bold": "Times-Bold", "italic": "Times-Italic"},
    "sans": {"base": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"},
}
PAGE_SIZES = {"A4": A4, "LETTER": LETTER}
OPTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _esc(text):
    return escape(text or "")


def _fmt_marks(value):
    d = Decimal(value)
    text = f"{d.normalize():f}"
    unit = "mark" if d == 1 else "marks"
    return f"[{text} {unit}]"


def _paragraphs(text, style):
    """User text -> Paragraph flowables. Blank line = new paragraph."""
    flowables = []
    for part in (text or "").split("\n\n"):
        part = part.strip()
        if part:
            flowables.append(Paragraph(_esc(part).replace("\n", "<br/>"), style))
    return flowables


class AnswerLines(Flowable):
    """Ruled lines for handwritten answers."""

    def __init__(self, count, spacing=9 * mm):
        super().__init__()
        self.count = count
        self.spacing = spacing

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        self.height = self.count * self.spacing
        return (self.width, self.height)

    def draw(self):
        self.canv.setStrokeColor(colors.Color(0.7, 0.7, 0.7))
        self.canv.setLineWidth(0.6)
        for i in range(self.count):
            y = self.height - (i + 1) * self.spacing + 2 * mm
            self.canv.line(0, y, self.width, y)


def _answer_line_count(marks):
    try:
        m = int(Decimal(marks))
    except Exception:
        m = 1
    return max(4, min(3 + m * 2, 20))


def _styles(font_key):
    font = FONTS.get(font_key, FONTS["serif"])
    base = dict(fontName=font["base"], fontSize=11, leading=15)
    return {
        "institution": ParagraphStyle(
            "institution", fontName=font["bold"], fontSize=16, leading=20,
            alignment=TA_CENTER, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=font["base"], fontSize=11, leading=14,
            alignment=TA_CENTER, textColor=colors.Color(0.25, 0.25, 0.25),
        ),
        "examtitle": ParagraphStyle(
            "examtitle", fontName=font["bold"], fontSize=13, leading=17,
            alignment=TA_CENTER, spaceBefore=8, spaceAfter=4,
        ),
        "meta": ParagraphStyle("meta", alignment=TA_CENTER, **base),
        "notice": ParagraphStyle(
            "notice", fontName=font["bold"], fontSize=11, leading=14,
            alignment=TA_CENTER, textColor=colors.Color(0.7, 0.1, 0.1),
            spaceBefore=4, spaceAfter=2,
        ),
        "heading": ParagraphStyle(
            "heading", fontName=font["bold"], fontSize=12, leading=16,
            spaceBefore=14, spaceAfter=4,
        ),
        "body": ParagraphStyle("body", spaceAfter=4, **base),
        "instructions": ParagraphStyle(
            "instructions", fontName=font["italic"], fontSize=10.5, leading=14,
            spaceAfter=4, textColor=colors.Color(0.15, 0.15, 0.15),
        ),
        "option": ParagraphStyle(
            "option", leftIndent=10 * mm, spaceAfter=2, **base,
        ),
        "answer": ParagraphStyle(
            "answer", fontName=font["base"], fontSize=10.5, leading=14,
            leftIndent=6 * mm, spaceAfter=3,
            backColor=colors.Color(0.93, 0.96, 0.93),
            borderPadding=4,
        ),
        "answerlabel": ParagraphStyle(
            "answerlabel", fontName=font["bold"], fontSize=10.5, leading=14,
            leftIndent=6 * mm, spaceBefore=4, spaceAfter=2,
            textColor=colors.Color(0.1, 0.4, 0.1),
        ),
        "marks": ParagraphStyle(
            "marks", fontName=font["bold"], fontSize=10.5, leading=14,
            alignment=2,  # right
        ),
        "font": font,
    }


def _header_block(paper, styles):
    template = paper.template
    flow = []
    if template is not None and template.institution_name:
        flow.append(Paragraph(_esc(template.institution_name), styles["institution"]))
        if template.subtitle:
            flow.append(Paragraph(_esc(template.subtitle), styles["subtitle"]))
        flow.append(
            HRFlowable(width="100%", thickness=1, color=colors.black, spaceBefore=6, spaceAfter=2)
        )
    flow.append(Paragraph(_esc(paper.title), styles["examtitle"]))

    meta_bits = []
    if paper.course_code:
        meta_bits.append(f"Course: {_esc(paper.course_code)}")
    if paper.exam_date:
        meta_bits.append(f"Date: {paper.exam_date.strftime('%d %B %Y')}")
    if paper.duration_minutes:
        meta_bits.append(f"Duration: {paper.duration_display()}")
    meta_bits.append(f"Total marks: {Decimal(paper.total_marks).normalize():f}")
    flow.append(Paragraph(" &nbsp;·&nbsp; ".join(meta_bits), styles["meta"]))
    return flow


def _instructions_block(paper, styles):
    text = paper.effective_instructions()
    if not text.strip():
        return []
    inner = [Paragraph("Instructions to candidates", styles["heading"])]
    inner += _paragraphs(text, styles["instructions"])
    table = Table([[inner]], colWidths=["100%"])
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [Spacer(0, 6), table]


def _question_flowables(number, pq, paper, styles, answers):
    item = pq.item
    flow = []

    body_style = styles["body"]
    first, *rest = (item.body or "").split("\n\n") or [""]
    lead = Paragraph(
        f"<b>{number}.</b> &nbsp;{_esc(first.strip()).replace(chr(10), '<br/>')}",
        body_style,
    )
    marks_para = Paragraph(_fmt_marks(pq.marks), styles["marks"])
    head = Table([[lead, marks_para]], colWidths=["*", 28 * mm])
    head.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    flow.append(head)
    for part in rest:
        if part.strip():
            flow.append(Paragraph(_esc(part.strip()).replace("\n", "<br/>"), body_style))

    if item.item_type == "mcq":
        choices = list(item.choices.all())
        for i, choice in enumerate(choices):
            letter = OPTION_LETTERS[i] if i < len(OPTION_LETTERS) else str(i + 1)
            text = _esc(choice.text)
            if answers and choice.is_correct:
                flow.append(
                    Paragraph(f"<b>{letter}. {text} &nbsp;✓</b>", styles["option"])
                )
            else:
                flow.append(Paragraph(f"{letter}. {text}", styles["option"]))
        if answers:
            correct = [
                OPTION_LETTERS[i] for i, c in enumerate(choices) if c.is_correct
            ]
            flow.append(
                Paragraph(f"Answer: {', '.join(correct) or '—'}", styles["answerlabel"])
            )
    else:
        if answers and item.model_answer.strip():
            flow.append(Paragraph("Marking guide", styles["answerlabel"]))
            flow += _paragraphs(item.model_answer, styles["answer"])
        elif not answers and paper.include_answer_space:
            flow.append(Spacer(0, 4))
            flow.append(AnswerLines(_answer_line_count(pq.marks)))

    flow.append(Spacer(0, 10))
    return flow


def build_paper_pdf(paper, answers=False):
    """Render a Paper to PDF bytes. answers=True adds the marking scheme."""
    template = paper.template
    font_key = template.font if template is not None else "serif"
    size_key = template.paper_size if template is not None else "A4"
    styles = _styles(font_key)
    footer_text = template.footer_text if template is not None else ""
    font = styles["font"]

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font["base"], 9)
        canvas.setFillColor(colors.Color(0.3, 0.3, 0.3))
        y = 12 * mm
        if footer_text:
            canvas.drawString(doc.leftMargin, y, footer_text)
        label = "MARKING SCHEME — " if answers else ""
        canvas.drawRightString(
            doc.pagesize[0] - doc.rightMargin, y,
            f"{label}Page {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=PAGE_SIZES.get(size_key, A4),
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=paper.title,
        author=template.institution_name if template is not None else "",
    )

    story = _header_block(paper, styles)
    if answers:
        story.append(
            Paragraph(
                "MARKING SCHEME — NOT FOR DISTRIBUTION TO CANDIDATES",
                styles["notice"],
            )
        )
    story += _instructions_block(paper, styles)
    story.append(Spacer(0, 8))

    number = 0
    for section in paper.sections.all():
        section_flow = [Paragraph(_esc(section.title), styles["heading"])]
        if section.instructions.strip():
            section_flow += _paragraphs(section.instructions, styles["instructions"])
        story += section_flow
        for pq in section.questions.select_related("item").prefetch_related("item__choices"):
            number += 1
            story.append(KeepTogether(_question_flowables(number, pq, paper, styles, answers)))

    if number == 0:
        story.append(Paragraph("This paper has no questions yet.", styles["body"]))

    story.append(Spacer(0, 12))
    story.append(HRFlowable(width="40%", thickness=0.8, color=colors.black))
    story.append(Paragraph("END OF PAPER", styles["meta"]))

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buf.getvalue()
