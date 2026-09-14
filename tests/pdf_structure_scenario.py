"""Parse every fixture PDF and snapshot the recovered document structure.

Characterization for the P3.3 split of ``domain/structure/pdf.py``: the PDF
writers from ``tests/test_pdf_support.py`` and the generators in
``tests/golden_pdfs/fixtures.py`` are parsed with the default ``PDFParser``
and the resulting ``ParsedDocument`` (chapters, blocks, anchors, types,
roles, metadata) is normalized for stable comparison.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tests.golden_pdfs.fixtures as golden_pdf_fixtures
import tests.test_pdf_support as pdf_support
from book_agent.domain.structure.pdf import PDFParser

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{32}\b|\b[0-9a-f]{40}\b")
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def fixture_writers() -> dict[str, Callable[[Path], None]]:
    writers: dict[str, Callable[[Path], None]] = {}
    for name, function in sorted(vars(pdf_support).items()):
        if name.startswith("_write_") and name.endswith("_pdf") and inspect.isfunction(function):
            if list(inspect.signature(function).parameters) == ["path"]:
                writers[name.removeprefix("_write_").removesuffix("_pdf")] = function
    for name, function in sorted(vars(golden_pdf_fixtures).items()):
        if name.startswith("make_") and inspect.isfunction(function):

            def write(path: Path, _make=function) -> None:
                path.write_bytes(_make())

            writers[f"golden_{name.removeprefix('make_')}"] = write
    return writers


def parse_fixture(writer: Callable[[Path], None], root: Path) -> Any:
    pdf_path = root / "fixture.pdf"
    writer(pdf_path)
    try:
        return PDFParser(image_output_dir=root / "images").parse(pdf_path)
    except Exception as exc:  # noqa: BLE001 - the failure itself is characterized
        return {"error": type(exc).__name__, "message": str(exc)}


def normalize(value: Any, root: Path) -> Any:
    ids: dict[str, str] = {}
    roots = sorted({str(root.resolve()), str(root)}, key=len, reverse=True)

    def text(item: str) -> str:
        for prefix in roots:
            item = item.replace(prefix, "<root>")
        item = _ISO_TIMESTAMP.sub("<ts>", item)
        return _UUID.sub(lambda match: ids.setdefault(match.group(0), f"<id{len(ids) + 1}>"), item)

    def walk(item: Any) -> Any:
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            return {field.name: walk(getattr(item, field.name)) for field in dataclasses.fields(item)}
        if isinstance(item, dict):
            return {text(str(key)): walk(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(val) for val in item]
        if isinstance(item, float):
            return round(item, 4)
        if isinstance(item, str):
            return text(item)
        if isinstance(item, Path):
            return text(str(item))
        if item is None or isinstance(item, (bool, int)):
            return item
        return text(repr(item))

    return walk(value)
