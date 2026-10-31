"""Assemble a new, explicitly versioned cohort from existing local sources only."""

from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from .storage import file_hash, read_json, write_json


def build_cohort(root: Path, output: Path, per_form: int = 25, seed: int = 42) -> dict:
    if per_form < 1:
        raise ValueError("per_form must be positive")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Cohort output must be empty or absent")
    root, output = root.resolve(), output.resolve()
    data = root / "data"
    sec = data / "sec"
    current = [sec / "index.json", sec / "financebench_index.json"]
    indexes = current + [p for p in sorted(sec.glob("*index*.json")) if p not in current]
    records = {}
    index_sources = []
    for path in indexes:
        if not path.exists():
            continue
        index_sources.append({"path": str(path), "sha256": file_hash(path)})
        for filing in read_json(path).get("filings", []):
            accession = filing.get("accession") or filing.get("xbrl_accession")
            if accession and accession not in records:
                records[accession] = {**filing, "metadata_source": str(path)}

    def source_path(kind: str, cik: int) -> Path | None:
        dedicated = (
            data
            / "xbrl"
            / (
                f"companyfacts/CIK{cik:010d}.json"
                if kind == "companyfacts"
                else f"submissions_CIK{cik:010d}.json"
            )
        )
        cache = (
            sec
            / "_http_cache"
            / (
                f"data.sec.gov_api_xbrl_companyfacts_CIK{cik:010d}.json"
                if kind == "companyfacts"
                else f"data.sec.gov_submissions_CIK{cik:010d}.json"
            )
        )
        return next((p for p in (dedicated, cache) if p.exists()), None)

    fact_cache, submission_cache = {}, {}

    def facts_for(cik: int) -> tuple[dict, Path | None]:
        if cik not in fact_cache:
            path = source_path("companyfacts", cik)
            fact_cache[cik] = (read_json(path) if path else {}, path)
        return fact_cache[cik]

    def submissions_for(cik: int) -> tuple[dict, Path | None]:
        if cik not in submission_cache:
            path = source_path("submissions", cik)
            submission_cache[cik] = (read_json(path) if path else {}, path)
        return submission_cache[cik]

    def accession_facts(cik: int, accession: str) -> list[dict]:
        payload, _ = facts_for(cik)
        if int(payload.get("cik", -1)) != cik:
            return []
        return [
            entry
            for concepts in payload.get("facts", {}).values()
            for concept in concepts.values()
            for entries in concept.get("units", {}).values()
            for entry in entries
            if entry.get("accn") == accession
        ]

    # Recover locally present PDFs omitted by the previous extraction index. Do not
    # reuse its exclusions or labels as the eligibility decision for this new study.
    companies_path = root / "configs" / "financebench_companies.csv"
    company_map = {}
    if companies_path.exists():
        with companies_path.open(encoding="utf-8") as stream:
            company_map = {
                row["company"].casefold(): int(row["cik"]) for row in csv.DictReader(stream)
            }
    metadata_path = data / "financebench/data/financebench_document_information.jsonl"
    recovered = []
    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            metadata = json.loads(line)
            form = {"10k": "10-K", "10q": "10-Q"}.get(metadata["doc_type"].lower())
            if form != "10-K" or metadata["doc_period"] not in {2023, 2024}:
                continue
            pdf = data / "financebench/pdfs" / (metadata["doc_name"] + ".pdf")
            cik = company_map.get(metadata["company"].casefold())
            if not pdf.exists() or cik is None:
                continue
            submissions, _ = submissions_for(cik)
            recent = submissions.get("filings", {}).get("recent", {})
            matches = []
            for i, candidate_form in enumerate(recent.get("form", [])):
                if candidate_form != form:
                    continue
                accession = recent["accessionNumber"][i]
                entries = accession_facts(cik, accession)
                years = {e.get("fy") for e in entries if e.get("fy") is not None}
                if years == {metadata["doc_period"]}:
                    matches.append((accession, i))
            if len(matches) == 1 and matches[0][0] not in records:
                accession, i = matches[0]
                records[accession] = {
                    "doc_id": metadata["doc_name"],
                    "cik": cik,
                    "company": metadata["company"],
                    "form": form,
                    "report_date": recent["reportDate"][i],
                    "pdf": str(pdf),
                    "pages": {},
                    "metadata_source": str(metadata_path),
                    "issuer_pdf_url": metadata["doc_link"],
                }
                recovered.append(metadata["doc_name"])

    eligible, excluded = [], Counter()
    for accession, record in records.items():
        form = record["form"]
        if form not in {"10-K", "10-Q"}:
            excluded["not_10k_or_10q"] += 1
            continue
        pdf = (root / record["pdf"]).resolve()
        if not pdf.is_relative_to(root) or not pdf.is_file():
            excluded["missing_pdf_or_path_outside_source"] += 1
            continue
        cik = int(record["cik"])
        entries = accession_facts(cik, accession)
        years = {e.get("fy") for e in entries if e.get("fy") is not None}
        forms = {e.get("form") for e in entries if e.get("form") is not None}
        if len(years) != 1 or not years <= {2023, 2024} or forms != {form}:
            excluded["missing_conflicting_or_out_of_range_fiscal_year_or_form"] += 1
            continue
        submissions, submission_path = submissions_for(cik)
        if int(submissions.get("cik", -1)) != cik:
            excluded["missing_or_mismatched_submission_identity"] += 1
            continue
        recent = submissions.get("filings", {}).get("recent", {})
        matches = [i for i, acc in enumerate(recent.get("accessionNumber", [])) if acc == accession]
        if len(matches) > 1:
            excluded["duplicate_accession_in_cached_submission_history"] += 1
            continue
        i = matches[0] if matches else None
        if i is not None and recent["form"][i] != form:
            excluded["submission_form_conflict"] += 1
            continue
        if i is None and not record.get("report_date"):
            excluded["no_report_date_in_either_source"] += 1
            continue
        _, facts_path = facts_for(cik)
        sic = str(submissions.get("sic", ""))
        eligible.append(
            {
                "id": record["doc_id"],
                "cik": cik,
                "company": record["company"],
                "form": form,
                "accession": accession,
                "fiscal_year": next(iter(years)),
                "report_date": recent["reportDate"][i] if i is not None else record["report_date"],
                "filing_date": recent["filingDate"][i] if i is not None else None,
                "submission_accession_match": i is not None,
                "sic": sic,
                "sic_2digit": sic.zfill(4)[:2] if sic.isdigit() else "unknown",
                "pdf": str(pdf),
                "pdf_sha256": file_hash(pdf),
                "source_url": (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                    f"{accession.replace('-', '')}/"
                    + (recent["primaryDocument"][i] if i is not None else accession + "-index.html")
                ),
                "companyfacts": str(facts_path),
                "companyfacts_sha256": file_hash(facts_path),
                "submission_file": str(submission_path),
                "submission_sha256": file_hash(submission_path),
                "numeric_fact_records_for_accession": len(entries),
                "old_zero_based_pages": record.get("pages", {}),
                "metadata_source": record["metadata_source"],
                "prior_exposure": "unknown",
                "pdf_binding_review": "pending visual match of issuer, report, period and source",
                "statement_page_review": "pending; historical page pointers may be wrong",
                "hierarchy_reference": "not annotated",
            }
        )

    # Deduplicate both filing accession and byte-identical PDFs before sampling.
    unique, pdf_hashes = [], set()
    for item in sorted(eligible, key=lambda row: row["accession"]):
        if item["pdf_sha256"] in pdf_hashes:
            excluded["duplicate_pdf_hash"] += 1
            continue
        pdf_hashes.add(item["pdf_sha256"])
        unique.append(item)
    rng = random.Random(seed)
    selected = []
    for form in ("10-K", "10-Q"):
        strata = defaultdict(list)
        for item in unique:
            if item["form"] == form:
                strata[item["sic_2digit"]].append(item)
        names = sorted(strata)
        rng.shuffle(names)
        for rows in strata.values():
            rng.shuffle(rows)
        form_selected, issuers = [], set()
        while len(form_selected) < per_form:
            progress = False
            for name in names:
                while strata[name] and strata[name][-1]["cik"] in issuers:
                    strata[name].pop()
                if strata[name] and len(form_selected) < per_form:
                    item = strata[name].pop()
                    form_selected.append(item)
                    issuers.add(item["cik"])
                    progress = True
            if not progress:
                break
        selected.extend(form_selected)
    counts = Counter(item["form"] for item in selected)
    report = {
        "status": "candidate_cohort_ready_for_review"
        if all(counts[form] == per_form for form in ("10-K", "10-Q"))
        else "insufficient_local_candidates",
        "target": {"10-K": per_form, "10-Q": per_form, "fiscal_years": [2023, 2024]},
        "selected_counts": dict(counts),
        "eligible_counts": dict(Counter(r["form"] for r in unique)),
        "seed": seed,
        "sampling": "Round-robin across shuffled two-digit SIC strata; shuffled within stratum; "
        "at most one filing per issuer per form. Conditional on this local convenience pool.",
        "fiscal_year_evidence": "Unique fy and form on exact-accession cached SEC companyfacts; "
        "cached submission accession/date cross-check where available. "
        "Missing historical submission entries are explicitly flagged.",
        "original_experiment_reproduced": False,
        "manually_verified_dataset": False,
        "source_indexes": index_sources,
        "excluded": dict(excluded),
        "selected_missing_cached_submission_accession": [
            r["id"] for r in selected if not r["submission_accession_match"]
        ],
        "recovered_unindexed_pdfs": recovered,
        "documents": selected,
        "remaining": [
            "Review PDF-to-filing bindings and actual table pages",
            "Verify true continuations; annotate complete tables and hierarchy",
            "Review exact fact/period/unit mappings and EPS eligibility",
            "Prior exposure unknown; use as disclosed pilot/development material",
            "Run new experiments; do not reuse original unverified result numbers",
        ],
    }
    write_json(output / "cohort.json", report)
    write_json(output / "eligible-pool.json", {"documents": unique})
    with (output / "cohort.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = [
            "id",
            "company",
            "form",
            "cik",
            "accession",
            "fiscal_year",
            "report_date",
            "sic_2digit",
            "pdf",
            "companyfacts",
            "numeric_fact_records_for_accession",
            "prior_exposure",
            "statement_page_review",
            "hierarchy_reference",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(selected)
    return report
