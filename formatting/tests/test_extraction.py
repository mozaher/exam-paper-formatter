"""Extraction self-consistency: render with a known spec, measure it back.

The reference PDF is produced by our own deterministic renderer, so these
tests need no external compilers and pin down the geometry pipeline:
extract -> label -> derive must recover the spec it was rendered from.
"""
from decimal import Decimal

import pytest

from formatting import extract, labeling
from formatting import spec as spec_module
from formatting.pdf import PaperData, QuestionData, SectionData, build_pdf

KNOWN_SPEC = {
    "font_family": "sans",
    "font_size_pt": 12.0,
    "line_spacing": 1.5,
    "para_spacing_pt": 10.0,
    "margin_left_mm": 28.0,
    "margin_right_mm": 24.0,
    "margin_top_mm": 25.0,
    "margin_bottom_mm": 22.0,
    "paper_size": "A4",
    # Small answer spaces so all questions plus following text share pages —
    # blank-gap measurement needs text below each gap on the same page.
    "answer_base_mm": 8.0,
    "answer_per_mark_mm": 12.0,
}

LONG = (
    "This paragraph is intentionally long so that it wraps over multiple "
    "lines when rendered, giving the extractor several consecutive baselines "
    "to measure the line spacing from with reasonable statistical confidence."
)


def _reference_pdf():
    data = PaperData(title="Reference Exam", instructions=LONG)
    data.sections = [
        SectionData(
            title="Section A",
            questions=[
                QuestionData(item_type="essay", body=f"First question. {LONG}", marks=Decimal(1)),
                QuestionData(item_type="essay", body=f"Second question. {LONG}", marks=Decimal(2)),
                QuestionData(item_type="essay", body=f"Third question. {LONG}", marks=Decimal(3)),
            ],
        )
    ]
    return build_pdf(data, layout=KNOWN_SPEC, answers=False)


@pytest.fixture(scope="module")
def derived():
    pdf = _reference_pdf()
    m = extract.extract_measurements(pdf)
    labels = labeling.HeuristicLabeler().label(m)
    return spec_module.derive_spec(m, labels), labels


def test_page_size_and_margins_recovered(derived):
    (spec, confidence, _), _ = derived
    assert spec["paper_size"] == "A4"
    # Glyph side-bearings and first-line leading shift measured text boxes by
    # a few millimetres — visually invisible, and the review step exists for
    # exactly this. Left: ±3mm. Top: ±4.5mm (leading overhead above the cap).
    assert abs(spec["margin_left_mm"] - KNOWN_SPEC["margin_left_mm"]) <= 3.0
    assert abs(spec["margin_top_mm"] - KNOWN_SPEC["margin_top_mm"]) <= 4.5
    assert confidence["paper_size"] is True


def test_font_recovered(derived):
    (spec, confidence, _), _ = derived
    assert spec["font_family"] == "sans"
    assert abs(spec["font_size_pt"] - KNOWN_SPEC["font_size_pt"]) <= 0.6
    assert confidence["font_family"] is True
    assert confidence["font_size"] is True


def test_line_spacing_recovered(derived):
    (spec, _, _), _ = derived
    assert abs(spec["line_spacing"] - KNOWN_SPEC["line_spacing"]) <= 0.15


def test_marks_rule_recovered_from_dummy_questions(derived):
    (spec, confidence, _), labels = derived
    # Three questions with distinct marks and proportional answer space.
    assert len(labels.rule_points) >= 2
    assert confidence["answer_rule"] is True
    assert spec["answer_per_mark_mm"] == pytest.approx(
        KNOWN_SPEC["answer_per_mark_mm"], abs=6.0
    )


def test_labeler_finds_questions_and_marks(derived):
    _, labels = derived
    assert labels.question_count >= 3
    assert labels.marks_count >= 3


def test_image_only_pdf_raises_extract_error():
    with pytest.raises(extract.ExtractError):
        extract.extract_measurements(b"%PDF-1.4 garbage no layout")


def test_spec_values_always_clamped():
    wild = {"font_size_pt": 400, "margin_left_mm": -5, "line_spacing": 99, "font_family": "wingdings"}
    clamped = spec_module.clamp_spec(wild)
    assert clamped["font_size_pt"] <= 16
    assert clamped["margin_left_mm"] >= 8
    assert clamped["line_spacing"] <= 2.2
    assert clamped["font_family"] == "serif"


def test_adjustments_are_bounded_and_plain_language():
    spec = spec_module.default_spec()
    for _ in range(50):  # hammer one direction; must stay in bounds
        spec = spec_module.apply_adjustment(spec, "margins", "more")
    assert spec["margin_left_mm"] <= 45
    spec = spec_module.apply_adjustment(spec, "font_family", "more")
    assert spec["font_family"] == "sans"
