from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Organization
from itembank.qti import importer


class Command(BaseCommand):
    help = "Import a QTI 3.0 package (zip) or single item XML into an organization."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Organization slug")
        parser.add_argument("--file", required=True, help="Path to .zip or .xml")

    def handle(self, *args, **options):
        try:
            org = Organization.objects.get(slug=options["org"])
        except Organization.DoesNotExist:
            slugs = ", ".join(Organization.objects.values_list("slug", flat=True)) or "(none)"
            raise CommandError(f"No organization '{options['org']}'. Known: {slugs}")

        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        result = importer.import_upload(org, None, path.name, path.read_bytes())
        for item in result.created:
            self.stdout.write(f"  created: {item.title} ({item.item_type})")
        for name, reason in result.skipped:
            self.stdout.write(f"  skipped: {name} — {reason}")
        for name, message in result.errors:
            self.stderr.write(f"  error:   {name} — {message}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {len(result.created)} created, "
                f"{len(result.skipped)} skipped, {len(result.errors)} errors."
            )
        )
