"""Module registry.

Each feature module (item bank, paper MCQ, online exam, formatting,
blueprint) is a Django app that registers itself here from its
AppConfig.ready(). The core knows nothing about specific modules beyond
this registry; navigation and the dashboard are built from it, and plans
gate access by module code. Adding a module later means adding an app and
one register() call — no changes to existing modules.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class Module:
    code: str                    # feature-flag code used in Plan.features["modules"]
    name: str
    description: str
    url_name: str                # namespaced URL to the module's landing page
    icon: str = ""
    # Optional callable(org) -> short stat string for the dashboard card.
    stats: Optional[Callable] = field(default=None, compare=False)


_REGISTRY: dict[str, Module] = {}


def register(module: Module) -> None:
    _REGISTRY[module.code] = module


def all_modules() -> list[Module]:
    return list(_REGISTRY.values())


def get_module(code: str) -> Optional[Module]:
    return _REGISTRY.get(code)


# Modules planned but not yet built; shown on the dashboard as "coming soon"
# so the product shape is visible from day one.
PLANNED_MODULES = [
    ("paper_mcq", "Paper MCQ (OMR)", "Print randomized MCQ sheets, scan them back, auto-grade via Auto Multiple Choice."),
    ("online_exam", "Online exam", "Browser-delivered, timed, auto-graded exams."),
    ("blueprint", "AI blueprint generator", "Propose a table of specification from a syllabus and fill it from the bank."),
]
