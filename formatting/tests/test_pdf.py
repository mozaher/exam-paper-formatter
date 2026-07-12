"""The deterministic PDF builder: content, answers separation, escaping."""
from decimal import Decimal

import pytest
import reportlab.rl_config

from core.tests.factories import make_org
from formatting.models import Paper, PaperQuestion, PaperTemplate, Section
from formatting.pdf import build_paper_pdf
from itembank.models import Choice, Item

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def uncompressed_pdfs():
    """Disable stream compression so tests can assert on PDF text content."""
    old = reportlab.rl_config.pageCompression
    reportlab.rl_config.pageCompression = 0
    yield
    reportlab.rl_config.pageCompression = old


@pytest.fixture
def paper(db):
    org = make_org("PDF Org", "pdf-org")
    template = PaperTemplate.objects.create(
        org=org,
        name="T",
        institution_name="Hogwarts Institute",
        footer_text="Confidential examination",
        default_instructions="Answer everything.",
    )
    paper = Paper.objects.create(
        org=org, template=template, title="Sample Final", course_code="XY-9",
        duration_minutes=120,
    )
    section = Section.objects.create(paper=paper, title="Section A")
    mcq = Item.objects.create(
        org=org, item_type="mcq", title="M", body="Pick the organelle",
        marks=Decimal("1"), status=Item.Status.PUBLISHED,
    )
    Choice.objects.create(item=mcq, text="Mitochondrion", is_correct=True, order=0)
    Choice.objects.create(item=mcq, text="Ribosome", is_correct=False, order=1)
    essay = Item.objects.create(
        org=org, item_type="essay", title="E", body="Discuss cristae in depth",
        model_answer="Award marks for cristae surface area.",
        marks=Decimal("5"), status=Item.Status.PUBLISHED,
    )
    PaperQuestion.objects.create(section=section, item=mcq, order=0)
    PaperQuestion.objects.create(section=section, item=essay, order=1)
    return paper


def test_question_paper_has_content_but_no_answers(paper):
    data = build_paper_pdf(paper, answers=False)
    assert data[:5] == b"%PDF-"
    assert b"Hogwarts" in data          # template slot rendered
    assert b"organelle" in data         # question stem
    assert b"Mitochondrion" in data     # options printed
    assert b"cristae" in data           # essay stem
    assert b"MARKING" not in data       # no scheme labelling
    assert b"surface area" not in data  # model answer must NOT leak to candidates


def test_marking_scheme_includes_answers(paper):
    data = build_paper_pdf(paper, answers=True)
    assert b"MARKING SCHEME" in data
    assert b"surface area" in data      # essay marking guide present
    assert b"Answer:" in data           # MCQ answer key present


def test_user_text_cannot_inject_markup(paper):
    """Angle brackets in question text must be escaped, not parsed as tags."""
    section = paper.sections.first()
    hostile = Item.objects.create(
        org=paper.org, item_type="essay",
        title="H", body="Compare <b>bold</b> & <font size=99>huge</font> claims",
        marks=Decimal("1"), status=Item.Status.PUBLISHED,
    )
    PaperQuestion.objects.create(section=section, item=hostile, order=9)
    data = build_paper_pdf(paper, answers=False)  # must not raise on markup
    assert data[:5] == b"%PDF-"


def test_pdf_without_template_uses_builtin_default(paper):
    paper.template = None
    paper.save()
    data = build_paper_pdf(paper, answers=False)
    assert data[:5] == b"%PDF-"
    assert b"Sample Final" in data


def test_empty_paper_still_renders(db):
    org = make_org("Empty", "empty-org")
    paper = Paper.objects.create(org=org, title="Empty Paper")
    data = build_paper_pdf(paper, answers=False)
    assert data[:5] == b"%PDF-"
    assert b"no questions yet" in data
