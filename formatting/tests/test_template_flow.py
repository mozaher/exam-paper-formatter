"""The template-by-example flow: upload -> review -> adjust -> confirm.

Uses PDF uploads (built with our own renderer) so no external compilers are
needed; the sandbox itself is covered in test_sandbox.py.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from core.tests.factories import make_org, make_user
from formatting import views as formatting_views
from formatting.models import PaperTemplate, TemplateDraft
from formatting.pdf import PaperData, QuestionData, SectionData, build_pdf

pytestmark = pytest.mark.django_db


def _login(client, slug="tpl", email="t@tpl.test"):
    org = make_org(slug.title(), slug)
    make_user(email, org)
    client.login(email=email, password="testpass123")
    return org


def _ambiguous_sample_pdf():
    """A sample with text but no marks indicators -> answer rule is ambiguous."""
    data = PaperData(title="Plain Sample")
    data.sections = [
        SectionData(
            title="Part One",
            questions=[
                QuestionData(
                    item_type="essay",
                    body="A question with no marks shown anywhere in the document, "
                    "written long enough to wrap across several rendered lines "
                    "so spacing itself is measurable.",
                    marks=Decimal(1),
                )
            ],
        )
    ]
    d = dict(marks="")  # noqa: F841  (clarity: no [n marks] text in output)
    pdf_bytes = build_pdf(data, layout=None, answers=True)  # answers mode: no answer lines
    return pdf_bytes


def _upload(client, pdf_bytes, name=""):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        reverse("formatting:template_from_sample"),
        {
            "name": name,
            "file": SimpleUploadedFile("dept-style.pdf", pdf_bytes, "application/pdf"),
        },
    )


def test_ambiguous_upload_goes_to_review_not_straight_to_template(client):
    org = _login(client)
    resp = _upload(client, _ambiguous_sample_pdf(), name="Department style")
    draft = TemplateDraft.objects.for_org(org).get()
    assert resp.status_code == 302
    assert resp.url == reverse("formatting:template_review", args=[draft.pk])
    assert PaperTemplate.objects.for_org(org).count() == 0  # nothing saved yet
    # The draft keeps only the rendered PDF — the upload itself is gone.
    assert bytes(draft.rendered_pdf).startswith(b"%PDF-")
    assert draft.notes  # plain-language ambiguity notes exist


def test_review_page_is_visual_and_plain_language(client):
    org = _login(client)
    _upload(client, _ambiguous_sample_pdf())
    draft = TemplateDraft.objects.for_org(org).get()
    resp = client.get(reverse("formatting:template_review", args=[draft.pk]))
    content = resp.content.decode()
    assert "Does this look right?" in content
    assert "Looks right" in content
    # Both sides of the comparison are embedded.
    assert reverse("formatting:draft_original_pdf", args=[draft.pk]) in content
    assert reverse("formatting:draft_preview_pdf", args=[draft.pk]) in content
    # Raw spec values / JSON are never shown.
    assert "answer_per_mark_mm" not in content
    assert "font_size_pt" not in content


def test_adjust_control_changes_spec_and_rerenders(client):
    org = _login(client)
    _upload(client, _ambiguous_sample_pdf())
    draft = TemplateDraft.objects.for_org(org).get()
    before = draft.spec["margin_left_mm"]
    client.post(
        reverse("formatting:draft_adjust", args=[draft.pk]),
        {"control": "margins", "direction": "more"},
    )
    draft.refresh_from_db()
    assert draft.spec["margin_left_mm"] == pytest.approx(before + 3.0)
    # Preview endpoint renders from the updated spec.
    resp = client.get(reverse("formatting:draft_preview_pdf", args=[draft.pk]))
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")


def test_confirm_saves_spec_and_deletes_draft(client):
    org = _login(client)
    _upload(client, _ambiguous_sample_pdf(), name="Confirmed style")
    draft = TemplateDraft.objects.for_org(org).get()
    expected_margin = draft.spec["margin_left_mm"]
    resp = client.post(reverse("formatting:draft_confirm", args=[draft.pk]))
    assert resp.status_code == 302
    template = PaperTemplate.objects.for_org(org).get()
    assert template.name == "Confirmed style"
    assert float(template.margin_left_mm) == pytest.approx(expected_margin, abs=0.1)
    # Draft (and with it the rendered upload) is gone.
    assert TemplateDraft.objects.count() == 0


def test_cancel_discards_everything(client):
    org = _login(client)
    _upload(client, _ambiguous_sample_pdf())
    draft = TemplateDraft.objects.for_org(org).get()
    client.post(reverse("formatting:draft_cancel", args=[draft.pk]))
    assert TemplateDraft.objects.count() == 0
    assert PaperTemplate.objects.for_org(org).count() == 0


def test_high_confidence_skips_review(client, monkeypatch):
    org = _login(client)

    def confident_derive(measurements, labels):
        from formatting import spec as spec_module

        spec = spec_module.default_spec()
        return spec, {name: True for name in spec_module.CONFIDENCE_FIELDS}, []

    monkeypatch.setattr(formatting_views.spec_module, "derive_spec", confident_derive)
    resp = _upload(client, _ambiguous_sample_pdf(), name="Instant")
    assert resp.status_code == 302
    assert resp.url == reverse("formatting:template_list")
    assert PaperTemplate.objects.for_org(org).filter(name="Instant").exists()
    assert TemplateDraft.objects.count() == 0  # no review needed, no draft kept


def test_drafts_are_tenant_isolated(client):
    org_b = make_org("B", "b-tpl")
    other = make_user("b@tpl.test", org_b)
    foreign = TemplateDraft.objects.create(
        org=org_b, name="theirs", rendered_pdf=b"%PDF-1.4", spec={}, created_by=other
    )
    _login(client)  # org A
    for url_name in (
        "template_review",
        "draft_original_pdf",
        "draft_preview_pdf",
    ):
        assert client.get(
            reverse(f"formatting:{url_name}", args=[foreign.pk])
        ).status_code == 404
    assert client.post(
        reverse("formatting:draft_confirm", args=[foreign.pk])
    ).status_code == 404


def test_saved_template_preview_endpoint(client):
    org = _login(client)
    template = PaperTemplate.objects.create(
        org=org, name="T", institution_name="Preview High"
    )
    resp = client.get(reverse("formatting:template_preview_pdf", args=[template.pk]))
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")


def test_invalid_upload_shows_friendly_error(client):
    _login(client)
    from django.core.files.uploadedfile import SimpleUploadedFile

    resp = client.post(
        reverse("formatting:template_from_sample"),
        {"name": "", "file": SimpleUploadedFile("junk.pdf", b"not a pdf", "application/pdf")},
    )
    assert resp.status_code == 200
    assert b"does not look like a valid PDF" in resp.content
    assert TemplateDraft.objects.count() == 0
