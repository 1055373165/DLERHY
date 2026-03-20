from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Protocol

from book_agent.domain.structure.pdf import (
    PdfExtraction,
    PdfFileProfile,
    PdfPage,
    PdfStructureRecoveryService,
    PdfTextBlock,
    _normalize_multiline_text,
    _normalize_text,
)
from book_agent.domain.structure.models import ParsedDocument

_OCR_INLINE_TAG_PATTERN = re.compile(r"</?(?:b|strong|i|em|u)>", re.IGNORECASE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_text_tail(path: Path, *, max_bytes: int = 4096) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes), os.SEEK_SET)
        return handle.read().decode("utf-8", errors="replace")


def _output_snapshot(output_dir: str | Path) -> dict[str, Any]:
    path = Path(output_dir).resolve()
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
        }

    file_count = 0
    dir_count = 0
    latest_mtime = 0.0
    results_path = next(path.rglob("results.json"), None)
    for candidate in path.rglob("*"):
        if candidate.is_dir():
            dir_count += 1
            continue
        file_count += 1
        latest_mtime = max(latest_mtime, candidate.stat().st_mtime)

    return {
        "path": str(path),
        "exists": True,
        "file_count": file_count,
        "dir_count": dir_count,
        "results_json_exists": results_path is not None,
        "results_json_path": str(results_path) if results_path is not None else None,
        "latest_file_mtime": (
            datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat() if latest_mtime > 0 else None
        ),
    }


class OcrCommandRunner(Protocol):
    def run(self, *, file_path: str | Path, output_dir: str | Path) -> Path:
        ...


