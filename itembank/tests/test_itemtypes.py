import pytest
from django.core.exceptions import ValidationError

from itembank import itemtypes


def test_mcq_requires_exactly_one_correct():
    with pytest.raises(ValidationError):
        itemtypes.validate_choices("mcq", [("A", False), ("B", False)])
    with pytest.raises(ValidationError):
        itemtypes.validate_choices("mcq", [("A", True), ("B", True)])
    # valid: two choices, one correct
    itemtypes.validate_choices("mcq", [("A", True), ("B", False)])


def test_mcq_requires_at_least_two_choices():
    with pytest.raises(ValidationError):
        itemtypes.validate_choices("mcq", [("A", True)])


def test_essay_rejects_choices():
    with pytest.raises(ValidationError):
        itemtypes.validate_choices("essay", [("A", False)])
    itemtypes.validate_choices("essay", [])  # ok, no choices


def test_registry_has_mcq_and_essay():
    codes = {t.code for t in itemtypes.all_types()}
    assert {"mcq", "essay"} <= codes
