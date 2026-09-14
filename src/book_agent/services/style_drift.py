from __future__ import annotations

from book_agent.translation.heuristics import DEFAULT_HEURISTICS, StyleDriftRule

# Rules live in the translation heuristics data pack.
STYLE_DRIFT_RULES: tuple[StyleDriftRule, ...] = DEFAULT_HEURISTICS.style_drift_rules


def source_aware_literalism_guardrail_lines(source_text: str) -> list[str]:
    normalized = source_text.strip()
    if not normalized:
        return []
    lines: list[str] = []
    for rule in STYLE_DRIFT_RULES:
        if rule.source_pattern.search(normalized):
            if rule.preferred_hint:
                lines.append(f"Prefer: {rule.preferred_hint}")
            if rule.prompt_guidance:
                lines.append(rule.prompt_guidance)
    return lines