@dataclass(slots=True)
class UvSuryaOcrRunner:
    runtime_python: str | None = None
    surya_package: str = "surya-ocr==0.17.1"
    transformers_package: str = "transformers==4.56.1"
    page_range: str | None = None
    status_path: str | Path | None = None
    heartbeat_interval_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.status_path is None:
            env_status_path = os.getenv("BOOK_AGENT_OCR_STATUS_PATH")
            if env_status_path:
                self.status_path = env_status_path
        if self.heartbeat_interval_seconds is None:
            env_heartbeat = os.getenv("BOOK_AGENT_OCR_HEARTBEAT_SECONDS")
            if env_heartbeat:
                try:
                    self.heartbeat_interval_seconds = float(env_heartbeat)
                except ValueError:
                    self.heartbeat_interval_seconds = None
        if self.heartbeat_interval_seconds is None:
            self.heartbeat_interval_seconds = 5.0
        self.heartbeat_interval_seconds = max(float(self.heartbeat_interval_seconds), 0.1)

    def _build_command(self, *, file_path: str | Path, output_dir: str | Path) -> list[str]:
        # Surya 0.17.1 currently breaks under transformers 5.x during decoder generation.
        # Pin the OCR subprocess runtime so scanned-PDF support stays reproducible.
        command = ["uv", "run"]
        runtime_python = self.runtime_python
        if runtime_python is None and shutil.which("python3.13") is not None:
            runtime_python = "3.13"
        if runtime_python:
            command.extend(["--python", runtime_python])
        command.extend(
            [
                "--with",
                self.surya_package,
                "--with",
                self.transformers_package,
                "--with",
                "Pillow",
                "--with",
                "requests",
                "surya_ocr",
                str(Path(file_path).resolve()),
            ]
        )
        if self.page_range:
            command.extend(["--page_range", self.page_range])
        command.extend(
            [
                "--output_dir",
                str(Path(output_dir).resolve()),
            ]
        )
        return command

    def _write_status(self, payload: dict[str, Any]) -> None:
        if self.status_path is None:
            return
        status_path = Path(self.status_path).resolve()
        status_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = status_path.with_suffix(status_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(status_path)

    def _status_payload(
        self,
        *,
        state: str,
        file_path: str | Path,
        output_dir: str | Path,
        command: list[str],
        pid: int | None = None,
        returncode: int | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": state,
            "file_path": str(Path(file_path).resolve()),
            "output_dir": str(Path(output_dir).resolve()),
            "command": command,
            "page_range": self.page_range,
            "runtime_python": self.runtime_python,
            "surya_package": self.surya_package,
            "transformers_package": self.transformers_package,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "last_updated_at": _utcnow().isoformat(),
            "output_snapshot": _output_snapshot(output_dir),
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        }
        if pid is not None:
            payload["pid"] = pid
        if returncode is not None:
            payload["returncode"] = returncode
        if started_at is not None:
            payload["started_at"] = started_at.isoformat()
            payload["elapsed_seconds"] = round((_utcnow() - started_at).total_seconds(), 3)
        if finished_at is not None:
            payload["finished_at"] = finished_at.isoformat()
        return payload

    def run(self, *, file_path: str | Path, output_dir: str | Path) -> Path:
        if shutil.which("uv") is None:
            raise RuntimeError("OCR support requires `uv` to be installed and available on PATH.")
        command = self._build_command(file_path=file_path, output_dir=output_dir)
        started_at = _utcnow()
        self._write_status(
            self._status_payload(
                state="starting",
                file_path=file_path,
                output_dir=output_dir,
                command=command,
                started_at=started_at,
            )
        )

        stdout_file = tempfile.NamedTemporaryFile(
            mode="w+",
            encoding="utf-8",
            suffix=".surya.stdout.log",
            delete=False,
        )
        stderr_file = tempfile.NamedTemporaryFile(
            mode="w+",
            encoding="utf-8",
            suffix=".surya.stderr.log",
            delete=False,
        )
        stdout_path = Path(stdout_file.name)
        stderr_path = Path(stderr_file.name)
        process: subprocess.Popen[str] | None = None
        returncode: int | None = None
        try:
            process = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
            )
            while True:
                stdout_file.flush()
                stderr_file.flush()
                returncode = process.poll()
                current_state = "running" if returncode is None else ("succeeded" if returncode == 0 else "failed")
                self._write_status(
                    self._status_payload(
                        state=current_state,
                        file_path=file_path,
                        output_dir=output_dir,
                        command=command,
                        pid=process.pid,
                        returncode=returncode,
                        started_at=started_at,
                        finished_at=_utcnow() if returncode is not None else None,
                        stdout_tail=_read_text_tail(stdout_path),
                        stderr_tail=_read_text_tail(stderr_path),
                    )
                )
                if returncode is not None:
                    break
                time.sleep(self.heartbeat_interval_seconds)
        except Exception as exc:
            stdout_file.flush()
            stderr_file.flush()
            self._write_status(
                self._status_payload(
                    state="failed",
                    file_path=file_path,
                    output_dir=output_dir,
                    command=command,
                    pid=process.pid if process is not None else None,
                    returncode=returncode,
                    started_at=started_at,
                    finished_at=_utcnow(),
                    stdout_tail=_read_text_tail(stdout_path),
                    stderr_tail=_read_text_tail(stderr_path) or str(exc),
                )
            )
            raise
        finally:
            stdout_file.close()
            stderr_file.close()

        stdout_tail = _read_text_tail(stdout_path)
        stderr_tail = _read_text_tail(stderr_path)
        try:
            stdout_path.unlink(missing_ok=True)
            stderr_path.unlink(missing_ok=True)
        except OSError:
            pass
        if returncode != 0:
            detail = stderr_tail.strip() or stdout_tail.strip() or "unknown OCR execution failure"
            raise RuntimeError(f"Surya OCR execution failed: {detail}")

        direct_results = Path(output_dir) / "results.json"
        if direct_results.exists():
            return direct_results
        nested_results = next(Path(output_dir).rglob("results.json"), None)
        if nested_results is None:
            self._write_status(
                self._status_payload(
                    state="failed",
                    file_path=file_path,
                    output_dir=output_dir,
                    command=command,
                    pid=process.pid if process is not None else None,
                    returncode=returncode,
                    started_at=started_at,
                    finished_at=_utcnow(),
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail or "Surya OCR did not produce a results.json output.",
                )
            )
            raise RuntimeError("Surya OCR did not produce a results.json output.")
        self._write_status(
            self._status_payload(
                state="succeeded",
                file_path=file_path,
                output_dir=output_dir,
                command=command,
                pid=process.pid,
                returncode=returncode,
                started_at=started_at,
                finished_at=_utcnow(),
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
        )
        return nested_results


@dataclass(slots=True, frozen=True)
class _OcrLine:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float


class OcrPdfTextExtractor:
    def __init__(self, runner: OcrCommandRunner | None = None, chunk_page_count: int | None = None):
        self.runner = runner or UvSuryaOcrRunner()
        self.chunk_page_count = max(
            int(chunk_page_count or os.getenv("BOOK_AGENT_OCR_CHUNK_PAGE_COUNT") or 32),
            1,
        )

    def extract(self, file_path: str | Path, *, page_count: int | None = None) -> PdfExtraction:
        resolved_path = Path(file_path).resolve()
        payload, chunk_metadata = self._extract_payload(
            resolved_path,
            page_count=page_count,
        )

        page_payloads = self._page_payloads(payload, resolved_path)
        pages: list[PdfPage] = []
        for page_number, page_payload in enumerate(page_payloads, start=1):
            lines = self._page_lines(page_payload)
            image_bbox = self._coerce_bbox(page_payload.get("image_bbox"))
            width = image_bbox[2] if image_bbox is not None else 1000.0
            height = image_bbox[3] if image_bbox is not None else 1400.0
            pages.append(
                PdfPage(
                    page_number=page_number,
                    width=width,
                    height=height,
                    blocks=self._group_lines_into_blocks(
                        page_number=page_number,
                        page_width=width,
                        lines=lines,
                    ),
                    image_blocks=[],
                )
            )

        return PdfExtraction(
            title=None,
            author=None,
            metadata={
                "pdf_extractor": "surya_ocr",
                "ocr_engine": "surya_ocr",
                "ocr_page_count": len(pages),
                **chunk_metadata,
            },
            pages=pages,
            outline_entries=[],
        )

    def _extract_payload(
        self,
        file_path: Path,
        *,
        page_count: int | None,
    ) -> tuple[Any, dict[str, Any]]:
        if self._should_chunk(page_count):
            return self._extract_payload_chunked(file_path, page_count=page_count or 0)
        return self._extract_payload_single(file_path), {
            "ocr_chunk_count": 1,
            "ocr_chunk_page_count": None,
        }

    def _extract_payload_single(self, file_path: Path) -> Any:
        with tempfile.TemporaryDirectory(prefix="book-agent-ocr-") as temp_dir:
            results_path = self.runner.run(file_path=file_path, output_dir=temp_dir)
            return json.loads(results_path.read_text(encoding="utf-8"))

    def _should_chunk(self, page_count: int | None) -> bool:
        return bool(
            isinstance(self.runner, UvSuryaOcrRunner)
            and self.runner.page_range is None
            and page_count is not None
            and page_count > self.chunk_page_count
        )

    def _extract_payload_chunked(self, file_path: Path, *, page_count: int) -> tuple[Any, dict[str, Any]]:
        assert isinstance(self.runner, UvSuryaOcrRunner)
        combined_page_payloads: list[dict[str, Any]] = []
        chunk_ranges = list(self._chunk_ranges(page_count))
        with tempfile.TemporaryDirectory(prefix="book-agent-ocr-") as temp_dir:
            temp_root = Path(temp_dir)
            for chunk_index, (start_page, end_page) in enumerate(chunk_ranges, start=1):
                chunk_runner = replace(
                    self.runner,
                    page_range=f"{start_page}-{end_page}",
                )
                chunk_output_dir = temp_root / f"chunk-{chunk_index:03d}"
                chunk_output_dir.mkdir(parents=True, exist_ok=True)
                results_path = chunk_runner.run(file_path=file_path, output_dir=chunk_output_dir)
                payload = json.loads(results_path.read_text(encoding="utf-8"))
                combined_page_payloads.extend(self._page_payloads(payload, file_path))
        return (
            {file_path.stem: combined_page_payloads},
            {
                "ocr_chunk_count": len(chunk_ranges),
                "ocr_chunk_page_count": self.chunk_page_count,
            },
        )

    def _chunk_ranges(self, page_count: int) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        start_page = 0
        while start_page < page_count:
            end_page = min(page_count - 1, start_page + self.chunk_page_count - 1)
            ranges.append((start_page, end_page))
            start_page = end_page + 1
        return ranges

    def _page_payloads(self, payload: Any, file_path: Path) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise RuntimeError("Surya OCR returned an unexpected results payload.")
        stem_candidates = [
            file_path.stem,
            file_path.name,
        ]
        for candidate in stem_candidates:
            page_payloads = payload.get(candidate)
            if isinstance(page_payloads, list):
                return [item for item in page_payloads if isinstance(item, dict)]
        first_list = next((value for value in payload.values() if isinstance(value, list)), None)
        if first_list is None:
            raise RuntimeError("Surya OCR results did not include page-level output.")
        return [item for item in first_list if isinstance(item, dict)]

    def _page_lines(self, page_payload: dict[str, Any]) -> list[_OcrLine]:
        raw_lines = page_payload.get("text_lines")
        if not isinstance(raw_lines, list):
            return []
        lines: list[_OcrLine] = []
        for raw_line in raw_lines:
            if not isinstance(raw_line, dict):
                continue
            text = self._normalize_ocr_text(str(raw_line.get("text") or ""))
            bbox = self._coerce_bbox(raw_line.get("bbox"))
            if not text or bbox is None:
                continue
            confidence = float(raw_line.get("confidence", 0.0) or 0.0)
            lines.append(_OcrLine(text=text, bbox=bbox, confidence=confidence))
        return sorted(lines, key=lambda item: (round(item.bbox[1], 2), round(item.bbox[0], 2)))

    def _normalize_ocr_text(self, text: str) -> str:
        return _normalize_text(_OCR_INLINE_TAG_PATTERN.sub("", text or ""))

    def _group_lines_into_blocks(
        self,
        *,
        page_number: int,
        page_width: float,
        lines: list[_OcrLine],
    ) -> list[PdfTextBlock]:
        if not lines:
            return []

        line_heights = [max(line.bbox[3] - line.bbox[1], 1.0) for line in lines]
        median_height = median(line_heights) if line_heights else 12.0
        blocks: list[list[_OcrLine]] = []
        current_block: list[_OcrLine] = []
        terminal_punctuation = (".", "!", "?", ":", ";")

        def flush_current() -> None:
            nonlocal current_block
            if current_block:
                blocks.append(current_block)
                current_block = []

        for line in lines:
            if self._looks_like_standalone_heading(line, page_width=page_width, median_height=median_height):
                flush_current()
                blocks.append([line])
                continue
            if not current_block:
                current_block = [line]
                continue

            previous = current_block[-1]
            vertical_gap = line.bbox[1] - previous.bbox[3]
            left_shift = abs(line.bbox[0] - previous.bbox[0])
            paragraph_gap = vertical_gap > max(median_height * 1.15, 14.0)
            indent_shift = (
                left_shift > max(page_width * 0.08, median_height * 1.6)
                and previous.text.rstrip().endswith(terminal_punctuation)
            )
            if paragraph_gap or indent_shift:
                flush_current()
                current_block = [line]
                continue
            current_block.append(line)

        flush_current()
        return [
            self._pdf_text_block_from_lines(page_number=page_number, block_number=index, lines=block_lines)
            for index, block_lines in enumerate(blocks, start=1)
        ]

    def _looks_like_standalone_heading(
        self,
        line: _OcrLine,
        *,
        page_width: float,
        median_height: float,
    ) -> bool:
        text = _normalize_text(line.text)
        if not text or len(text) > 120:
            return False
        line_height = max(line.bbox[3] - line.bbox[1], 1.0)
        line_width = max(line.bbox[2] - line.bbox[0], 1.0)
        center_offset = abs(((line.bbox[0] + line.bbox[2]) / 2.0) - (page_width / 2.0))
        centered = center_offset <= page_width * 0.14
        explicit_heading = bool(
            re.match(r"^(chapter|part|appendix)\b", text, re.IGNORECASE)
            or re.match(r"^\d+(?:\.\d+){0,2}\s+[A-Z]", text)
        )
        visual_heading = (
            line_height >= median_height * 1.2
            and centered
            and line_width <= page_width * 0.82
            and len(text.split()) <= 16
        )
        return explicit_heading or visual_heading

    def _pdf_text_block_from_lines(
        self,
        *,
        page_number: int,
        block_number: int,
        lines: list[_OcrLine],
    ) -> PdfTextBlock:
        text = _normalize_multiline_text("\n".join(line.text for line in lines))
        left = min(line.bbox[0] for line in lines)
        top = min(line.bbox[1] for line in lines)
        right = max(line.bbox[2] for line in lines)
        bottom = max(line.bbox[3] for line in lines)
        font_sizes = [max(line.bbox[3] - line.bbox[1], 1.0) for line in lines]
        span_count = sum(max(len(line.text.split()), 1) for line in lines)
        return PdfTextBlock(
            page_number=page_number,
            block_number=block_number,
            text=text,
            bbox=(left, top, right, bottom),
            line_texts=[line.text for line in lines],
            span_count=span_count,
            line_count=len(lines),
            font_size_min=min(font_sizes),
            font_size_max=max(font_sizes),
            font_size_avg=sum(font_sizes) / len(font_sizes),
        )

    def _coerce_bbox(self, payload: Any) -> tuple[float, float, float, float] | None:
        if not isinstance(payload, list) or len(payload) != 4:
            return None
        try:
            return tuple(float(value) for value in payload)
        except (TypeError, ValueError):
            return None


class OcrPdfParser:
    def __init__(
        self,
        extractor: OcrPdfTextExtractor | None = None,
        recovery_service: PdfStructureRecoveryService | None = None,
    ):
        self.extractor = extractor or OcrPdfTextExtractor()
        self.recovery_service = recovery_service or PdfStructureRecoveryService()

    def parse(
        self,
        file_path: str | Path,
        profile: PdfFileProfile | dict[str, Any] | None = None,
    ) -> ParsedDocument:
        if isinstance(profile, PdfFileProfile):
            effective_profile = profile
            extraction = self.extractor.extract(file_path, page_count=effective_profile.page_count or None)
        elif isinstance(profile, dict):
            effective_profile = PdfFileProfile.from_dict(profile)
            extraction = self.extractor.extract(file_path, page_count=effective_profile.page_count or None)
        else:
            extraction = self.extractor.extract(file_path)
            effective_profile = PdfFileProfile(
                pdf_kind="scanned_pdf",
                page_count=len(extraction.pages),
                has_extractable_text=False,
                outline_present=False,
                layout_risk="high",
                ocr_required=False,
                extractor_kind="surya_ocr",
            )
        return self.recovery_service.recover(file_path, extraction, effective_profile)
