from django.conf import settings
from django.db import models
from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce

from core.models import TenantOwnedModel
from itembank.models import Item


class PaperTemplate(TenantOwnedModel):
    """Institutional look-and-feel as a FIXED SET OF NAMED SLOTS.

    This is a hard constraint: templates are data filling known slots that a
    deterministic builder renders — never uploaded LaTeX/Word files, and never
    anything executable.
    """

    FONT_CHOICES = [("serif", "Serif (Times)"), ("sans", "Sans-serif (Helvetica)")]
    SIZE_CHOICES = [("A4", "A4"), ("LETTER", "US Letter")]

    name = models.CharField(max_length=120, help_text="Internal label for this template.")
    institution_name = models.CharField(max_length=200)
    subtitle = models.CharField(
        max_length=200, blank=True, help_text="E.g. department or faculty name."
    )
    footer_text = models.CharField(
        max_length=200, blank=True, help_text="Printed in the footer of every page."
    )
    default_instructions = models.TextField(
        blank=True,
        help_text="Candidate instructions used when a paper doesn't set its own.",
    )
    font = models.CharField(max_length=8, choices=FONT_CHOICES, default="serif")
    paper_size = models.CharField(max_length=8, choices=SIZE_CHOICES, default="A4")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Paper(TenantOwnedModel):
    """An exam paper: metadata plus ordered sections of bank questions."""

    template = models.ForeignKey(
        PaperTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="papers"
    )
    title = models.CharField(max_length=255)
    course_code = models.CharField(max_length=64, blank=True)
    exam_date = models.DateField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    instructions = models.TextField(
        blank=True, help_text="Overrides the template's default instructions."
    )
    include_answer_space = models.BooleanField(
        default=True, help_text="Print ruled answer space after essay questions."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title

    @property
    def total_marks(self):
        agg = PaperQuestion.objects.filter(section__paper=self).aggregate(
            total=Sum(
                Coalesce(
                    F("marks_override"),
                    F("item__marks"),
                    output_field=DecimalField(max_digits=8, decimal_places=2),
                )
            )
        )
        return agg["total"] or 0

    @property
    def question_count(self):
        return PaperQuestion.objects.filter(section__paper=self).count()

    def duration_display(self):
        if not self.duration_minutes:
            return ""
        hours, minutes = divmod(self.duration_minutes, 60)
        parts = []
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        return " ".join(parts)

    def effective_instructions(self):
        if self.instructions.strip():
            return self.instructions
        if self.template is not None:
            return self.template.default_instructions
        return ""


class Section(models.Model):
    """A titled group of questions inside a paper (Section A, Section B…).

    Accessed through its Paper (which carries the org), same pattern as
    Choice→Item in the item bank.
    """

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField(max_length=120)
    instructions = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.paper}: {self.title}"


class PaperQuestion(models.Model):
    """One bank item placed in a section, with optional per-paper marks."""

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="questions")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="paper_usages")
    order = models.PositiveSmallIntegerField(default=0)
    marks_override = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Marks for this paper only; blank uses the item's marks.",
    )

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["section", "item"], name="unique_item_per_section")
        ]

    def __str__(self):
        return f"{self.section}: {self.item}"

    @property
    def marks(self):
        return self.marks_override if self.marks_override is not None else self.item.marks
