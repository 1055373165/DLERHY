"""End-to-end smoke for the provider credentials service.

Boots the secret module (auto-generates BOOK_AGENT_SECRET_KEY into .env
if missing), runs through bootstrap-from-env / create / activate / test
without touching the HTTP layer. Run after migrations to verify the
service plumbing works.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Bypass the user's local proxy for direct provider calls.
for _v in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "*"

from book_agent.core.config import get_settings
from book_agent.domain.enums import ProviderKind
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services import provider_credentials as svc


def main() -> int:
    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine=engine)

    with session_scope(factory) as session:
        active = svc.resolve_active_credential(session, settings)
        print(f"[smoke] bootstrap active: {active.name if active else None}")

        rows = svc.list_credentials(session)
        print(f"[smoke] list count: {len(rows)}")
        for r in rows:
            print(
                f"  - {r.id[:8]} '{r.name}' kind={r.provider_kind.value} "
                f"model={r.model_name} active={r.is_active} "
                f"key_preview={svc.api_key_preview(r)}"
            )

        if active is not None and active.provider_kind == ProviderKind.OPENAI_COMPATIBLE:
            print(f"[smoke] testing connection for active: {active.name}")
            outcome = svc.test_credential_connection(active)
            print(
                f"  status={outcome.status.value} elapsed_ms={outcome.elapsed_ms} "
                f"sample={(outcome.sample_output or '')[:80]} message={outcome.message}"
            )
            svc.record_test_outcome(session, active.id, outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
