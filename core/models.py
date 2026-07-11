import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Manager for the email-login user model (no username field)."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Platform user. Signs in with email; belongs to organizations via Membership."""

    username = None
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class Organization(models.Model):
    """A tenant: an institution/customer. All content rows carry an org FK."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    """Links a user to an organization with a role."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        TEACHER = "teacher", "Teacher"

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.TEACHER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "user"], name="unique_membership_per_org")
        ]

    def __str__(self):
        return f"{self.user} @ {self.org} ({self.role})"

    @property
    def is_org_admin(self):
        return self.role in (self.Role.OWNER, self.Role.ADMIN)


def make_invite_token():
    return secrets.token_urlsafe(32)


class Invitation(models.Model):
    """An invite to join an organization, redeemed via a tokenized link.

    No SMTP is assumed: the accept link is displayed to the admin who
    created it, to be shared out-of-band.
    """

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(
        max_length=16, choices=Membership.Role.choices, default=Membership.Role.TEACHER
    )
    token = models.CharField(max_length=64, unique=True, default=make_invite_token)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invite {self.email} to {self.org}"

    @property
    def is_pending(self):
        return self.accepted_at is None


class Plan(models.Model):
    """A subscription plan.

    ``features`` schema:
        {
          "modules": ["itembank", "formatting", ...],   # enabled module codes
          "limits": {"max_items": 200}                   # absent/None = unlimited
        }
    """

    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    price_month_cents = models.PositiveIntegerField(default=0)
    features = models.JSONField(default=dict)
    is_default = models.BooleanField(
        default=False, help_text="Plan used by orgs with no active subscription."
    )

    class Meta:
        ordering = ["price_month_cents"]

    def __str__(self):
        return self.name

    @property
    def price_display(self):
        if self.price_month_cents == 0:
            return "Free"
        return f"${self.price_month_cents / 100:.0f}/month"


class Subscription(models.Model):
    """An organization's current plan, managed by a billing provider."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TRIALING = "trialing", "Trialing"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"

    org = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    provider = models.CharField(max_length=32, default="manual")
    provider_ref = models.CharField(
        max_length=255, blank=True, help_text="Provider-side id, e.g. a Stripe subscription id."
    )
    current_period_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.org} → {self.plan} ({self.status})"


class TenantOwnedQuerySet(models.QuerySet):
    def for_org(self, org):
        """Scope to one tenant. Views must always go through this."""
        return self.filter(org=org)


class TenantOwnedModel(models.Model):
    """Base for every model that stores tenant content.

    Rows carry an org FK; querysets are scoped with .for_org(org).
    View-layer scoping is enforced by core.access mixins and covered by
    the cross-tenant isolation tests.
    """

    org = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
    )

    objects = TenantOwnedQuerySet.as_manager()

    class Meta:
        abstract = True
