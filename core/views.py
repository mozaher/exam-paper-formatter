from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, TemplateView

from . import billing, modules
from .access import OrgAdminRequiredMixin, OrgRequiredMixin
from .forms import AcceptInviteForm, InviteForm, OrgSettingsForm, SignupForm
from .models import Invitation, Membership, Organization, Plan, User
from .utils import unique_org_slug


class SignupView(FormView):
    """Creates the user, their organization (tenant), and an owner membership."""

    template_name = "core/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        data = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                email=data["email"],
                password=data["password1"],
                first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""),
            )
            org = Organization.objects.create(
                name=data["organization_name"],
                slug=unique_org_slug(data["organization_name"]),
            )
            Membership.objects.create(org=org, user=user, role=Membership.Role.OWNER)
        login(self.request, user)
        messages.success(
            self.request,
            f"Welcome! Your organization “{org.name}” is ready on the free plan.",
        )
        return super().form_valid(form)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        org = self.request.org
        cards = []
        if org is not None:
            enabled = billing.enabled_modules(org)
            for module in modules.all_modules():
                stat = None
                if module.stats is not None:
                    stat = module.stats(org)
                cards.append(
                    {
                        "module": module,
                        "enabled": module.code in enabled,
                        "stat": stat,
                    }
                )
        ctx["module_cards"] = cards
        ctx["planned_modules"] = modules.PLANNED_MODULES
        return ctx


class OrgSettingsView(OrgAdminRequiredMixin, View):
    template_name = "core/org_settings.html"

    def get(self, request):
        return self._render(request, OrgSettingsForm(instance=request.org), InviteForm(org=request.org))

    def post(self, request):
        form = OrgSettingsForm(request.POST, instance=request.org)
        if form.is_valid():
            form.save()
            messages.success(request, "Organization updated.")
            return redirect("org_settings")
        return self._render(request, form, InviteForm(org=request.org))

    def _render(self, request, form, invite_form):
        memberships = request.org.memberships.select_related("user").order_by("created_at")
        invitations = request.org.invitations.filter(accepted_at__isnull=True)
        invite_links = [
            (inv, request.build_absolute_uri(reverse("invite_accept", args=[inv.token])))
            for inv in invitations
        ]
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "invite_form": invite_form,
                "memberships": memberships,
                "invite_links": invite_links,
            },
        )


class InviteCreateView(OrgAdminRequiredMixin, View):
    def post(self, request):
        form = InviteForm(request.POST, org=request.org)
        if form.is_valid():
            invite = form.save(commit=False)
            invite.org = request.org
            invite.invited_by = request.user
            invite.save()
            link = request.build_absolute_uri(reverse("invite_accept", args=[invite.token]))
            messages.success(
                request,
                f"Invite created for {invite.email}. Share this link with them: {link}",
            )
        else:
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, e)
        return redirect("org_settings")


class MemberRemoveView(OrgAdminRequiredMixin, View):
    def post(self, request, pk):
        membership = get_object_or_404(Membership, pk=pk, org=request.org)
        if membership.user_id == request.user.id:
            messages.error(request, "You cannot remove yourself.")
        elif (
            membership.role == Membership.Role.OWNER
            and request.org.memberships.filter(role=Membership.Role.OWNER).count() <= 1
        ):
            messages.error(request, "An organization must keep at least one owner.")
        else:
            membership.delete()
            messages.success(request, f"Removed {membership.user.email}.")
        return redirect("org_settings")


class InviteAcceptView(View):
    """Redeems an invite link: joins an existing account or creates a new one."""

    template_name = "core/invite_accept.html"

    def dispatch(self, request, *args, **kwargs):
        self.invitation = Invitation.objects.filter(
            token=kwargs["token"], accepted_at__isnull=True
        ).select_related("org").first()
        if self.invitation is None:
            return render(request, "core/invite_invalid.html", status=410)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, token):
        inv = self.invitation
        if request.user.is_authenticated:
            if request.user.email.lower() == inv.email.lower():
                self._join(request.user)
                messages.success(request, f"You have joined {inv.org.name}.")
                return redirect("dashboard")
            messages.error(
                request,
                f"This invite is for {inv.email}, but you are signed in as "
                f"{request.user.email}. Sign out first.",
            )
            return redirect("dashboard")
        if User.objects.filter(email__iexact=inv.email).exists():
            messages.info(request, "Sign in to accept the invitation, then open the link again.")
            return redirect(f"{reverse('login')}?next={request.path}")
        return render(request, self.template_name, {"invitation": inv, "form": AcceptInviteForm()})

    def post(self, request, token):
        inv = self.invitation
        form = AcceptInviteForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"invitation": inv, "form": form})
        with transaction.atomic():
            user = User.objects.create_user(
                email=inv.email,
                password=form.cleaned_data["password1"],
                first_name=form.cleaned_data.get("first_name", ""),
                last_name=form.cleaned_data.get("last_name", ""),
            )
            self._join(user)
        login(request, user)
        messages.success(request, f"Welcome to {inv.org.name}!")
        return redirect("dashboard")

    def _join(self, user):
        Membership.objects.get_or_create(
            org=self.invitation.org, user=user, defaults={"role": self.invitation.role}
        )
        self.invitation.accepted_at = timezone.now()
        self.invitation.save(update_fields=["accepted_at"])


class BillingView(OrgRequiredMixin, View):
    template_name = "core/billing.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "plans": Plan.objects.all(),
                "active_plan": billing.get_active_plan(request.org),
                "self_serve": settings.BILLING_SELF_SERVE,
            },
        )

    def post(self, request):
        if not settings.BILLING_SELF_SERVE:
            messages.error(request, "Self-serve plan changes are disabled.")
            return redirect("billing")
        if not request.membership.is_org_admin:
            return render(request, "core/forbidden.html", status=403)
        plan = get_object_or_404(Plan, code=request.POST.get("plan_code", ""))
        billing.get_provider().change_plan(request.org, plan)
        messages.success(request, f"Your organization is now on the {plan.name} plan.")
        return redirect("billing")
