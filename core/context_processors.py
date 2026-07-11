from . import billing, modules


def platform_context(request):
    org = getattr(request, "org", None)
    ctx = {
        "current_org": org,
        "current_membership": getattr(request, "membership", None),
        "current_plan": None,
        "nav_modules": [],
    }
    if org is not None:
        plan = billing.get_active_plan(org)
        enabled = set(plan.features.get("modules") or [])
        ctx["current_plan"] = plan
        ctx["nav_modules"] = [
            {"module": m, "enabled": m.code in enabled} for m in modules.all_modules()
        ]
    return ctx
