"""Estimate what translating a book will cost before the run starts.

The estimate is built from the book's packets and sentences with ratios
measured on a real whole-book run (docs/agent-upgrade/07-paid-book-eval.md,
RSI book, 625 packets, deepseek-v4-flash):

- translation input = packet content + ~2,040 tokens of prompt scaffolding
  per packet (instructions, glossary, chapter memory);
- translation output = ~6.2 × source tokens (the answer carries target text
  plus sentence alignment);
- model review (sampled) reads ~0.64 × the translation input, a full review
  ~1.9 ×; terminology costs ~300k input tokens sampled, ~600k thorough,
  for a book of ~30k source tokens or more, and proportionally less for a
  shorter one (it reads what there is);
- the agents also have a floor that does not shrink with the book: a
  reviewer turn reads ~50k tokens of its own prompts and tool results and
  terminology ~10k (measured on a 5-packet book, where they were 85% of
  the input);
- ~5% on top for retries and output repairs.

It is a range (±30%), not a quote: it ignores prompt-cache discounts (so it
leans high on input) and model verbosity varies. Prices come from the
provider the book's organisation translates with (its own, else the shared
one), falling back to the price settings; without prices only tokens are
given.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.config import Settings
from book_agent.domain.models import Chapter, Document, Sentence
from book_agent.domain.models.translation import TranslationPacket
from book_agent.harness.kernel.messages import estimate_tokens

PROMPT_SCAFFOLD_TOKENS_PER_PACKET = 2040
OUTPUT_TOKENS_PER_SOURCE_TOKEN = 6.2
REVIEW_INPUT_FACTOR = {"skip": 0.0, "sampled": 0.64, "full": 1.9}
REVIEW_OUTPUT_TOKENS_PER_PACKET = {"skip": 0, "sampled": 25, "full": 70}
TERMINOLOGY_FULL_COST_SOURCE_TOKENS = 30_000
TERMINOLOGY_TOKENS = {"skip": (0, 0), "sampled": (300_000, 11_000), "thorough": (600_000, 22_000)}
TERMINOLOGY_FLOOR_TOKENS = (10_000, 2_500)
REVIEW_FIXED_TOKENS = (50_000, 5_000)
RETRY_OVERHEAD = 1.05
SPREAD = 0.3


@dataclass(slots=True)
class CostEstimate:
    document_id: str
    packet_count: int
    source_tokens: int
    token_in: int
    token_out: int
    token_in_range: tuple[int, int]
    token_out_range: tuple[int, int]
    breakdown: dict[str, dict[str, int]]
    cost_usd: float | None
    cost_usd_range: tuple[float, float] | None
    price_source: str | None
    input_cost_per_1m_tokens: float | None
    output_cost_per_1m_tokens: float | None
    thinking_may_inflate_output: bool
    notes: list[str]

    def to_json(self) -> dict:
        return asdict(self)


def estimate_document_cost(
    session: Session,
    document_id: str,
    settings: Settings,
    *,
    terminology: str = "sampled",
    model_review: str = "sampled",
) -> CostEstimate:
    document = session.get(Document, document_id)
    if document is None:
        raise LookupError("document not found")
    packets = list(
        session.scalars(
            select(TranslationPacket).join(Chapter, Chapter.id == TranslationPacket.chapter_id).where(Chapter.document_id == document_id)
        ).all()
    )
    source_tokens = sum(
        estimate_tokens(text)
        for (text,) in session.execute(
            select(Sentence.source_text).where(
                Sentence.document_id == document_id,
                Sentence.translatable.is_(True),
                Sentence.retired_by_revision_id.is_(None),
            )
        )
    )
    packet_tokens = sum(estimate_tokens(json.dumps(packet.packet_json or {}, ensure_ascii=False)) for packet in packets)
    translate_in = packet_tokens + PROMPT_SCAFFOLD_TOKENS_PER_PACKET * len(packets)
    translate_out = int(OUTPUT_TOKENS_PER_SOURCE_TOKEN * source_tokens)
    review_mode = model_review if model_review in REVIEW_INPUT_FACTOR else "sampled"
    terminology_mode = terminology if terminology in TERMINOLOGY_TOKENS else "sampled"
    breakdown = {
        "translate": {"token_in": translate_in, "token_out": translate_out},
        "model_review": {
            "token_in": int(REVIEW_INPUT_FACTOR[review_mode] * translate_in)
            + (REVIEW_FIXED_TOKENS[0] if review_mode != "skip" and packets else 0),
            "token_out": REVIEW_OUTPUT_TOKENS_PER_PACKET[review_mode] * len(packets)
            + (REVIEW_FIXED_TOKENS[1] if review_mode != "skip" and packets else 0),
        },
        "terminology": {
            key: max(floor, int(tokens * min(1.0, source_tokens / TERMINOLOGY_FULL_COST_SOURCE_TOKENS)))
            if terminology_mode != "skip" and packets
            else 0
            for key, tokens, floor in zip(
                ("token_in", "token_out"), TERMINOLOGY_TOKENS[terminology_mode], TERMINOLOGY_FLOOR_TOKENS, strict=True
            )
        },
    }
    token_in = int(RETRY_OVERHEAD * sum(part["token_in"] for part in breakdown.values()))
    token_out = int(RETRY_OVERHEAD * sum(part["token_out"] for part in breakdown.values()))
    input_price, output_price, price_source, thinking_off = _provider(session, document, settings)
    notes = ["估算依据真实整书运行测得的比例，误差约 ±30%；未计提示缓存折扣，输入部分偏高。"]
    if not thinking_off:
        notes.append("估算按关闭思考模式计。若所用模型开启了思考，输出 token 会明显更多（实测小书约为估算的 2–3 倍）；可在「服务商」页关闭。")
    cost = cost_range = None
    if input_price is not None and output_price is not None:
        cost = round(token_in / 1e6 * input_price + token_out / 1e6 * output_price, 4)
        cost_range = (round(cost * (1 - SPREAD), 4), round(cost * (1 + SPREAD), 4))
    else:
        notes.append("当前服务商没有设置单价，只给出 token 数；在「服务商」页填入单价后可估算金额。")
    if not packets:
        notes.append("书稿还没有翻译分包，估算为 0。")
    return CostEstimate(
        document_id=document_id,
        packet_count=len(packets),
        source_tokens=source_tokens,
        token_in=token_in,
        token_out=token_out,
        token_in_range=(int(token_in * (1 - SPREAD)), int(token_in * (1 + SPREAD))),
        token_out_range=(int(token_out * (1 - SPREAD)), int(token_out * (1 + SPREAD))),
        breakdown=breakdown,
        cost_usd=cost,
        cost_usd_range=cost_range,
        price_source=price_source,
        input_cost_per_1m_tokens=input_price,
        output_cost_per_1m_tokens=output_price,
        thinking_may_inflate_output=not thinking_off,
        notes=notes,
    )


def _provider(
    session: Session, document: Document, settings: Settings
) -> tuple[float | None, float | None, str | None, bool]:
    return active_provider_pricing(session, document.org_id, settings)


def active_provider_pricing(
    session: Session, org_id: str | None, settings: Settings
) -> tuple[float | None, float | None, str | None, bool]:
    """For the provider an organisation translates with: (input price, output price, where the
    prices came from, whether thinking is known to be off)."""
    from book_agent.domain.enums import ProviderKind
    from book_agent.services.provider_credentials import credential_scope, get_active_credential

    scope = credential_scope(org_id)
    record = (get_active_credential(session, scope) if scope is not None else None) or get_active_credential(session, None)
    # Like the worker factory: the credential's overrides, else the settings'.
    overrides = (record.request_overrides_json if record is not None else None) or settings.translation_openai_request_overrides
    thinking = overrides.get("thinking") if isinstance(overrides, dict) else None
    thinking_off = (record is not None and record.provider_kind == ProviderKind.ECHO) or (
        isinstance(thinking, dict) and thinking.get("type") == "disabled"
    )
    if record is not None and record.input_cost_per_1m_tokens is not None and record.output_cost_per_1m_tokens is not None:
        prices = float(record.input_cost_per_1m_tokens), float(record.output_cost_per_1m_tokens), f"provider:{record.name}"
        return (*prices, thinking_off)
    if settings.translation_input_cost_per_1m_tokens is not None and settings.translation_output_cost_per_1m_tokens is not None:
        prices = settings.translation_input_cost_per_1m_tokens, settings.translation_output_cost_per_1m_tokens, "settings"
        return (*prices, thinking_off)
    return None, None, None, thinking_off
