"""In-place injection into .docx: detection, prototypes, preservation."""
import io
import zipfile
from decimal import Decimal

import pytest

from formatting import inject_docx
from formatting.pdf import PaperData, QuestionData, SectionData
from .docx_factory import build_docx, para, standard_exam_docx


def _paper_data():
    data = PaperData(title="Real Midterm", course_code="CS-101", duration_text="2 hours")
    data.sections = [
        SectionData(
            title="Section A",
            questions=[
                QuestionData(
                    item_type="mcq", body="Which organelle produces ATP?",
                    marks=Decimal(1),
                    choices=[("Mitochondrion", True), ("Ribosome", False), ("Nucleus", False)],
                ),
                QuestionData(
                    item_type="essay", body="Discuss cellular respiration.",
                    marks=Decimal(5),
                ),
            ],
        )
    ]
    return data


def test_detection_finds_question_region_confidently():
    detection = inject_docx.detect(standard_exam_docx())
    assert not detection.ambiguous
    blocks = detection.blocks
    # Region starts at/before the first question-ish content and excludes header
    assert blocks[detection.start]["text"] not in ("Kinford University", "EXAM")
    kinds_in_region = {b["kind"] for b in blocks[detection.start:detection.end + 1]}
    assert "question" in kinds_in_region
    assert "option" in kinds_in_region
    # Header and field table are OUTSIDE the region.
    header_indices = [b["index"] for b in blocks if b["text"] in ("Kinford University", "EXAM")]
    assert all(i < detection.start for i in header_indices)


def test_single_question_is_ambiguous():
    doc = build_docx(para("Title") + para("1. Only one question here?"))
    detection = inject_docx.detect(doc)
    assert detection.ambiguous


def test_manual_region_overrides_detection():
    doc = build_docx(para("Title") + para("1. Only one question here?") + para(""))
    detection = inject_docx.detect(doc, manual_region=(1, 2))
    assert not detection.ambiguous
    assert (detection.start, detection.end) == (1, 2)


def test_generate_replaces_dummy_content_with_real_questions():
    source = standard_exam_docx()
    detection = inject_docx.detect(source)
    out = inject_docx.generate(source, (detection.start, detection.end), _paper_data())
    doc = zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml").decode()

    assert "Which organelle produces ATP?" in doc      # real question in
    assert "Mitochondrion" in doc
    assert "What is the purpose of a loop?" not in doc  # dummy questions gone
    assert "Linked list" not in doc
    assert "Section A" in doc                           # section heading injected


def test_everything_outside_region_is_preserved():
    source = standard_exam_docx()
    detection = inject_docx.detect(source)
    out = inject_docx.generate(source, (detection.start, detection.end), _paper_data())
    doc = zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml").decode()

    assert "Kinford University" in doc
    assert "EXAM" in doc
    assert "End of dummy content note" in doc
    # Untouched zip entries are byte-identical.
    zin = zipfile.ZipFile(io.BytesIO(source))
    zout = zipfile.ZipFile(io.BytesIO(out))
    assert zin.read("word/styles.xml") == zout.read("word/styles.xml")
    assert zin.read("[Content_Types].xml") == zout.read("[Content_Types].xml")


def test_option_label_style_follows_dummy():
    source = standard_exam_docx()  # dummy uses "A)"
    detection = inject_docx.detect(source)
    out = inject_docx.generate(source, (detection.start, detection.end), _paper_data())
    doc = zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml").decode()
    assert "A) Mitochondrion" in doc
    assert "A. Mitochondrion" not in doc


def test_numbered_prototype_does_not_get_literal_numbers():
    """Dummy questions use Word auto-numbering -> no literal '1.' injected."""
    source = standard_exam_docx()
    detection = inject_docx.detect(source)
    out = inject_docx.generate(source, (detection.start, detection.end), _paper_data())
    doc = zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml").decode()
    assert "1. Which organelle" not in doc  # numbering comes from numPr
    assert "<w:numPr>" in doc               # prototypes carried the numbering


def test_recognized_fields_are_remapped():
    source = standard_exam_docx()
    detection = inject_docx.detect(source)
    out = inject_docx.generate(source, (detection.start, detection.end), _paper_data())
    doc = zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml").decode()
    assert "Real Midterm" in doc                        # Class: -> paper title
    assert "Introduction to Programming" not in doc     # old value gone


def test_xml_special_characters_in_content_are_safe():
    data = _paper_data()
    data.sections[0].questions[0].body = 'Is "x < y & y > z" valid <XML>?'
    source = standard_exam_docx()
    detection = inject_docx.detect(source)
    out = inject_docx.generate(source, (detection.start, detection.end), data)
    # Must remain a parseable document.
    import xml.etree.ElementTree as ET

    doc = zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml")
    ET.fromstring(doc)
