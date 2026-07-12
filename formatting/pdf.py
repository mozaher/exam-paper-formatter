"""Deterministic exam-paper PDF builder.

Pure-Python rendering with ReportLab: no LaTeX, no subprocess, no shell, no
network — the same inputs always produce the same layout. All layout values
come from a bounded formatting spec (see spec.py); all content values are
named slots. Every piece of user text is XML-escaped before it reaches the
layout engine, so content can never carry markup or executable anything.

The core builder consumes plain data (PaperData), so the same code renders
real papers from the ORM and spec previews with dummy content.

Two outputs from one paper:
- the QUESTION PAPER (what candidates see), and
- the MARKING SCHEME (adds correct answers and marking guides, clearly
  labelled as staff-only).
"""
import io
from dataclasses import dataclass, field
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

from . import spec as spec_module

FONTS = {
    "serif": {"base": "Times-Roman", "bold": "Times-Bold", "italic": "Times-Italic"},
    "sans": {"base": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"},
}
PAGE_SIZES = {"A4": A4, "LETTER": LETTER}
OPTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ANSWER_LINE_GAP_MM = 9.0


# ---------------------------------------------------------------------------
# Neutral data model consumed by the builder.
# ---------------------------------------------------------------------------

@dataclass
class QuestionData:
    item_type: str                  # "mcq" | "essay"
    body: str
    marks: Decimal
    choices: list = field(default_factory=list)  # [(text, is_correct)]
    model_answer: str = ""


@dataclass
class SectionData:
    title: str
    instructions: str = ""
    questions: list = field(default_factory=list)


@dataclass
class PaperData:
    title: str
    course_code: str = ""
    exam_date = None
    duration_text: str = ""
    instructions: str = ""
    include_answer_space: bool = True
    institution_name: str = ""
    subtitle: str = ""
    footer_text: str = ""
    cover_heading: str = ""
    address_text: str = ""
    candidate_fields: list = field(default_factory=list)
    sections: list = field(default_factory=list)

    @property
    def total_marks(self):
        return sum(
            (Decimal(q.marks) for s in self.sections for q in s.questions),
            Decimal(0),
        )


def paper_to_data(paper) -> PaperData:
    """Adapter: ORM Paper -> neutral PaperData."""
    template = paper.template
    data = PaperData(
        title=paper.title,
        course_code=paper.course_code,
        duration_text=paper.duration_display(),
        instructions=paper.effective_instructions(),
        include_answer_space=paper.include_answer_space,
        institution_name=template.institution_name if template else "",
        subtitle=template.subtitle if template else "",
        footer_text=template.footer_text if template else "",
        cover_heading=template.cover_heading if template else "",
        address_text=template.address_text if template else "",
        candidate_fields=list(template.candidate_fields or []) if template else [],
    )
    data.exam_date = paper.exam_date
    for section in paper.sections.all():
        sdata = SectionData(title=section.title, instructions=section.instructions)
        for pq in section.questions.select_related("item").prefetch_related(
            "item__choices"
        ):
            item = pq.item
            sdata.questions.append(
                QuestionData(
                    item_type=item.item_type,
                    body=item.body,
                    marks=pq.marks,
                    choices=[(c.text, c.is_correct) for c in item.choices.all()],
                    model_answer=item.model_answer,
                )
            )
        data.sections.append(sdata)
    return data


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

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

    def __init__(self, count, spacing=ANSWER_LINE_GAP_MM * mm):
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


def _answer_line_count(marks, layout):
    """Marks -> ruled line count via the spec's marks-to-space rule."""
    try:
        m = float(marks)
    except (TypeError, ValueError):
        m = 1.0
    height_mm = layout["answer_base_mm"] + m * layout["answer_per_mark_mm"]
    height_mm = max(18.0, min(height_mm, 220.0))
    return max(2, round(height_mm / ANSWER_LINE_GAP_MM))


def _styles(layout):
    font = FONTS.get(layout["font_family"], FONTS["serif"])
    size = layout["font_size_pt"]
    leading = size * layout["line_spacing"]
    para_after = layout["para_spacing_pt"]
    base = dict(fontName=font["base"], fontSize=size, leading=leading)
    return {
        "institution": ParagraphStyle(
            "institution", fontName=font["bold"], fontSize=size + 5,
            leading=(size + 5) * 1.25, alignment=TA_CENTER, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=font["base"], fontSize=size, leading=leading,
            alignment=TA_CENTER, textColor=colors.Color(0.25, 0.25, 0.25),
        ),
        "examtitle": ParagraphStyle(
            "examtitle", fontName=font["bold"], fontSize=size + 2,
            leading=(size + 2) * 1.3, alignment=TA_CENTER, spaceBefore=8, spaceAfter=4,
        ),
        "meta": ParagraphStyle("meta", alignment=TA_CENTER, **base),
        "notice": ParagraphStyle(
            "notice", fontName=font["bold"], fontSize=size, leading=leading,
            alignment=TA_CENTER, textColor=colors.Color(0.7, 0.1, 0.1),
            spaceBefore=4, spaceAfter=2,
        ),
        "heading": ParagraphStyle(
            "heading", fontName=font["bold"], fontSize=size + 1,
            leading=(size + 1) * 1.3, spaceBefore=14, spaceAfter=4,
        ),
        "body": ParagraphStyle("body", spaceAfter=para_after, **base),
        "instructions": ParagraphStyle(
            "instructions", fontName=font["italic"], fontSize=size - 0.5,
            leading=(size - 0.5) * layout["line_spacing"], spaceAfter=4,
            textColor=colors.Color(0.15, 0.15, 0.15),
        ),
        "option": ParagraphStyle(
            "option",
            leftIndent=(10 + layout["question_indent_mm"]) * mm,
            spaceAfter=2,
            **base,
        ),
        "qbody": ParagraphStyle(
            "qbody", spaceAfter=para_after,
            leftIndent=layout["question_indent_mm"] * mm, **base,
        ),
        "address": ParagraphStyle(
            "address", fontName=font["base"], fontSize=size - 1.5,
            leading=(size - 1.5) * 1.25, alignment=2,  # right
            textColor=colors.Color(0.25, 0.25, 0.25),
        ),
        "coverheading": ParagraphStyle(
            "coverheading", fontName=font["bold"], fontSize=size + 7,
            leading=(size + 7) * 1.3, alignment=TA_CENTER,
            spaceBefore=10, spaceAfter=8,
        ),
        "candidate": ParagraphStyle(
            "candidate", spaceAfter=8, **base,
        ),
        "answer": ParagraphStyle(
            "answer", fontName=font["base"], fontSize=size - 0.5,
            leading=(size - 0.5) * layout["line_spacing"], leftIndent=6 * mm,
            spaceAfter=3, backColor=colors.Color(0.93, 0.96, 0.93), borderPadding=4,
        ),
        "answerlabel": ParagraphStyle(
            "answerlabel", fontName=font["bold"], fontSize=size - 0.5,
            leading=(size - 0.5) * 1.3, leftIndent=6 * mm, spaceBefore=4,
            spaceAfter=2, textColor=colors.Color(0.1, 0.4, 0.1),
        ),
        "marks": ParagraphStyle(
            "marks", fontName=font["bold"], fontSize=size - 0.5,
            leading=(size - 0.5) * 1.3, alignment=2,
        ),
        "font": font,
    }


def _header_block(data: PaperData, styles, layout):
    flow = []
    if data.address_text:
        for line in data.address_text.splitlines():
            if line.strip():
                flow.append(Paragraph(_esc(line.strip()), styles["address"]))
        flow.append(Spacer(0, 6))
    if data.institution_name:
        flow.append(Paragraph(_esc(data.institution_name), styles["institution"]))
        if data.subtitle:
            flow.append(Paragraph(_esc(data.subtitle), styles["subtitle"]))
        if layout["show_header_rule"]:
            flow.append(
                HRFlowable(width="100%", thickness=1, color=colors.black, spaceBefore=6, spaceAfter=2)
            )
    if data.cover_heading:
        flow.append(Paragraph(_esc(data.cover_heading), styles["coverheading"]))
    flow.append(Paragraph(_esc(data.title), styles["examtitle"]))

    meta_bits = []
    if data.course_code:
        meta_bits.append(f"Course: {_esc(data.course_code)}")
    if data.exam_date:
        meta_bits.append(f"Date: {data.exam_date.strftime('%d %B %Y')}")
    if data.duration_text:
        meta_bits.append(f"Duration: {_esc(data.duration_text)}")
    meta_bits.append(f"Total marks: {Decimal(data.total_marks).normalize():f}")
    flow.append(Paragraph(" &nbsp;·&nbsp; ".join(meta_bits), styles["meta"]))

    if data.candidate_fields:
        flow.append(Spacer(0, 10))
        for label in data.candidate_fields:
            flow.append(
                Paragraph(
                    f"{_esc(label)}: " + "_" * 46,
                    styles["candidate"],
                )
            )
    return flow


def _instructions_block(data: PaperData, styles):
    text = data.instructions
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


def _option_label(index, style):
    letter = OPTION_LETTERS[index] if index < len(OPTION_LETTERS) else str(index + 1)
    if style == "A)":
        return f"{letter})"
    if style == "(a)":
        return f"({letter.lower()})"
    if style == "a)":
        return f"{letter.lower()})"
    return f"{letter}."


def _question_flowables(number, q: QuestionData, data: PaperData, styles, layout, answers):
    flow = []
    body_style = styles["qbody"]
    first, *rest = (q.body or "").split("\n\n") or [""]
    lead = Paragraph(
        f"<b>{number}.</b> &nbsp;{_esc(first.strip()).replace(chr(10), '<br/>')}",
        body_style,
    )
    marks_para = Paragraph(_fmt_marks(q.marks), styles["marks"])
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

    if q.item_type == "mcq":
        style_key = layout["option_label_style"]
        for i, (text, is_correct) in enumerate(q.choices):
            label = _option_label(i, style_key)
            if answers and is_correct:
                flow.append(Paragraph(f"<b>{label} {_esc(text)} &nbsp;✓</b>", styles["option"]))
            else:
                flow.append(Paragraph(f"{label} {_esc(text)}", styles["option"]))
        if answers:
            correct = [
                _option_label(i, style_key) for i, (_, ok) in enumerate(q.choices) if ok
            ]
            flow.append(
                Paragraph(f"Answer: {', '.join(correct) or '—'}", styles["answerlabel"])
            )
    else:
        if answers and q.model_answer.strip():
            flow.append(Paragraph("Marking guide", styles["answerlabel"]))
            flow += _paragraphs(q.model_answer, styles["answer"])
        elif not answers and data.include_answer_space:
            flow.append(Spacer(0, 4))
            flow.append(AnswerLines(_answer_line_count(q.marks, layout)))

    flow.append(Spacer(0, 10))
    return flow


def build_pdf(data: PaperData, layout: dict = None, answers: bool = False) -> bytes:
    """Render neutral paper data with a formatting spec. Returns PDF bytes."""
    layout = spec_module.clamp_spec(layout or {})
    styles = _styles(layout)
    font = styles["font"]
    footer_text = data.footer_text

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
        pagesize=PAGE_SIZES.get(layout["paper_size"], A4),
        leftMargin=layout["margin_left_mm"] * mm,
        rightMargin=layout["margin_right_mm"] * mm,
        topMargin=layout["margin_top_mm"] * mm,
        bottomMargin=layout["margin_bottom_mm"] * mm,
        title=data.title,
        author=data.institution_name,
    )

    story = _header_block(data, styles, layout)
    if answers:
        story.append(
            Paragraph("MARKING SCHEME — NOT FOR DISTRIBUTION TO CANDIDATES", styles["notice"])
        )
    story += _instructions_block(data, styles)
    story.append(Spacer(0, 8))

    number = 0
    for section in data.sections:
        story.append(Paragraph(_esc(section.title), styles["heading"]))
        if section.instructions.strip():
            story += _paragraphs(section.instructions, styles["instructions"])
        for q in section.questions:
            number += 1
            story.append(
                KeepTogether(_question_flowables(number, q, data, styles, layout, answers))
            )

    if number == 0:
        story.append(Paragraph("This paper has no questions yet.", styles["body"]))

    story.append(Spacer(0, 12))
    story.append(HRFlowable(width="40%", thickness=0.8, color=colors.black))
    story.append(Paragraph("END OF PAPER", styles["meta"]))

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buf.getvalue()


def build_paper_pdf(paper, answers=False):
    """Render an ORM Paper using its template's confirmed formatting spec."""
    return build_pdf(
        paper_to_data(paper),
        layout=spec_module.spec_from_template(paper.template),
        answers=answers,
    )
