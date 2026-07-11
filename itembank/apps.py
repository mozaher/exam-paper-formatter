from django.apps import AppConfig


class ItembankConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "itembank"
    verbose_name = "Item bank"

    def ready(self):
        from core import modules

        def item_stats(org):
            from .models import Item

            n = Item.objects.for_org(org).exclude(status=Item.Status.ARCHIVED).count()
            return f"{n} question{'s' if n != 1 else ''}"

        modules.register(
            modules.Module(
                code="itembank",
                name="Item bank",
                description=(
                    "Author and tag questions (MCQ, essay) by topic, difficulty, "
                    "cognitive level and marks. Import/export as QTI 3.0."
                ),
                url_name="itembank:item_list",
                icon="🗂",
                stats=item_stats,
            )
        )
