"""Sample-paper preview: dummy content rendered with a formatting spec.

Used for the side-by-side review (derived spec vs. the uploaded original)
and for previewing saved templates. Content is fixed dummy questions with a
spread of marks values so the answer-space rule is visible.
"""
from decimal import Decimal

from .pdf import PaperData, QuestionData, SectionData, build_pdf


def sample_data(institution_name="", subtitle="", footer_text="") -> PaperData:
    data = PaperData(
        title="Sample Examination Paper",
        course_code="SAMPLE-101",
        duration_text="2 hours",
        instructions=(
            "Answer ALL questions.\n\n"
            "This is a preview generated from your formatting settings — the "
            "questions below are dummy content."
        ),
        institution_name=institution_name or "Your Institution Name",
        subtitle=subtitle,
        footer_text=footer_text,
    )
    section_a = SectionData(
        title="Section A — Multiple choice",
        instructions="Choose the single best answer for each question.",
        questions=[
            QuestionData(
                item_type="mcq",
                body="Which of the following best demonstrates the option layout?",
                marks=Decimal("1"),
                choices=[
                    ("The first option", True),
                    ("The second option", False),
                    ("The third option", False),
                    ("The fourth option", False),
                ],
            ),
            QuestionData(
                item_type="mcq",
                body="A second multiple-choice question, to show question spacing.",
                marks=Decimal("2"),
                choices=[("Alpha", False), ("Beta", True), ("Gamma", False)],
            ),
        ],
    )
    section_b = SectionData(
        title="Section B — Structured questions",
        questions=[
            QuestionData(
                item_type="essay",
                body="Explain a concept briefly. This two-mark question shows the "
                "answer space for a small question.",
                marks=Decimal("2"),
            ),
            QuestionData(
                item_type="essay",
                body="Discuss a topic in depth, giving examples. This five-mark "
                "question shows how answer space grows with marks.",
                marks=Decimal("5"),
            ),
        ],
    )
    data.sections = [section_a, section_b]
    return data


def build_sample_pdf(layout: dict, *, institution_name="", subtitle="", footer_text="") -> bytes:
    layout = layout or {}
    data = sample_data(institution_name, subtitle, footer_text)
    # Cover slots proposed by extraction ride in the spec; show them so the
    # side-by-side reflects what would actually be saved.
    data.cover_heading = layout.get("cover_heading", "")
    data.address_text = layout.get("address_text", "")
    data.candidate_fields = list(layout.get("candidate_fields") or [])
    return build_pdf(data, layout=layout, answers=False)
