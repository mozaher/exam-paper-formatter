from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Membership, Organization, Plan, Subscription, User
from itembank.models import Choice, Item, Topic


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


class Command(BaseCommand):
    help = "Seed plans and a demo organization with sample questions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the demo org first, then recreate it.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        for data in PLANS:
            Plan.objects.update_or_create(code=data["code"], defaults=data)
        self.stdout.write(self.style.SUCCESS(f"Ensured {len(PLANS)} plans."))

        if options["reset"]:
            Organization.objects.filter(slug="demo-high-school").delete()
            User.objects.filter(email="teacher@demo.test").delete()

        org, created = Organization.objects.get_or_create(
            slug="demo-high-school", defaults={"name": "Demo High School"}
        )
        if not created and not options["reset"]:
            self.stdout.write("Demo org already exists; use --reset to rebuild it.")
            return

        Subscription.objects.update_or_create(
            org=org,
            defaults={
                "plan": Plan.objects.get(code="pro"),
                "status": Subscription.Status.ACTIVE,
                "provider": "manual",
            },
        )

        user, _ = User.objects.get_or_create(
            email="teacher@demo.test",
            defaults={"first_name": "Demo", "last_name": "Teacher"},
        )
        user.set_password("demopass123")
        user.save()
        Membership.objects.get_or_create(
            org=org, user=user, defaults={"role": Membership.Role.OWNER}
        )

        bio = Topic.objects.create(org=org, name="Biology")
        cells = Topic.objects.create(org=org, name="Cell biology", parent=bio)
        math = Topic.objects.create(org=org, name="Mathematics")

        mcq = Item.objects.create(
            org=org,
            item_type=Item.TYPE_MCQ,
            status=Item.Status.PUBLISHED,
            title="Powerhouse of the cell",
            body="Which organelle is primarily responsible for producing ATP in "
            "eukaryotic cells?",
            topic=cells,
            difficulty=Item.Difficulty.EASY,
            cognitive_level=Item.CognitiveLevel.REMEMBER,
            marks=Decimal("1"),
            created_by=user,
        )
        for i, (text, correct) in enumerate(
            [
                ("Mitochondrion", True),
                ("Ribosome", False),
                ("Golgi apparatus", False),
                ("Nucleus", False),
            ]
        ):
            Choice.objects.create(item=mcq, text=text, is_correct=correct, order=i)

        mcq2 = Item.objects.create(
            org=org,
            item_type=Item.TYPE_MCQ,
            status=Item.Status.PUBLISHED,
            title="Derivative of sine",
            body="What is the derivative of sin(x) with respect to x?",
            topic=math,
            difficulty=Item.Difficulty.MEDIUM,
            cognitive_level=Item.CognitiveLevel.APPLY,
            marks=Decimal("2"),
            created_by=user,
        )
        for i, (text, correct) in enumerate(
            [("cos(x)", True), ("-cos(x)", False), ("-sin(x)", False), ("tan(x)", False)]
        ):
            Choice.objects.create(item=mcq2, text=text, is_correct=correct, order=i)

        Item.objects.create(
            org=org,
            item_type=Item.TYPE_ESSAY,
            status=Item.Status.PUBLISHED,
            title="Cellular respiration overview",
            body="Explain how the structure of the mitochondrion supports its role "
            "in cellular respiration. Refer to at least two structural features.",
            model_answer="Award up to 5 marks: inner-membrane cristae increase surface "
            "area for the electron transport chain (2); matrix houses Krebs-cycle "
            "enzymes (2); double membrane compartmentalizes the proton gradient (1).",
            topic=cells,
            difficulty=Item.Difficulty.HARD,
            cognitive_level=Item.CognitiveLevel.ANALYZE,
            marks=Decimal("5"),
            created_by=user,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded demo org 'Demo High School'.\n"
                "  Login: teacher@demo.test / demopass123\n"
                "  Plan:  Pro (item bank + formatting + paper MCQ + online exam)\n"
                "  Items: 2 MCQ + 1 essay across Biology and Mathematics topics"
            )
        )
