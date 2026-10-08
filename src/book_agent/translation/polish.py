"""Second pass over an accepted translation: a source-anchored Chinese editor.

The editing philosophy is the translator's edition of op7418/humanizer-zh (MIT): information and
certainty first, the author's voice second, expression last, and only the problems the translation
introduced are touched — the author's own padding and rhetoric stay. The edits most likely to change
meaning (pronouns turned into nouns, relation words, purpose and causal links, near-synonyms for
technical concepts) are ruled out, because a blind review found those were where the editor slipped.

Measured before it shipped, on 48 passages of a distributed-systems book judged blind against the
unpolished translation: preferred 20 to 10 (18 ties), fidelity level, naturalness about 5% higher,
one meaning slip introduced (a pronoun made specific), at about a fifth more tokens.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from book_agent.translation.contracts import TranslationUsage, TranslationWorkerOutput

POLISH_CALL_KIND = "translate.polish"

POLISH_SYSTEM_PROMPT = """你是中文技术书的译文编辑。你拿到英文原文和它的中文译文（按段编号），任务是只修改译文里的表达问题，让它读起来像中文作者直接写的，同时一个意思都不能改。

约束冲突时按以下顺序取舍：
一、忠实第一。改动后必须保留原文的每个信息点和确定程度：否定、比较级和最高级（least/most、at least/at most）、数量、范围、条件、时间、归因、目的和因果（for the sake of、so that、because）都不能变。拿不准的地方不改。
二、作者的声音。原文对“you”说话，译文就用“你”；保留作者的幽默、调侃、犹豫和第一人称；原文平实就保持平实，口语就保持口语。原文自己的铺垫、强调和修辞属于作者，不删。
三、只修译文带来的毛病，不添加原文没有的东西（不加拔高、宣传、总结、解释）：
- 长定语堆在名词前（“的”超过两层）→ 拆句或后置修饰语；
- 英文语序、名词链、“进行＋动词”、连用“被”字句 → 改成地道中文句式；
- 破折号用来代替逗号或制造悬念 → 改成正常标点；
- 同义限定词叠用、空泛的四字排比、重复表达（如“众多……最多”）→ 精简；
- 生硬或有歧义的用词（编程语境里把泛指的东西译成“对象”）→ 换成准确常用的词；
- 中文正文用中文标点，中文与英文、数字之间加空格。
四、原样保留：锁定术语的译法、代码、行内代码、命令、路径、URL、标识符、公式和数据；强调标记（*斜体*、**粗体**）成对保留，包住同一内容，不增不减。
五、以下改动一律不做（它们最容易改错意思）：
- 不把代词（它、这、其）替换成具体名词，除非原文就是那个名词；
- 不改动“在……中”“用……”“对……”“为了……”“以便……”这类表示关系、方式、目的的介词和连词；
- 不删除表示目的、因果、让步、条件的词语；
- 不把技术概念换成近义词（本质的≠必要的，潜伏≠潜在，部分同步≠半同步）；
- 不合并或拆分会改变主语的句子。
六、没有问题的句子原样保留，不要为了显得做了修改而改动通顺的文字。每一段都要返回，编号不变。

只输出 JSON：{"segments": [{"id": "段编号", "text_zh": "修改后的这一段译文"}]}。"""

POLISH_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "text_zh": {"type": "string"}},
                "required": ["id", "text_zh"],
            },
        }
    },
    "required": ["segments"],
}


@dataclass(slots=True)
class PolishOutcome:
    """The output to keep, what the editor did, and what the call cost."""

    output: TranslationWorkerOutput
    applied: bool
    changed_segment_ids: list[str] = field(default_factory=list)
    reverted_reason: str | None = None
    usage: TranslationUsage | None = None
    draft: list[dict[str, str]] = field(default_factory=list)

    def audit(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "changed_segments": len(self.changed_segment_ids),
            "reverted_reason": self.reverted_reason,
            # The unpolished text, so a bad edit can be traced and undone.
            "draft": self.draft if self.changed_segment_ids else [],
        }


def polish_user_prompt(
    output: TranslationWorkerOutput,
    source_by_sentence_id: Mapping[str, str],
    locked_terms: Sequence[tuple[str, str]] = (),
) -> str:
    sources = " ".join(source_by_sentence_id.values())
    relevant = [
        f"- {source} => {target}"
        for source, target in locked_terms
        if source.strip() and re.search(rf"(?i)\b{re.escape(source)}\b", sources)
    ]
    lines = ["锁定术语（必须照用）：", *(relevant or ["- （无）"]), ""]
    for segment in output.target_segments:
        source = " ".join(source_by_sentence_id.get(sid, "") for sid in segment.source_sentence_ids).strip()
        lines += [f"[{segment.temp_id}]", f"原文：{source}", f"译文：{segment.text_zh}", ""]
    return "\n".join(lines).rstrip()


def polish_output(
    generate: Callable[..., tuple[dict[str, Any], TranslationUsage]],
    *,
    model_name: str,
    output: TranslationWorkerOutput,
    source_by_sentence_id: Mapping[str, str],
    locked_terms: Sequence[tuple[str, str]] = (),
) -> PolishOutcome:
    """Ask the editor for every segment back; anything short of that keeps the draft."""
    draft = [{"id": segment.temp_id, "text_zh": segment.text_zh} for segment in output.target_segments]
    if not output.target_segments:
        return PolishOutcome(output=output, applied=False, reverted_reason="no_segments")
    payload, usage = generate(
        model_name=model_name,
        system_prompt=POLISH_SYSTEM_PROMPT,
        user_prompt=polish_user_prompt(output, source_by_sentence_id, locked_terms),
        response_schema=POLISH_RESPONSE_SCHEMA,
        schema_name="translation_polish",
    )
    returned = {
        str(item.get("id")): str(item.get("text_zh") or "").strip()
        for item in (payload or {}).get("segments") or []
        if isinstance(item, Mapping)
    }
    expected = {segment.temp_id for segment in output.target_segments}
    if set(returned) != expected or not all(returned.values()):
        return PolishOutcome(output=output, applied=False, reverted_reason="segments_mismatch", usage=usage, draft=draft)
    changed = [segment.temp_id for segment in output.target_segments if returned[segment.temp_id] != segment.text_zh]
    polished = output.model_copy(
        update={
            "target_segments": [
                segment.model_copy(update={"text_zh": returned[segment.temp_id]}) for segment in output.target_segments
            ]
        }
    )
    return PolishOutcome(output=polished, applied=True, changed_segment_ids=changed, usage=usage, draft=draft)
