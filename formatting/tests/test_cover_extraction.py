"""Cover-page structure extraction: heading, address, fields, options, indent.

The synthetic sample reproduces the reported real-world template shape:
address top-right, big centered EXAM heading, "Label:" fill-in rows,
"A)" option labels, indented questions, no header rule.
"""
import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from formatting import extract, labeling
from formatting import spec as spec_module

PAGE_W, PAGE_H = A4


def _sample_pdf(with_rule=False):
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 11)

    # Address block, top right.
    for i, line in enumerate(
        ["kinforduniversity.com", "Kinford University", "222 555 7777"]
    ):
        c.drawRightString(PAGE_W - 20 * mm, PAGE_H - (18 + i * 5) * mm, line)

    if with_rule:
        c.setLineWidth(1)
        c.line(20 * mm, PAGE_H - 38 * mm, PAGE_W - 20 * mm, PAGE_H - 38 * mm)

    # Big centered heading.
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 50 * mm, "EXAM")
    c.setFont("Helvetica", 11)

    # Candidate fill-in rows.
    c.drawString(25 * mm, PAGE_H - 65 * mm, "Name: ____________")
    c.drawString(25 * mm, PAGE_H - 72 * mm, "Date: ____________")
    c.drawString(25 * mm, PAGE_H - 79 * mm, "Class: ____________")

    # Two indented questions with A) options; multi-line for spacing stats.
    y = PAGE_H - 95 * mm
    for qnum in (1, 2):
        c.drawString(38 * mm, y, f"{qnum}. A question about a topic, with text?")
        y -= 6 * mm
        for letter in "ABC":
            c.drawString(45 * mm, y, f"{letter}) An answer option here")
            y -= 6 * mm
        y -= 6 * mm

    for i in range(5):  # wrapped paragraph for leading measurement
        c.drawString(25 * mm, y, f"A long explanatory paragraph line number {i} here.")
        y -= 5.5 * mm

    c.showPage()
    c.save()
    return buf.getvalue()


def _derive(pdf_bytes):
    m = extract.extract_measurements(pdf_bytes)
    labels = labeling.HeuristicLabeler().label(m)
    return spec_module.derive_spec(m, labels)


def test_cover_structure_extracted():
    spec, confidence, notes = _derive(_sample_pdf())
    assert spec["cover_heading"] == "EXAM"
    assert "Kinford University" in spec["address_text"]
    assert "Date:" not in spec["address_text"]  # fields must not bleed into address
    assert spec["candidate_fields"] == ["Name", "Date", "Class"]
    assert spec["option_label_style"] == "A)"
    # Questions start 38mm from the edge; left margin is ~25mm -> ~13mm indent.
    assert spec["question_indent_mm"] == pytest.approx(13.0, abs=2.5)
    assert spec["show_header_rule"] is False
    # Structural finds always require the visual review.
    assert confidence["cover_layout"] is False
    assert any("cover page" in n for n in notes)


def test_header_rule_detected_when_present():
    spec, _, _ = _derive(_sample_pdf(with_rule=True))
    assert spec["show_header_rule"] is True


def test_plain_document_has_no_cover_structure():
    """No heading/address/fields -> confidently absent, defaults kept."""
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 11)
    y = PAGE_H - 30 * mm
    for i in range(12):
        c.drawString(20 * mm, y, f"Body paragraph line {i} with plain content only.")
        y -= 6 * mm
    c.showPage()
    c.save()

    spec, confidence, _ = _derive(buf.getvalue())
    assert spec["cover_heading"] == ""
    assert spec["address_text"] == ""
    assert spec["candidate_fields"] == []
    assert confidence["cover_layout"] is True


def test_times_are_not_questions():
    """'4:00 PM' must not count as question number 4 (indent poisoning)."""
    assert labeling.QUESTION_START_RE.match("4:00 PM - 5:30 PM") is None
    assert labeling.QUESTION_START_RE.match("4. A real question") is not None
    assert labeling.QUESTION_START_RE.match("12) Another question") is not None
