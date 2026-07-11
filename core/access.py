"""View mixins that enforce tenancy and plan gating."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render

from . import billing


class OrgRequiredMixin(LoginRequiredMixin):
    """Requires a signed-in user with an organization membership."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.org is None:
            messages.error(
                request,
                "Your account is not a member of any organization. "
                "Sign up to create one, or ask an admin for an invite.",
            )
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)


class OrgAdminRequiredMixin(OrgRequiredMixin):
    """Requires an owner/admin role in the current organization."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.org is not None:
            if not request.membership.is_org_admin:
                return render(request, "core/forbidden.html", status=403)
        return super().dispatch(request, *args, **kwargs)


class ModuleRequiredMixin(OrgRequiredMixin):
    """Gates a view behind a module code in the org's plan."""

    required_module: str = ""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.org is not None:
            if self.required_module and not billing.has_module(
                request.org, self.required_module
            ):
                return render(
                    request,
                    "core/upgrade_required.html",
                    {"module_code": self.required_module},
                    status=403,
                )
        return super().dispatch(request, *args, **kwargs)
