from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class StyleDriftRule:
    pattern_id: str
    source_pattern: re.Pattern[str]
    target_pattern: re.Pattern[str]
    preferred_hint: str | None = None
    message: str = ""
    prompt_guidance: str | None = None


STYLE_DRIFT_RULES = (
    StyleDriftRule(
        pattern_id="context_engineering_literal",
        source_pattern=re.compile(r"\bcontext engineering\b", re.IGNORECASE),
        target_pattern=re.compile(r"(?:情境|语境)工程|(?:对|将|把)?(?:情境|语境)如何"),
        preferred_hint="上下文工程",
        message="核心概念出现明显字面直译，未采用更自然的技术表达。",
        prompt_guidance=(
            "When naming the field, prefer the established concept name '上下文工程' over literal "
            "variants like '情境工程' or '语境工程'. In the same definition sentence, keep 'context' "
            "rendered consistently as '上下文', not '情境' or '语境'."
        ),
    ),
    StyleDriftRule(
        pattern_id="weight_of_evidence_literal",
        source_pattern=re.compile(r"\bweight of evidence\b", re.IGNORECASE),
        target_pattern=re.compile(r"证据(?:的)?(?:分量|权重|重量)(?:表明|显示|说明|证明)?"),
        preferred_hint="大量证据表明 / 现有证据表明",
        message="英文习语被按字面结构硬译，中文技术写作感偏硬。",
        prompt_guidance=(
            "Prefer natural Chinese evidential phrasing such as '大量证据表明' or '现有证据表明', "
            "not literal weight metaphors."
        ),
    ),
    StyleDriftRule(
        pattern_id="contextually_accurate_outputs_literal",
        source_pattern=re.compile(r"\bcontextually accurate outputs?\b", re.IGNORECASE),
        target_pattern=re.compile(
            r"(?:"
            r"(?:上下文|情境)(?:更|更加)(?:准确|精确)(?:的)?(?:输出|结果)"
            r"|更具(?:上下文|情境)准确性(?:的)?(?:输出|结果)"
            r"|(?:上下文|情境)准确性(?:更高)?(?:的)?(?:输出|结果)"
            r")"
        ),
        preferred_hint="更符合上下文的输出",
        message="英文中的 contextually accurate 被按字面结构硬译，中文表达仍显生硬。",
        prompt_guidance=(
            "Prefer '更符合上下文的输出' or an equally natural Chinese expression, not literal forms "
            "like '上下文更准确的输出'."
        ),
    ),
    StyleDriftRule(
        pattern_id="knowledge_timeline_literal",
        source_pattern=re.compile(r"\bwhat was known,\s*when it was known\b", re.IGNORECASE),
        target_pattern=re.compile(r"获知时间"),
        preferred_hint="已知内容、知晓这些内容的时间点，以及其对行动的重要性",
        message="英文中的时间线说明被压成了生硬缩写，中文定义句读起来像直译痕迹。",
        prompt_guidance=(
            "When the source contrasts what was known with when it was known, unpack the timeline "
            "explicitly in Chinese; prefer phrasing like '已知内容、知晓这些内容的时间点，以及其对行动的重要性', "
            "and avoid compressed calques like '获知时间'."
        ),
    ),
    StyleDriftRule(
        pattern_id="emerging_term_scaffolding_literal",
        source_pattern=re.compile(r"\bwhat some are beginning to call\b", re.IGNORECASE),
        target_pattern=re.compile(r"(?:称之为|称为|称作).{0,18}(?:领域|内容)"),
        preferred_hint="有人开始将其称为……，它指的是……",
        message="英文中的命名引导句被译成了“称之为……的领域/内容”式骨架，中文读起来发硬。",
        prompt_guidance=(
            "When introducing an emerging term, avoid scaffolding like '称之为……的领域/内容'; "
            "prefer direct Chinese phrasing such as '有人开始将其称为……，它指的是……'."
        ),
    ),
    StyleDriftRule(
        pattern_id="durable_substrate_literal",
        source_pattern=re.compile(r"\bdurable substrate\b", re.IGNORECASE),
        target_pattern=re.compile(r"持久(?:性)?基底"),
        preferred_hint="使上下文得以持久存在的基础",
        message="英文抽象名词链被硬压成“持久基底”，中文定义句仍显生硬。",
        prompt_guidance=(
            "When translating 'durable substrate' in abstract technical prose, prefer natural Chinese "
            "such as '使上下文得以持久存在的基础' or '持久基础', not rigid calques like '持久基底'."
        ),
    ),
    StyleDriftRule(
        pattern_id="in_context_information_literal",
        source_pattern=re.compile(r"\bin-context information\b|\bexternal context\b", re.IGNORECASE),
        target_pattern=re.compile(r"情境信息|外部情境"),
        preferred_hint="上下文信息 / 外部上下文",
        message="与 context 相关的术语在前文记忆里仍使用了“情境”系旧译法，会污染后续 packet。",
        prompt_guidance=(
            "When translating technical phrases like 'in-context information' or 'external context', "
            "prefer '上下文信息' and '外部上下文', not older renderings with '情境'."
        ),
    ),
    StyleDriftRule(
        pattern_id="shift_from_to_literal",
        source_pattern=re.compile(r"\b(?:it represents|this represents|a) shift from\b", re.IGNORECASE),
        target_pattern=re.compile(r"(?:代表着|表示|意味着).{0,18}(?:从).{0,40}(?:转向|转变为|变成)"),
        preferred_hint="不再是……而是…… / 从……转向……",
        message="英文中的 shift-from-to 结构容易被硬搬成中文句法，读起来像直译骨架。",
        prompt_guidance=(
            "For shift statements, prefer natural Chinese structures such as '不再是……而是……' or "
            "'从……转向……' when they preserve meaning better than literal mirroring."
        ),
    ),
    StyleDriftRule(
        pattern_id="goal_reason_how_literal",
        source_pattern=re.compile(
            r"\btelling a computer what to do\b.*\bexplaining why we need something done\b.*\bfigure out the how\b",
            re.IGNORECASE,
        ),
        target_pattern=re.compile(r"执行什么操作|完成什么|探索实现方法"),
        preferred_hint="告诉它目标与原因，让它自己决定如何实现",
        message="英文中的目标/原因/实现分工被硬搬成逐词结构，中文技术表达不够像母语作者。",
        prompt_guidance=(
            "When the source contrasts 'what to do' with 'why we need something done' and 'the how', "
            "recast it into natural Chinese technical prose such as '告诉它目标与原因，让它自己决定如何实现', "
            "not stiff phrasing like '执行什么操作', '完成什么', or '探索实现方法'."
        ),
    ),
    StyleDriftRule(
        pattern_id="vantage_point_literal",
        source_pattern=re.compile(r"\bfrom my (?:vantage point|perspective) as\b", re.IGNORECASE),
        target_pattern=re.compile(r"从我作为.{0,30}(?:视角|角度)来看"),
        preferred_hint="从我这位……的角度看 / 站在……的角度看",
        message="英文 perspective shell 被按字面结构硬搬进中文，句子发重发硬。",
        prompt_guidance=(
            "When the source uses perspective shells like 'from my vantage point as ...', rewrite them into "
            "lighter Chinese technical prose such as '从我这位……的角度看' or '站在……的角度看', not rigid forms "
            "like '从我作为……的视角来看'."
        ),
    ),
    StyleDriftRule(
        pattern_id="immeasurably_high_literal",
        source_pattern=re.compile(r"\bthe stakes are immeasurably high\b", re.IGNORECASE),
        target_pattern=re.compile(r"难以估量|不可估量"),
        preferred_hint="风险极高 / 代价极高",
        message="英文强调风险的句子被译成了偏书面抒情的固定搭配，技术文风发飘。",
        prompt_guidance=(
            "For technical-book risk statements, prefer plain Chinese such as '风险极高' or '代价极高', "
            "not inflated forms like '难以估量'."
        ),
    ),
    StyleDriftRule(
        pattern_id="fun_anecdote_literal",
        source_pattern=re.compile(r"\b(?:a )?fun anecdote\b", re.IGNORECASE),
        target_pattern=re.compile(r"趣闻轶事"),
        preferred_hint="只是个趣闻 / 只是个小插曲",
        message="英文中的轻描淡写被译成了偏文绉绉的书面词，技术书语气不够平实。",
        prompt_guidance=(
            "For light contrast phrases such as 'a fun anecdote', prefer plain Chinese like '只是个趣闻' or "
            "'只是个小插曲', not literary wording such as '趣闻轶事'."
        ),
    ),
)


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
