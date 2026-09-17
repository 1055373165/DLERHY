"""In-process Prometheus metrics (text exposition format), no extra dependency.

Counters and histograms are updated where things happen; gauges that are
cheap to read from the database or the connection pool are collected at
scrape time by ``app/api/metrics_route.py``. Each process keeps its own
registry, so run one scrape target per API/executor process.

Event-derived LLM metrics are counted when the event is written; a
transaction that later rolls back may leave a count behind, which is fine
for rates and dashboards but not for billing (billing reads the events).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable, Sequence

_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: Sequence[str], values: Sequence[str], extra: tuple[str, str] | None = None) -> str:
    pairs = [f'{name}="{_escape(value)}"' for name, value in zip(names, values)]
    if extra is not None:
        pairs.append(f'{extra[0]}="{_escape(extra[1])}"')
    return "{" + ",".join(pairs) + "}" if pairs else ""


def _format(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    number = float(value)
    return str(int(number)) if number.is_integer() and abs(number) < 1e15 else repr(number)


class _Metric:
    kind = ""

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> None:
        self.name = name
        self.help = help_text
        self.label_names = tuple(label_names)
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str]) -> tuple[str, ...]:
        return tuple(str(labels.get(name, "")) for name in self.label_names)

    def header(self) -> list[str]:
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]


class Counter(_Metric):
    kind = "counter"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            return
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        return self._values.get(self._key(labels), 0.0)

    def render(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        return self.header() + [f"{self.name}{_labels(self.label_names, key)} {_format(value)}" for key, value in items]


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = (), buckets: Iterable[float] = _DEFAULT_BUCKETS) -> None:
        super().__init__(name, help_text, label_names)
        self.buckets = tuple(sorted(buckets))
        self._counts: dict[tuple[str, ...], list[int]] = {}
        self._sums: dict[tuple[str, ...], float] = {}

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * (len(self.buckets) + 1))
            for index, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[index] += 1
            counts[-1] += 1
            self._sums[key] = self._sums.get(key, 0.0) + value

    def count(self, **labels: str) -> int:
        counts = self._counts.get(self._key(labels))
        return counts[-1] if counts else 0

    def render(self) -> list[str]:
        lines = self.header()
        with self._lock:
            items = sorted((key, list(counts), self._sums[key]) for key, counts in self._counts.items())
        for key, counts, total in items:
            for index, bound in enumerate(self.buckets):
                lines.append(f"{self.name}_bucket{_labels(self.label_names, key, ('le', _format(bound)))} {counts[index]}")
            lines.append(f"{self.name}_bucket{_labels(self.label_names, key, ('le', '+Inf'))} {counts[-1]}")
            lines.append(f"{self.name}_sum{_labels(self.label_names, key)} {_format(total)}")
            lines.append(f"{self.name}_count{_labels(self.label_names, key)} {counts[-1]}")
        return lines


def render_gauge(name: str, help_text: str, samples: Iterable[tuple[dict[str, str], float]]) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    for labels, value in samples:
        names = sorted(labels)
        lines.append(f"{name}{_labels(names, [labels[n] for n in names])} {_format(value)}")
    return lines


HTTP_REQUESTS = Counter("book_agent_http_requests_total", "HTTP requests by method, route template and status.", ("method", "route", "status"))
HTTP_DURATION = Histogram("book_agent_http_request_duration_seconds", "HTTP request latency.", ("method", "route"))
EXECUTOR_TICK = Histogram("book_agent_executor_tick_duration_seconds", "Duration of one run loop tick.")
WORK_ITEMS = Counter("book_agent_work_items_finished_total", "Work items finished by stage and outcome.", ("stage", "outcome"))
LEASES_RECLAIMED = Counter("book_agent_leases_reclaimed_total", "Expired work item leases reclaimed.")
LLM_CALLS = Counter("book_agent_llm_calls_total", "Model calls by call kind and status.", ("call_kind", "status"))
LLM_TOKENS = Counter("book_agent_llm_tokens_total", "Model tokens by call kind and direction.", ("call_kind", "direction"))
LLM_COST = Counter("book_agent_llm_cost_usd_total", "Model cost in USD by call kind (when priced).", ("call_kind",))
LLM_LATENCY = Histogram("book_agent_llm_call_duration_seconds", "Model call latency by call kind.", ("call_kind",))

REGISTRY: tuple[Counter | Histogram, ...] = (
    HTTP_REQUESTS,
    HTTP_DURATION,
    EXECUTOR_TICK,
    WORK_ITEMS,
    LEASES_RECLAIMED,
    LLM_CALLS,
    LLM_TOKENS,
    LLM_COST,
    LLM_LATENCY,
)


def record_llm_event(kind: str, payload: dict) -> None:
    """Called by emit_event for llm.call.completed / llm.call.failed."""
    call_kind = str(payload.get("call_kind") or "unknown")
    if kind.endswith("completed"):
        LLM_CALLS.inc(call_kind=call_kind, status="completed")
        LLM_TOKENS.inc(float(payload.get("token_in") or 0), call_kind=call_kind, direction="in")
        LLM_TOKENS.inc(float(payload.get("token_out") or 0), call_kind=call_kind, direction="out")
        if payload.get("cost_usd") is not None:
            LLM_COST.inc(float(payload["cost_usd"]), call_kind=call_kind)
        if payload.get("latency_ms") is not None:
            LLM_LATENCY.observe(float(payload["latency_ms"]) / 1000.0, call_kind=call_kind)
    else:
        LLM_CALLS.inc(call_kind=call_kind, status="failed")


def render_registry() -> list[str]:
    lines: list[str] = []
    for metric in REGISTRY:
        lines.extend(metric.render())
    return lines
