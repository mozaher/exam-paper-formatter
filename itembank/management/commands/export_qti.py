from django.core.management.base import BaseCommand, CommandError

from core.models import Organization
from itembank.models import Item
from itembank.qti import export


class Command(BaseCommand):
    help = "Export an organization's item bank as a QTI 3.0 content package (zip)."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Organization slug")
        parser.add_argument("--out", required=True, help="Output .zip path")

    def handle(self, *args, **options):
        try:
            org = Organization.objects.get(slug=options["org"])
        except Organization.DoesNotExist:
            slugs = ", ".join(Organization.objects.values_list("slug", flat=True)) or "(none)"
            raise CommandError(f"No organization '{options['org']}'. Known: {slugs}")

        items = (
            Item.objects.for_org(org)
            .exclude(status=Item.Status.ARCHIVED)
            .prefetch_related("choices")
        )
        data = export.build_package(items)
        with open(options["out"], "wb") as fh:
            fh.write(data)
        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {items.count()} items from '{org.name}' to {options['out']}"
            )
        )
