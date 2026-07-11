import uuid

from django.conf import settings
from django.db import models

from core.models import TenantOwnedModel


class Topic(TenantOwnedModel):
    """A subject-matter tag, optionally nested one level (topic → subtopic)."""

    name = models.CharField(max_length=200)
    # Cascade so deleting an organization (which cascades to its topics) works
    # even when parent→child topic links exist. User-initiated deletion of a
    # populated parent topic is blocked at the view layer (TopicDeleteView).
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "parent", "name"], name="unique_topic_name_per_parent"
            )
        ]

    def __str__(self):
        if self.parent is not None:
            return f"{self.parent.name} › {self.name}"
        return self.name


class Item(TenantOwnedModel):
    """A question in the bank.

    The type registry (itembank.itemtypes) defines per-type behavior;
    adding a question type later means registering a new type, not
    changing this model.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Hard"

    class CognitiveLevel(models.TextChoices):
        # Bloom's taxonomy (revised).
        REMEMBER = "remember", "Remember"
        UNDERSTAND = "understand", "Understand"
        APPLY = "apply", "Apply"
        ANALYZE = "analyze", "Analyze"
        EVALUATE = "evaluate", "Evaluate"
        CREATE = "create", "Create"

    TYPE_MCQ = "mcq"
    TYPE_ESSAY = "essay"
    TYPE_CHOICES = [(TYPE_MCQ, "Multiple choice"), (TYPE_ESSAY, "Essay")]

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    item_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    title = models.CharField(max_length=255)
    body = models.TextField(
        help_text="The question text (the stem). Blank line separates paragraphs."
    )
    model_answer = models.TextField(
        blank=True,
        help_text="Marking guide / model answer (essay items). Exported as a "
        "QTI scorer rubric block.",
    )
    topic = models.ForeignKey(
        Topic, on_delete=models.SET_NULL, null=True, blank=True, related_name="items"
    )
    difficulty = models.CharField(
        max_length=16, choices=Difficulty.choices, default=Difficulty.MEDIUM
    )
    cognitive_level = models.CharField(
        max_length=16, choices=CognitiveLevel.choices, default=CognitiveLevel.UNDERSTAND
    )
    marks = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    external_id = models.CharField(
        max_length=128,
        blank=True,
        help_text="QTI identifier recorded on import, used to detect duplicates.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["org", "item_type"]),
            models.Index(fields=["org", "status"]),
        ]

    def __str__(self):
        return self.title

    @property
    def qti_identifier(self):
        return self.external_id or f"itm-{self.uuid}"


class Choice(models.Model):
    """An answer option for an MCQ item."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=1000)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:80]
