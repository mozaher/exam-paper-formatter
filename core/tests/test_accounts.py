"""Signup provisions a tenant; invites join an org."""
import pytest
from django.urls import reverse

from core.models import Invitation, Membership, Organization, User
from .factories import make_org, make_user

pytestmark = pytest.mark.django_db


def test_signup_creates_user_org_and_owner_membership(client):
    resp = client.post(
        reverse("signup"),
        {
            "organization_name": "Springfield High",
            "email": "principal@springfield.test",
            "password1": "s3cretpass99",
            "password2": "s3cretpass99",
        },
        follow=True,
    )
    assert resp.status_code == 200
    user = User.objects.get(email="principal@springfield.test")
    org = Organization.objects.get(name="Springfield High")
    membership = Membership.objects.get(user=user, org=org)
    assert membership.role == Membership.Role.OWNER
    assert org.slug  # slug auto-derived


def test_invite_link_creates_membership_for_new_user(client):
    org = make_org("Org", "org")
    admin = make_user("admin@org.test", org, role=Membership.Role.OWNER)
    invite = Invitation.objects.create(
        org=org, email="newteacher@org.test", role=Membership.Role.TEACHER, invited_by=admin
    )

    url = reverse("invite_accept", args=[invite.token])
    resp = client.post(
        url,
        {"password1": "newpass12345", "password2": "newpass12345"},
        follow=True,
    )
    assert resp.status_code == 200
    user = User.objects.get(email="newteacher@org.test")
    assert Membership.objects.filter(user=user, org=org, role=Membership.Role.TEACHER).exists()
    invite.refresh_from_db()
    assert invite.accepted_at is not None


def test_used_invite_is_rejected(client):
    org = make_org("Org", "org2")
    admin = make_user("admin@org2.test", org)
    invite = Invitation.objects.create(org=org, email="x@org2.test", invited_by=admin)
    client.post(invite_url := reverse("invite_accept", args=[invite.token]),
                {"password1": "newpass12345", "password2": "newpass12345"})
    # Second attempt: token now used
    resp = client.get(invite_url)
    assert resp.status_code == 410


def test_non_admin_cannot_open_org_settings(client):
    org = make_org("Org", "org3")
    make_user("teacher@org3.test", org, role=Membership.Role.TEACHER)
    client.login(email="teacher@org3.test", password="testpass123")
    resp = client.get(reverse("org_settings"))
    assert resp.status_code == 403
