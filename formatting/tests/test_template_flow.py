"""The template-by-example flow (in-place injection model).

Rendering is monkeypatched to avoid needing LibreOffice in view tests; the
real converters are covered in test_sandbox.py / test_inject_tex.py.
"""
import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.tests.factories import make_org, make_user
from formatting import views as formatting_views
from formatting.models import Paper, PaperTemplate, Section, TemplateDraft
from .docx_factory import build_docx, para, standard_exam_docx

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_pdf_rendering(monkeypatch):
    """Injection stays real; only PDF conversion is faked for speed."""
    monkeypatch.setattr(
        formatting_views.ingest, "render_pdf", lambda kind, doc: b"%PDF-fake " + kind.encode()
    )


def _login(client, slug="inj", email="t@inj.test"):
    org = make_org(slug.title(), slug)
    make_user(email, org)
    client.login(email=email, password="testpass123")
    return org


def _upload(client, data=None, name="Dept template", filename="dept.docx"):
    return client.post(
        reverse("formatting:template_from_sample"),
        {
            "name": name,
            "file": SimpleUploadedFile(
                filename, data or standard_exam_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        },
    )


def test_clear_upload_goes_straight_to_review(client):
    org = _login(client)
    resp = _upload(client)
    draft = TemplateDraft.objects.for_org(org).get()
    assert resp.status_code == 302
    assert resp.url == reverse("formatting:template_review", args=[draft.pk])
    assert not draft.detection["ambiguous"]
    assert bytes(draft.rendered_pdf).startswith(b"%PDF-")   # preview cached
    # The stored source is the SANITIZED file, still a valid docx zip.
    assert zipfile.is_zipfile(io.BytesIO(bytes(draft.source_file)))


def test_ambiguous_upload_asks_for_region(client):
    org = _login(client)
    doc = build_docx(para("Cover text") + para("1. Single question?") + para(""))
    resp = _upload(client, data=doc)
    draft = TemplateDraft.objects.for_org(org).get()
    assert resp.url == reverse("formatting:template_region", args=[draft.pk])

    # The region page lists document lines to point at, not raw data.
    page = client.get(resp.url)
    assert b"Where are the dummy questions?" in page.content
    assert b"Single question?" in page.content

    # Staff point out the area -> preview -> review.
    resp2 = client.post(resp.url, {"start": 1, "end": 2})
    draft.refresh_from_db()
    assert resp2.url == reverse("formatting:template_review", args=[draft.pk])
    assert draft.detection["start"] == 1 and draft.detection["end"] == 2
    assert not draft.detection["ambiguous"]


def test_confirm_stores_source_template_and_deletes_draft(client):
    org = _login(client)
    _upload(client)
    draft = TemplateDraft.objects.for_org(org).get()
    client.post(reverse("formatting:draft_confirm", args=[draft.pk]))

    template = PaperTemplate.objects.for_org(org).get()
    assert template.source_kind == "docx"
    assert template.source_detection["start"] >= 0
    assert bytes(template.source_file)  # sanitized source stored
    assert TemplateDraft.objects.count() == 0


def test_cancel_discards_draft(client):
    org = _login(client)
    _upload(client)
    draft = TemplateDraft.objects.for_org(org).get()
    client.post(reverse("formatting:draft_cancel", args=[draft.pk]))
    assert TemplateDraft.objects.count() == 0
    assert PaperTemplate.objects.for_org(org).count() == 0


def test_paper_pdf_generated_inside_source_template(client):
    org = _login(client)
    _upload(client)
    draft = TemplateDraft.objects.for_org(org).get()
    client.post(reverse("formatting:draft_confirm", args=[draft.pk]))
    template = PaperTemplate.objects.for_org(org).get()

    paper = Paper.objects.create(org=org, title="Real Paper", template=template)
    Section.objects.create(paper=paper, title="Section A")
    resp = client.get(reverse("formatting:paper_pdf", args=[paper.pk]))
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-fake docx")  # injected route used

    # Marking scheme always uses the built-in renderer (real PDF bytes).
    resp = client.get(reverse("formatting:paper_marking_pdf", args=[paper.pk]))
    assert resp.content.startswith(b"%PDF-1")


def test_paper_docx_download(client):
    org = _login(client)
    _upload(client)
    draft = TemplateDraft.objects.for_org(org).get()
    client.post(reverse("formatting:draft_confirm", args=[draft.pk]))
    template = PaperTemplate.objects.for_org(org).get()
    paper = Paper.objects.create(org=org, title="P", template=template)
    Section.objects.create(paper=paper, title="A")

    resp = client.get(reverse("formatting:paper_docx", args=[paper.pk]))
    assert resp.status_code == 200
    assert "wordprocessingml" in resp["Content-Type"]
    assert zipfile.is_zipfile(io.BytesIO(resp.content))  # real injected docx


def test_drafts_are_tenant_isolated(client):
    org_b = make_org("B", "b-inj")
    other = make_user("b@inj.test", org_b)
    foreign = TemplateDraft.objects.create(
        org=org_b, name="theirs", source_kind="docx",
        source_file=standard_exam_docx(), detection={}, rendered_pdf=b"%PDF-x",
        created_by=other,
    )
    _login(client)
    for url_name in ("template_review", "template_region", "draft_preview_pdf"):
        assert client.get(
            reverse(f"formatting:{url_name}", args=[foreign.pk])
        ).status_code == 404
    assert client.post(
        reverse("formatting:draft_confirm", args=[foreign.pk])
    ).status_code == 404


def test_invalid_docx_shows_friendly_error(client):
    _login(client)
    resp = _upload(client, data=b"MZ not a docx at all")
    assert resp.status_code == 200
    assert b"Not a valid .docx" in resp.content
    assert TemplateDraft.objects.count() == 0


def test_macros_are_stripped_on_ingest(client):
    org = _login(client)
    body = (
        para("Header")
        + para("1. Q one?", numpr=True) + para("A) x") + para("B) y")
        + para("2. Q two?", numpr=True) + para("A) x") + para("B) y")
    )
    doc = build_docx(body, extra_entries={"word/vbaProject.bin": b"MACRO"})
    _upload(client, data=doc)
    draft = TemplateDraft.objects.for_org(org).get()
    names = zipfile.ZipFile(io.BytesIO(bytes(draft.source_file))).namelist()
    assert "word/vbaProject.bin" not in names
