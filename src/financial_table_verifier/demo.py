"""Explicitly synthetic, offline end-to-end smoke example. Not research evidence."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .evaluation import compare_runs, evaluate_run
from .pipeline import run_experiment
from .schema import Experiment, Manifest
from .storage import file_hash, write_json

MODEL = "synthetic/offline-fixture"


def make_pdf(path: Path) -> None:
    """Write a tiny two-page PDF using only standard PDF primitives."""
    streams = [
        [
            "SYNTHETIC DEMO - NOT AN SEC FILING",
            "Demo Company - Consolidated Balance Sheet",
            "USD, amounts in thousands | December 31, 2024",
            "Assets",
            "Total assets     100",
        ],
        [
            "SYNTHETIC DEMO - NOT AN SEC FILING",
            "Consolidated Balance Sheet - continued",
            "USD, amounts in thousands | December 31, 2024",
            "Liabilities and equity",
            "Total liabilities     60",
            "Total equity     40",
        ],
    ]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R 6 0 R] /Count 2 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for number, lines in enumerate(streams):
        content_id = 5 + number * 2
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        text = "BT /F1 12 Tf 50 740 Td 22 TL "
        for index, line in enumerate(lines):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            text += ("T* " if index else "") + f"({escaped}) Tj "
        content = (text + "ET").encode()
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    )
    path.write_bytes(output)


def fixture_pages() -> list[dict]:
    pages = []
    specs = [
        [("Total assets", "total_assets", "100")],
        [("Total liabilities", "total_liabilities", "60"), ("Total equity", "total_equity", "40")],
    ]
    for number, rows in enumerate(specs, 1):
        pages.append(
            {
                "page": number,
                "tables": [
                    {
                        "table_index": 0,
                        "title": "Consolidated Balance Sheet",
                        "statement": "balance_sheet",
                        "scope": "consolidated",
                        "continuation_of": "p1-t0" if number == 2 else None,
                        "columns": [
                            {
                                "column_index": 0,
                                "label": "December 31, 2024",
                                "period": "2024-12-31",
                            }
                        ],
                        "rows": [
                            {
                                "row_index": i,
                                "label": label,
                                "path": [
                                    "Assets"
                                    if concept == "total_assets"
                                    else "Liabilities and equity"
                                ],
                                "role": "total",
                                "concept": concept,
                                "cells": [
                                    {
                                        "column_index": 0,
                                        "raw_value": raw,
                                        "quantity": "money",
                                        "scale": 1000,
                                        "currency": "USD",
                                    }
                                ],
                            }
                            for i, (label, concept, raw) in enumerate(rows)
                        ],
                    }
                ],
            }
        )
    return pages


class FixtureTransport:
    """Returns canned transcriptions; does NOT inspect images or emulate model quality."""

    def __init__(self):
        self.calls = 0

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if method == "GET":
            return {
                "data": [
                    {
                        "id": MODEL,
                        "architecture": {"input_modalities": ["image", "text"]},
                        "supported_parameters": ["response_format", "temperature", "seed"],
                    }
                ]
            }
        self.calls += 1
        text = payload["messages"][1]["content"][0]["text"]
        number = int(re.search(r"physical PDF page (\d+)", text).group(1))
        page = copy.deepcopy(fixture_pages()[number - 1])
        if "UNTRUSTED_PREVIOUS_PAGE_JSON" not in text:
            page["tables"][0]["continuation_of"] = None
        return {
            "id": f"synthetic-{self.calls}",
            "model": MODEL,
            "provider": "offline-fixture",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(page)}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0},
        }


def demo(directory: Path) -> dict:
    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Demo directory must be empty or absent")
    directory.mkdir(parents=True, exist_ok=True)
    pdf = directory / "synthetic.pdf"
    make_pdf(pdf)
    manifest = Manifest.model_validate(
        {
            "documents": [
                {
                    "id": "synthetic",
                    "pdf": str(pdf),
                    "pages": [1, 2],
                    "split": "pilot",
                }
            ]
        }
    )
    write_json(directory / "manifest.json", manifest.model_dump(mode="json"))
    gold_dir = directory / "gold"
    write_json(
        gold_dir / "synthetic.json",
        {
            "document_id": "synthetic",
            "pdf_sha256": file_hash(pdf),
            "annotator": "SYNTHETIC FIXTURE - NOT HUMAN RESEARCH ANNOTATION",
            "reviewed": True,
            "pages": fixture_pages(),
        },
    )
    for context in ("independent", "sequential"):
        config = Experiment(
            model=MODEL,
            context=context,
            dpi=100,
            manifest=directory / "manifest.json",
            output=directory / context,
        )
        run_experiment(config, manifest, transport=FixtureTransport())
        evaluate_run(config.output, gold_dir)
    comparison = compare_runs(directory / "independent", directory / "sequential")
    write_json(directory / "comparison.json", comparison)
    return {
        "directory": str(directory),
        "synthetic": True,
        "note": "Offline plumbing demonstration only. No model performance was measured.",
    }
