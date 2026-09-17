from __future__ import annotations

from pathlib import Path

from book_agent.services.bootstrap import BootstrapArtifacts, BootstrapPipeline


class BootstrapOrchestrator:
    """Runs the current bootstrap pipeline for supported document types."""

    def __init__(self, pipeline: BootstrapPipeline | None = None):
        self.pipeline = pipeline or BootstrapPipeline()

    def bootstrap_document(self, file_path: str | Path, *, org_id: str | None = None) -> BootstrapArtifacts:
        return self.pipeline.run(file_path, org_id=org_id)

    def bootstrap_epub(self, file_path: str | Path) -> BootstrapArtifacts:
        return self.bootstrap_document(file_path)
