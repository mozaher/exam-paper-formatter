"""Cross-tenant isolation and plan gating — the load-bearing multi-tenant tests."""
import pytest
from django.urls import reverse

from core import billing
from core.models import Plan
from itembank.models import Item
from .factories import make_org, make_user

pytestmark = pytest.mark.django_db


def _make_item(org, title="Q"):
    return Item.objects.create(
        org=org, item_type=Item.TYPE_ESSAY, title=title, body="Body", marks=1
    )


def test_queryset_for_org_scopes_rows():
    org_a = make_org("A", "a")
    org_b = make_org("B", "b")
    _make_item(org_a, "A-item")
    _make_item(org_b, "B-item")

    a_items = Item.objects.for_org(org_a)
    assert [i.title for i in a_items] == ["A-item"]
    assert Item.objects.for_org(org_b).count() == 1


def test_cannot_view_another_orgs_item(client):
    org_a = make_org("A", "a")
    org_b = make_org("B", "b")
    make_user("a@test.com", org_a)
    b_item = _make_item(org_b, "secret")

    client.login(email="a@test.com", password="testpass123")
    resp = client.get(reverse("itembank:item_detail", args=[b_item.pk]))
    assert resp.status_code == 404  # scoped queryset -> not found, not forbidden-with-leak


def test_cannot_edit_another_orgs_item(client):
    org_a = make_org("A", "a")
    org_b = make_org("B", "b")
    make_user("a@test.com", org_a)
    b_item = _make_item(org_b, "secret")

    client.login(email="a@test.com", password="testpass123")
    resp = client.post(
        reverse("itembank:item_edit", args=[b_item.pk]),
        {"title": "hacked", "body": "x", "difficulty": "easy",
         "cognitive_level": "apply", "marks": "1", "status": "draft"},
    )
    assert resp.status_code == 404
    b_item.refresh_from_db()
    assert b_item.title == "secret"


def test_module_gating_blocks_disabled_module(client):
    # Free plan does not include paper_mcq; itembank IS included.
    org = make_org("Free Org", "free-org", plan_code="free")
    make_user("t@test.com", org)
    client.login(email="t@test.com", password="testpass123")

    # itembank allowed
    assert client.get(reverse("itembank:item_list")).status_code == 200
    # module the plan lacks is reported as gated
    assert not billing.has_module(org, "paper_mcq")
    assert billing.has_module(org, "itembank")


def test_item_limit_enforced_on_free_plan(client):
    org = make_org("Free Org", "free-org2", plan_code="free")
    # Shrink the limit to make the test cheap and explicit.
    plan = Plan.objects.get(code="free")
    plan.features = {"modules": ["itembank"], "limits": {"max_items": 1}}
    plan.save()
    make_user("t@test.com", org)
    _make_item(org, "existing")

    client.login(email="t@test.com", password="testpass123")
    resp = client.post(
        reverse("itembank:item_create"),
        {"item_type": "essay", "title": "second", "body": "x", "difficulty": "easy",
         "cognitive_level": "apply", "marks": "1", "status": "draft"},
        follow=True,
    )
    assert Item.objects.for_org(org).count() == 1  # blocked
    assert b"plan allows up to" in resp.content


def test_deleting_org_cascades_including_nested_topics():
    """Tenant offboarding must delete all content, even parent→child topics."""
    from itembank.models import Topic

    org = make_org("Doomed", "doomed")
    parent = Topic.objects.create(org=org, name="Parent")
    Topic.objects.create(org=org, name="Child", parent=parent)
    _make_item(org, "will-vanish")

    org.delete()  # must not raise ProtectedError
    assert Topic.objects.filter(name__in=["Parent", "Child"]).count() == 0
    assert Item.objects.filter(title="will-vanish").count() == 0


def test_default_plan_used_when_no_subscription():
    org = make_org("NoSub", "nosub")
    org.subscription.delete()
    plan = billing.get_active_plan(org)
    assert plan.is_default is True
    assert plan.code == "free"
