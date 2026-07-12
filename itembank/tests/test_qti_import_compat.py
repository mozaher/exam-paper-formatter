"""Compatibility with QTI 2.x-style files and real-world authoring patterns.

Regression tests for user-reported import bugs:
- scorer rubric placed OUTSIDE <itemBody> (sibling of it) must still become
  the marking guide, including <ul>/<li> content;
- marks declared as a child element <normalMaximum>5.0</normalMaximum>
  (not just the QTI 3 normal-maximum attribute);
- topic/difficulty embedded as IEEE LOM metadata in the item XML;
- archived items must not block re-import of the same question.
"""
import pytest

from core.models import Organization, Plan, Subscription
from itembank.models import Item
from itembank.qti import importer

pytestmark = pytest.mark.django_db

USER_STYLE_ESSAY = b"""<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
    identifier="essay-astronomy-01" title="Describe the life cycle of a star"
    adaptive="false" timeDependent="false">
    <metadata>
        <lom xmlns="http://ltsc.ieee.org/xsd/LOM">
            <general>
                <keyword><string language="en">Astronomy</string></keyword>
            </general>
            <educational>
                <difficulty><value>easy</value></difficulty>
            </educational>
        </lom>
    </metadata>
    <outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">
        <normalMaximum>5.0</normalMaximum>
        <defaultValue><value>0.0</value></defaultValue>
    </outcomeDeclaration>
    <responseDeclaration identifier="RESPONSE" cardinality="single" baseType="string"/>
    <rubricBlock view="scorer">
        <p>Grading Criteria:</p>
        <ul>
            <li>Clear thesis statement and logical structure.</li>
        </ul>
    </rubricBlock>
    <itemBody>
        <p>Describe the main stages in the life cycle of a sun-like star.</p>
        <extendedTextInteraction responseIdentifier="RESPONSE"/>
    </itemBody>
</assessmentItem>"""


def _org(slug):
    org = Organization.objects.create(name=slug, slug=slug)
    Subscription.objects.create(
        org=org, plan=Plan.objects.get(code="pro"),
        status=Subscription.Status.ACTIVE, provider="manual",
    )
    return org


def test_parse_user_style_essay_gets_rubric_marks_and_lom_tags():
    parsed = importer.parse_item_xml(USER_STYLE_ESSAY)
    assert parsed.item_type == "essay"
    assert "Grading Criteria" in parsed.model_answer
    assert "Clear thesis statement" in parsed.model_answer  # <li> content kept
    assert parsed.marks == "5.0"          # child-element normalMaximum
    assert parsed.topic == "Astronomy"    # LOM general/keyword
    assert parsed.difficulty == "easy"    # LOM educational/difficulty
    assert "life cycle of a sun-like star" in parsed.body
    assert "Grading Criteria" not in parsed.body  # rubric not leaked into stem


def test_import_user_style_essay_creates_tagged_item():
    org = _org("compat")
    result = importer.import_upload(org, None, "essay.xml", USER_STYLE_ESSAY)
    assert not result.errors
    item = result.created[0]
    assert float(item.marks) == 5.0
    assert item.difficulty == "easy"
    assert str(item.topic) == "Astronomy"
    assert "Clear thesis statement" in item.model_answer


def test_rubric_inside_item_body_still_works():
    xml = USER_STYLE_ESSAY.replace(
        b"<itemBody>",
        b"<itemBody><rubricBlock view=\"scorer\"><p>Inner rubric.</p></rubricBlock>",
    ).replace(
        b"""<rubricBlock view="scorer">
        <p>Grading Criteria:</p>
        <ul>
            <li>Clear thesis statement and logical structure.</li>
        </ul>
    </rubricBlock>""",
        b"",
    )
    parsed = importer.parse_item_xml(xml)
    assert parsed.model_answer == "Inner rubric."
    assert "Inner rubric" not in parsed.body


def test_archived_item_does_not_block_reimport():
    org = _org("rearchive")
    first = importer.import_upload(org, None, "essay.xml", USER_STYLE_ESSAY)
    assert len(first.created) == 1

    # Active copy present -> duplicate is skipped.
    second = importer.import_upload(org, None, "essay.xml", USER_STYLE_ESSAY)
    assert len(second.created) == 0
    assert len(second.skipped) == 1

    # Archive it -> re-import creates a fresh copy.
    item = first.created[0]
    item.status = Item.Status.ARCHIVED
    item.save(update_fields=["status"])
    third = importer.import_upload(org, None, "essay.xml", USER_STYLE_ESSAY)
    assert len(third.created) == 1
    assert len(third.skipped) == 0
    active = Item.objects.for_org(org).exclude(status=Item.Status.ARCHIVED)
    assert active.count() == 1
