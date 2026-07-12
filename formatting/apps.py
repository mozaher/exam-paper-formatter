from django.apps import AppConfig


class FormattingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "formatting"
    verbose_name = "Exam paper formatting"

    def ready(self):
        from core import modules

        def paper_stats(org):
            from .models import Paper

            n = Paper.objects.for_org(org).count()
            return f"{n} paper{'s' if n != 1 else ''}"

        modules.register(
            modules.Module(
                code="formatting",
                name="Paper formatting",
                description=(
                    "Assemble a professionally formatted exam paper PDF from bank "
                    "questions plus your institutional template, with a separate "
                    "marking-scheme PDF for staff."
                ),
                url_name="formatting:paper_list",
                icon="📄",
                stats=paper_stats,
            )
        )
