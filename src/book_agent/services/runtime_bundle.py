from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.config import Settings, get_settings
from book_agent.core.ids import stable_id
from book_agent.domain.enums import RuntimeBundleRevisionStatus
from book_agent.domain.models.ops import RuntimeBundleRevision


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(slots=True)
class RuntimeBundleRecord:
    revision: RuntimeBundleRevision
    manifest_path: Path
    manifest_json: dict[str, Any]


class RuntimeBundleService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.bundle_root = Path(self.settings.runtime_bundle_root).resolve()
        self.bundle_root.mkdir(parents=True, exist_ok=True)
        self._active_pointer = self.bundle_root / "active.json"

    def publish_bundle(
        self,
        *,
        revision_name: str,
        manifest_json: dict[str, Any],
        bundle_type: str = "runtime",
        parent_bundle_revision_id: str | None = None,
        rollout_scope_json: dict[str, Any] | None = None,
    ) -> RuntimeBundleRecord:
        manifest_json = dict(manifest_json)
        manifest_hash = sha256(_canonical_json(manifest_json).encode("utf-8")).hexdigest()
        revision_id = stable_id("runtime-bundle-revision", bundle_type, revision_name, manifest_hash)
        revision_dir = self.bundle_root / revision_id
        revision_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = revision_dir / "manifest.json"
        published_at = _utcnow()

        payload = {
            "revision_id": revision_id,
            "bundle_type": bundle_type,
            "revision_name": revision_name,
            "parent_bundle_revision_id": parent_bundle_revision_id,
            "published_at": published_at.isoformat(),
            "manifest_json": manifest_json,
            "rollout_scope_json": rollout_scope_json or {},
        }
        manifest_path.write_text(_canonical_json(payload) + "\n", encoding="utf-8")

        revision = self.session.scalar(
            select(RuntimeBundleRevision).where(RuntimeBundleRevision.id == revision_id)
        )
        if revision is None:
            revision = RuntimeBundleRevision(
                id=revision_id,
                bundle_type=bundle_type,
                revision_name=revision_name,
                status=RuntimeBundleRevisionStatus.PUBLISHED,
                parent_bundle_revision_id=parent_bundle_revision_id,
                manifest_json=manifest_json,
                rollout_scope_json=rollout_scope_json or {},
                published_at=published_at,
                active_at=None,
                created_at=published_at,
                updated_at=published_at,
            )
        else:
            revision.bundle_type = bundle_type
            revision.revision_name = revision_name
            revision.status = RuntimeBundleRevisionStatus.PUBLISHED
            revision.parent_bundle_revision_id = parent_bundle_revision_id
            revision.manifest_json = manifest_json
            revision.rollout_scope_json = rollout_scope_json or {}
            revision.published_at = published_at
            revision.updated_at = published_at
        self.session.add(revision)
        self.session.flush()
        return RuntimeBundleRecord(revision=revision, manifest_path=manifest_path, manifest_json=payload)

    def lookup_bundle(self, revision_id: str) -> RuntimeBundleRecord:
        revision = self.session.scalar(
            select(RuntimeBundleRevision).where(RuntimeBundleRevision.id == revision_id)
        )
        if revision is None:
            raise ValueError(f"RuntimeBundleRevision not found: {revision_id}")
        manifest_path = self.bundle_root / revision.id / "manifest.json"
        if not manifest_path.exists():
            raise ValueError(f"Runtime bundle manifest missing: {manifest_path}")
        manifest_json = json.loads(manifest_path.read_text(encoding="utf-8"))
        return RuntimeBundleRecord(revision=revision, manifest_path=manifest_path, manifest_json=manifest_json)

    def lookup_bundle_manifest_payload(self, revision_id: str) -> dict[str, Any]:
        record = self.lookup_bundle(revision_id)
        return self._manifest_payload(record)

    def activate_bundle(self, revision_id: str) -> RuntimeBundleRecord:
        record = self.lookup_bundle(revision_id)
        now = _utcnow()
        record.revision.status = RuntimeBundleRevisionStatus.PUBLISHED
        record.revision.active_at = now
        record.revision.updated_at = now
        self.session.add(record.revision)
        self.session.flush()
        self._active_pointer.write_text(
            _canonical_json(
                {
                    "active_revision_id": revision_id,
                    "activated_at": now.isoformat(),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return record

    def lookup_active_bundle(self) -> RuntimeBundleRecord:
        if self._active_pointer.exists():
            active_pointer = json.loads(self._active_pointer.read_text(encoding="utf-8"))
            active_revision_id = active_pointer.get("active_revision_id")
            if isinstance(active_revision_id, str) and active_revision_id:
                return self.lookup_bundle(active_revision_id)

        revision = self.session.scalar(
            select(RuntimeBundleRevision)
            .where(RuntimeBundleRevision.status == RuntimeBundleRevisionStatus.PUBLISHED)
            .order_by(RuntimeBundleRevision.published_at.desc(), RuntimeBundleRevision.created_at.desc())
        )
        if revision is None:
            raise ValueError("No published runtime bundle revision available")
        return self.lookup_bundle(revision.id)

    def lookup_active_bundle_manifest_payload(self) -> dict[str, Any]:
        record = self.lookup_active_bundle()
        return self._manifest_payload(record)

    def _manifest_payload(self, record: RuntimeBundleRecord) -> dict[str, Any]:
        manifest_json = record.manifest_json.get("manifest_json")
        return dict(manifest_json) if isinstance(manifest_json, dict) else {}
