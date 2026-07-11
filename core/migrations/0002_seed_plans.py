from django.db import migrations

PLANS = [
    {
        "code": "free",
        "name": "Free",
        "price_month_cents": 0,
        "is_default": True,
        "features": {"modules": ["itembank", "formatting"], "limits": {"max_items": 100}},
    },
    {
        "code": "pro",
        "name": "Pro",
        "price_month_cents": 2900,
        "is_default": False,
        "features": {
            "modules": ["itembank", "formatting", "paper_mcq", "online_exam"],
            "limits": {"max_items": None},
        },
    },
    {
        "code": "institution",
        "name": "Institution",
        "price_month_cents": 9900,
        "is_default": False,
        "features": {
            "modules": ["itembank", "formatting", "paper_mcq", "online_exam", "blueprint"],
            "limits": {"max_items": None},
        },
    },
]


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("core", "Plan")
    for data in PLANS:
        Plan.objects.update_or_create(code=data["code"], defaults=data)


def unseed_plans(apps, schema_editor):
    Plan = apps.get_model("core", "Plan")
    Plan.objects.filter(code__in=[p["code"] for p in PLANS]).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(seed_plans, unseed_plans)]
