"""Read-only adapters for the user's existing LMDX filing indexes."""

from __future__ import annotations

from pathlib import Path

from .render import page_count
from .schema import Manifest
from .storage import file_hash, read_json, write_json


def import_index(
    index: Path, source_root: Path, output: Path, limit: int = 3, window: int = 1
) -> dict:
    """Convert verified zero-based LMDX page pointers into a PILOT manifest.

    This imports locations/provenance only. Existing field-level labels are not
    silently promoted to table/hierarchy gold. Paths remain references to originals.
    """
    if limit < 1 or not 0 <= window <= 3:
        raise ValueError("Use a positive document limit and adjacent window from 0 to 3")
    if output.exists():
        raise ValueError("Refusing to overwrite existing manifest")
    source_root = source_root.resolve()
    index = index.resolve()
    payload = read_json(index)
    filings = payload["filings"]

    # Prefer explicitly adjacent indexed pages for a continuation pilot, without claiming
    # the heuristic has verified that they are fragments of the same table.
    def has_adjacent(filing):
        return any(
            any(b == a + 1 for a, b in zip(pages, pages[1:], strict=False))
            for pages in filing["pages"].values()
        )

    filings = sorted(filings, key=lambda f: (not has_adjacent(f), f["doc_id"]))
    selected, provenance = [], []
    for filing in filings:
        if len(selected) >= limit:
            break
        pdf = (source_root / filing["pdf"]).resolve()
        if not pdf.is_relative_to(source_root):
            raise ValueError("Index PDF path escapes source root")
        if not pdf.is_file():
            continue
        count = page_count(pdf)
        indexed = sorted({n for ns in filing["pages"].values() for n in ns})
        if not indexed or any(not isinstance(n, int) or not 0 <= n < count for n in indexed):
            raise ValueError("Source index has missing or invalid zero-based page pointers")
        pages = sorted(
            {
                n + delta + 1
                for n in indexed
                for delta in range(-window, window + 1)
                if 0 <= n + delta < count
            }
        )
        selected.append(
            {
                "id": filing["doc_id"],
                "pdf": str(pdf),
                "pages": pages,
                "split": "pilot",
                "source_url": filing.get("url"),
                "accession": filing.get("accession") or filing.get("xbrl_accession"),
                "eps_eligible_tables": [],
            }
        )
        provenance.append(
            {
                "id": filing["doc_id"],
                "pdf_sha256": file_hash(pdf),
                "source_zero_based_pages": filing["pages"],
                "physical_pages": pages,
                "source_field_labels": list(filing.get("gold", {})),
                "reuse_history": "unknown",
                "continuation_candidate": has_adjacent(filing),
                "review_required": "Confirm pages, continuation, units and labels visually",
            }
        )
    if not selected:
        raise ValueError("No usable PDFs found in source index")
    manifest = Manifest.model_validate({"documents": selected})
    write_json(output, manifest.model_dump(mode="json"))
    report = {
        "index": str(index),
        "index_sha256": file_hash(index),
        "page_conversion": "source zero-based n -> physical PDF page n+1",
        "selected_documents": provenance,
        "note": "Pilot only. Unknown prior exposure; no hierarchy/reference labels imported.",
    }
    write_json(output.with_suffix(".provenance.json"), report)
    return report
