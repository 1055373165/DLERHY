from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5


def stable_id(*parts: object) -> str:
    material = "::".join(str(part) for part in parts)
    return str(uuid5(NAMESPACE_URL, material))

