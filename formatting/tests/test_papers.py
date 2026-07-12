"""Paper assembly: sections, picking questions, marks, and tenant isolation."""
from decimal import Decimal

import pytest
from django.urls import reverse

from core.tests.factories import make_org, make_user
from formatting.models import Paper, PaperQuestion, Section
from itembank.models import Choice, Item

pytestmark = pytest.mark.django_db


def _item(org, title="Q", item_type="essay", marks="2"):
    item = Item.objects.create(
        org=org, item_type=item_type, title=title, body="Body of " + title,
        marks=Decimal(marks), status=Item.Status.PUBLISHED,
    )
    if item_type == "mcq":
        Choice.objects.create(item=item, text="Right", is_correct=True, order=0)
        Choice.objects.create(item=item, text="Wrong", is_correct=False, order=1)
    return item


def _login(client, org_slug="fmt", email="t@fmt.test"):
    org = make_org(org_slug.title(), org_slug)
    make_user(email, org)
    client.login(email=email, password="testpass123")
    return org


def test_create_paper_makes_default_section(client):
    _login(client)
    resp = client.post(
        reverse("formatting:paper_create"),
        {"title": "Final Exam", "course_code": "BIO-1", "instructions": ""},
    )
    assert resp.status_code == 302
    paper = Paper.objects.get(title="Final Exam")
    assert paper.sections.count() == 1
    assert paper.sections.first().title == "Section A"


def test_add_questions_and_total_marks(client):
    org = _login(client)
    i1 = _item(org, "One", "mcq", "1")
    i2 = _item(org, "Two", "essay", "5")
    paper = Paper.objects.create(org=org, title="P")
    section = Section.objects.create(paper=paper, title="Section A")

    resp = client.post(
        reverse("formatting:question_picker", args=[section.pk]),
        {"item_ids": [i1.pk, i2.pk]},
    )
    assert resp.status_code == 302
    assert section.questions.count() == 2
    assert paper.total_marks == Decimal("6")

    # Marks override affects the total; clearing it restores the item's marks.
    pq = section.questions.get(item=i2)
    client.post(reverse("formatting:pq_marks", args=[pq.pk]), {"marks_override": "10"})
    assert paper.total_marks == Decimal("11")
    client.post(reverse("formatting:pq_marks", args=[pq.pk]), {"marks_override": ""})
    assert paper.total_marks == Decimal("6")


def test_same_item_not_added_twice(client):
    org = _login(client)
    item = _item(org)
    paper = Paper.objects.create(org=org, title="P")
    s1 = Section.objects.create(paper=paper, title="A", order=0)
    s2 = Section.objects.create(paper=paper, title="B", order=1)
    client.post(reverse("formatting:question_picker", args=[s1.pk]), {"item_ids": [item.pk]})
    # Same item into another section of the same paper: silently skipped.
    client.post(reverse("formatting:question_picker", args=[s2.pk]), {"item_ids": [item.pk]})
    assert PaperQuestion.objects.filter(section__paper=paper).count() == 1


def test_question_reorder(client):
    org = _login(client)
    paper = Paper.objects.create(org=org, title="P")
    section = Section.objects.create(paper=paper, title="A")
    pq1 = PaperQuestion.objects.create(section=section, item=_item(org, "First"), order=0)
    pq2 = PaperQuestion.objects.create(section=section, item=_item(org, "Second"), order=1)

    client.post(reverse("formatting:pq_move", args=[pq2.pk]), {"direction": "up"})
    titles = [pq.item.title for pq in section.questions.all()]
    assert titles == ["Second", "First"]


def test_cannot_see_or_touch_another_orgs_paper(client):
    org_b = make_org("B", "b-fmt")
    foreign_paper = Paper.objects.create(org=org_b, title="Foreign")
    foreign_section = Section.objects.create(paper=foreign_paper, title="A")
    foreign_pq = PaperQuestion.objects.create(
        section=foreign_section, item=_item(org_b, "Theirs")
    )

    _login(client)  # org A
    assert client.get(
        reverse("formatting:paper_detail", args=[foreign_paper.pk])
    ).status_code == 404
    assert client.get(
        reverse("formatting:paper_pdf", args=[foreign_paper.pk])
    ).status_code == 404
    assert client.post(
        reverse("formatting:pq_remove", args=[foreign_pq.pk])
    ).status_code == 404
    assert PaperQuestion.objects.filter(pk=foreign_pq.pk).exists()


def test_cannot_add_another_orgs_item_to_own_paper(client):
    org_b = make_org("B", "b-fmt2")
    foreign_item = _item(org_b, "Secret question")

    org_a = _login(client)
    paper = Paper.objects.create(org=org_a, title="Mine")
    section = Section.objects.create(paper=paper, title="A")
    client.post(
        reverse("formatting:question_picker", args=[section.pk]),
        {"item_ids": [foreign_item.pk]},
    )
    assert section.questions.count() == 0  # silently dropped
