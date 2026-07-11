"""QTI 3.0 export → import round-trip: the portability guarantee."""
import zipfile
from decimal import Decimal
from io import BytesIO

import pytest

from core.models import Organization, Plan, Subscription
from itembank.models import Choice, Item, Topic
from itembank.qti import export, importer

pytestmark = pytest.mark.django_db


def _org(slug):
    org = Organization.objects.create(name=slug, slug=slug)
    Subscription.objects.create(
        org=org, plan=Plan.objects.get(code="pro"),
        status=Subscription.Status.ACTIVE, provider="manual",
    )
    return org


def _seed_items(org):
    bio = Topic.objects.create(org=org, name="Biology")
    cells = Topic.objects.create(org=org, name="Cell biology", parent=bio)
    mcq = Item.objects.create(
        org=org, item_type="mcq", status=Item.Status.PUBLISHED,
        title="ATP organelle", body="Which organelle makes ATP?",
        topic=cells, difficulty="easy", cognitive_level="remember", marks=Decimal("1"),
    )
    for i, (t, c) in enumerate([("Mitochondrion", True), ("Ribosome", False), ("Nucleus", False)]):
        Choice.objects.create(item=mcq, text=t, is_correct=c, order=i)
    Item.objects.create(
        org=org, item_type="essay", status=Item.Status.PUBLISHED,
        title="Respiration essay", body="Explain cellular respiration.",
        model_answer="Award marks for cristae, matrix, double membrane.",
        topic=cells, difficulty="hard", cognitive_level="analyze", marks=Decimal("5"),
    )


def test_export_produces_valid_package():
    org = _org("exp")
    _seed_items(org)
    data = export.build_package(Item.objects.for_org(org))
    zf = zipfile.ZipFile(BytesIO(data))
    names = zf.namelist()
    assert "imsmanifest.xml" in names
    assert sum(1 for n in names if n.startswith("items/")) == 2
    manifest = zf.read("imsmanifest.xml").decode()
    assert "imsqti_item_xmlv3p0" in manifest  # QTI 3 resource type
    assert "cognitiveLevel" in manifest       # our metadata rides along


def test_full_roundtrip_preserves_content_and_metadata():
    src = _org("src")
    _seed_items(src)
    package = export.build_package(Item.objects.for_org(src))

    dst = _org("dst")
    result = importer.import_upload(dst, None, "bank.zip", package)
    assert len(result.created) == 2
    assert not result.errors

    mcq = Item.objects.for_org(dst).get(item_type="mcq")
    assert mcq.title == "ATP organelle"
    assert mcq.difficulty == "easy"
    assert mcq.cognitive_level == "remember"
    assert str(mcq.topic) == "Biology › Cell biology"
    correct = [c.text for c in mcq.choices.all() if c.is_correct]
    assert correct == ["Mitochondrion"]
    assert mcq.choices.count() == 3

    essay = Item.objects.for_org(dst).get(item_type="essay")
    assert float(essay.marks) == 5.0
    assert "cristae" in essay.model_answer
    assert essay.cognitive_level == "analyze"


def test_reimport_same_package_skips_duplicates():
    src = _org("src2")
    _seed_items(src)
    package = export.build_package(Item.objects.for_org(src))
    dst = _org("dst2")
    importer.import_upload(dst, None, "bank.zip", package)
    result2 = importer.import_upload(dst, None, "bank.zip", package)
    assert len(result2.created) == 0
    assert len(result2.skipped) == 2
    assert Item.objects.for_org(dst).count() == 2


def test_import_rejects_xxe_entities():
    """Uploaded XML must not expand external entities (billion-laughs / SSRF)."""
    malicious = b"""<?xml version="1.0"?>
    <!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
    <qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0"
        identifier="evil" title="&xxe;">
      <qti-item-body><p>hi</p></qti-item-body>
    </qti-assessment-item>"""
    org = _org("sec")
    result = importer.import_upload(org, None, "evil.xml", malicious)
    # defusedxml raises on the DOCTYPE; importer records it as an error, creates nothing.
    assert len(result.created) == 0
    assert len(result.errors) == 1


def test_import_single_item_xml():
    org = _org("single")
    xml = export.item_to_qti_xml(
        Item.objects.create(
            org=org, item_type="mcq", title="Standalone",
            body="Pick one", difficulty="medium", cognitive_level="apply", marks=2,
        )
    )
    # Give it choices via a fresh item so the XML has them:
    src = Item.objects.for_org(org).get(title="Standalone")
    Choice.objects.create(item=src, text="X", is_correct=True, order=0)
    Choice.objects.create(item=src, text="Y", is_correct=False, order=1)
    xml = export.item_to_qti_xml(src)

    dst = _org("single-dst")
    result = importer.import_upload(dst, None, "one.xml", xml)
    assert len(result.created) == 1
    assert result.created[0].item_type == "mcq"
    assert result.created[0].choices.count() == 2
