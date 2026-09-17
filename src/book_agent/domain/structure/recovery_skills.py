"""Book- and publisher-specific PDF recovery passes as switchable skills.

Most of the 22 block recovery passes are generic layout repair. A few encode
conventions of particular publishers or document kinds and misfire on books
that do not follow them. Each such pass belongs to a named skill that a
document can switch off (``documents.metadata_json["recovery_skills"]``);
a structure refresh then reparses without it. All skills are on by default,
which is exactly the parser's behaviour before skills existed.

Skill guides live in ``skills/structure/<name>/SKILL.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

METADATA_KEY = "recovery_skills"


@dataclass(frozen=True, slots=True)
class RecoverySkill:
    name: str
    title: str
    passes: tuple[str, ...]
    description: str
    default_enabled: bool = True


RECOVERY_SKILLS: tuple[RecoverySkill, ...] = (
    RecoverySkill(
        name="manning-listings",
        title="Manning-style code listings",
        passes=("lock_listing_scope",),
        description=(
            "Treats 'Listing N.M' as the start of a code listing: splits trailing code comments back out, "
            "suppresses listing annotations and turns keyword-led paragraphs nearby into code. Switch off for "
            "books that do not use Manning's listing convention."
        ),
    ),
    RecoverySkill(
        name="academic-sections",
        title="Academic paper inline sections",
        passes=("recover_academic_sections",),
        description=(
            "Splits paragraphs at inline numbered section titles ('3.2 Method'). Runs only in the academic "
            "recovery lane; switch off when numbered sentences are being cut into fake sections."
        ),
    ),
    RecoverySkill(
        name="text-only-figures",
        title="Figures drawn as text",
        passes=("recover_text_only_figures",),
        description=(
            "Builds a figure from short text blocks above an unlinked 'Figure N.M' caption. Switch off when "
            "short headings or list items above captions are swallowed into figures."
        ),
    ),
    RecoverySkill(
        name="contextual-image-legends",
        title="Legends next to images",
        passes=("promote_contextual_image_legends",),
        description=(
            "Turns short centred 'The/These ... N' lines next to images into captions. Switch off when body "
            "sentences near images are misread as captions."
        ),
    ),
)

_BY_NAME = {skill.name: skill for skill in RECOVERY_SKILLS}


def skill_names() -> list[str]:
    return [skill.name for skill in RECOVERY_SKILLS]


def validate_overrides(overrides: Mapping[str, object]) -> dict[str, bool]:
    unknown = sorted(set(overrides) - set(_BY_NAME))
    if unknown:
        raise ValueError(f"unknown recovery skills: {', '.join(unknown)}; known: {', '.join(skill_names())}")
    return {str(name): bool(value) for name, value in overrides.items()}


def resolve(overrides: Mapping[str, object] | None) -> dict[str, bool]:
    """Enabled state of every skill: document override, else the default. Unknown names are ignored."""
    overrides = overrides if isinstance(overrides, Mapping) else {}
    return {
        skill.name: bool(overrides[skill.name]) if skill.name in overrides else skill.default_enabled
        for skill in RECOVERY_SKILLS
    }


def disabled_passes(overrides: Mapping[str, object] | None) -> frozenset[str]:
    enabled = resolve(overrides)
    return frozenset(pass_name for skill in RECOVERY_SKILLS if not enabled[skill.name] for pass_name in skill.passes)
