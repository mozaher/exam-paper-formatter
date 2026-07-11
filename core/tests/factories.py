"""Small helpers to build orgs/users/subscriptions in tests."""
from core.models import Membership, Organization, Plan, Subscription, User


def make_org(name, slug, plan_code="pro"):
    org = Organization.objects.create(name=name, slug=slug)
    Subscription.objects.create(
        org=org,
        plan=Plan.objects.get(code=plan_code),
        status=Subscription.Status.ACTIVE,
        provider="manual",
    )
    return org


def make_user(email, org=None, role=Membership.Role.OWNER, password="testpass123"):
    user = User.objects.create_user(email=email, password=password)
    if org is not None:
        Membership.objects.create(org=org, user=user, role=role)
    return user
