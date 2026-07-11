"""Question-type registry.

Each type declares its authoring/validation behavior in one place so that
adding a type (e.g. true/false, matching, numeric) is a matter of
registering a new spec plus QTI (de)serialization — no changes to models,
views or templates that iterate the registry.
"""
from dataclasses import dataclass

from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class ItemTypeSpec:
    code: str
    label: str
    description: str
    has_choices: bool
    has_model_answer: bool


_REGISTRY: dict[str, ItemTypeSpec] = {}


def register(spec: ItemTypeSpec) -> None:
    _REGISTRY[spec.code] = spec


def get_type(code: str) -> ItemTypeSpec | None:
    return _REGISTRY.get(code)


def all_types() -> list[ItemTypeSpec]:
    return list(_REGISTRY.values())


register(
    ItemTypeSpec(
        code="mcq",
        label="Multiple choice",
        description="One stem, several options, exactly one correct answer.",
        has_choices=True,
        has_model_answer=False,
    )
)
register(
    ItemTypeSpec(
        code="essay",
        label="Essay",
        description="Free-response question marked by a human against a marking guide.",
        has_choices=False,
        has_model_answer=True,
    )
)


def validate_choices(item_type: str, choices: list[tuple[str, bool]]) -> None:
    """Validate (text, is_correct) pairs for a type. Raises ValidationError.

    v1 MCQs are single-correct-answer: that is what OMR answer keys and
    simple auto-grading expect. Multiple-response can be added as a new
    type later.
    """
    spec = get_type(item_type)
    if spec is None:
        raise ValidationError(f"Unknown item type: {item_type}")
    non_empty = [(t, c) for t, c in choices if t and t.strip()]
    if not spec.has_choices:
        if non_empty:
            raise ValidationError("This question type does not take answer choices.")
        return
    if len(non_empty) < 2:
        raise ValidationError("A multiple-choice question needs at least two choices.")
    correct = [t for t, c in non_empty if c]
    if len(correct) != 1:
        raise ValidationError("Mark exactly one choice as correct.")
