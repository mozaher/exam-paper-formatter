"""Plan resolution and feature gating.

An organization's effective plan is its active subscription's plan, or the
default (free) plan when it has no active subscription. Feature access is
always resolved through these helpers so a future billing provider only has
to keep Subscription rows accurate.
"""
from django.core.exceptions import ImproperlyConfigured

from .models import Organization, Plan, Subscription

_ACTIVE_STATUSES = (Subscription.Status.ACTIVE, Subscription.Status.TRIALING)


def get_default_plan() -> Plan:
    plan = (
        Plan.objects.filter(is_default=True).first()
        or Plan.objects.filter(code="free").first()
    )
    if plan is None:
        raise ImproperlyConfigured(
            "No default plan exists. Run migrations (they seed the plans)."
        )
    return plan


def get_active_plan(org: Organization) -> Plan:
    sub = Subscription.objects.filter(org=org).select_related("plan").first()
    if sub is not None and sub.status in _ACTIVE_STATUSES:
        return sub.plan
    return get_default_plan()


def enabled_modules(org: Organization) -> frozenset:
    return frozenset(get_active_plan(org).features.get("modules") or [])


def has_module(org: Organization, module_code: str) -> bool:
    return module_code in enabled_modules(org)


def get_limit(org: Organization, limit_name: str):
    """Return a numeric limit for the org's plan, or None for unlimited."""
    return (get_active_plan(org).features.get("limits") or {}).get(limit_name)


class BillingProvider:
    """Interface every billing backend implements.

    The manual provider just writes Subscription rows. A Stripe provider
    would create a Checkout session in change_plan() and keep Subscription
    rows in sync from webhooks — callers don't change.
    """

    code = "base"

    def change_plan(self, org: Organization, plan: Plan) -> Subscription:
        raise NotImplementedError


class ManualBillingProvider(BillingProvider):
    """Dev/demo provider: plan changes take effect immediately, no payment."""

    code = "manual"

    def change_plan(self, org: Organization, plan: Plan) -> Subscription:
        sub, _ = Subscription.objects.update_or_create(
            org=org,
            defaults={
                "plan": plan,
                "status": Subscription.Status.ACTIVE,
                "provider": self.code,
            },
        )
        return sub


class StripeBillingProvider(BillingProvider):
    """Placeholder for real payments.

    Integration point: implement change_plan() to create a Stripe Checkout
    session, and a webhook view to update Subscription on
    customer.subscription.* events. Not wired up — no API keys in this
    environment.
    """

    code = "stripe"

    def change_plan(self, org: Organization, plan: Plan) -> Subscription:
        raise NotImplementedError(
            "Stripe is not configured. Set up API keys and implement the "
            "checkout + webhook flow described in docs/architecture.md."
        )


def get_provider() -> BillingProvider:
    # Future: select by settings/env once a real provider exists.
    return ManualBillingProvider()
