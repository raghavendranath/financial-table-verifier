# Source-only study companion — prepared locally for release

This companion contains the fixed 50-report cohort manifest, the actual generated guidance for 48 reports, and the source-cell/accounting reference populations used by the reconstruction. It is prepared locally for review; this directory has not been published. The public software repository alone does not establish public availability of these data.

## Contents

- `cohort.csv`: all 50 document IDs, CIKs, accessions, filing links, exact PDF hashes, page counts, selected physical pages, and the 10 development / 40 additional split.
- `preparation-status.json`: 48 available prompt/schema pairs and two explicit preparation blocks. Abacus document `1820302-0001628280-24-047906` has no complete generated CSV guidance; `SALESFORCE_2023_10K` was blocked in source preparation. Blocked reports remain in the study denominator; no guidance has been fabricated for them.
- `prompts/`: the actual generated filing-specific prompt and schema JSON, not generic templates. Each prompt's schema hash is checked against canonical JSON serialization of the source schema (which differs from the pretty-printed file byte hash).
- `references/`: 11 canonical source-reference populations, including BS/IS, EPS/RE, page/table inventories, the physical-cell map and complete inventory. The source reference counts are 2,938 BS/IS numeric targets and 508 EPS/RE numeric targets; their overlap is 64, leaving 3,382 distinct numeric cells plus 96 markers. These files retain source cases, selectors and review-status limitations exactly as recorded.
- `file-inventory.json`: canonical original and released SHA-256/size for every payload, with explicit transformation details. The inventory does not hash itself.
- `relocation-map.json`: stable source-reference IDs and mappings for included files.

## Reference preparation

Source annotations were prepared and checked in separate Codex agent tasks. Exact underlying agent model identifiers and independent pre-adjudication agreement were not retained. A limited author audit used previously familiar cases and is not a blinded gold standard. The full 50-report reference set must not be described as independently human annotated.

## PDF provenance and scope

Local custody was 23 PDFs in a FinanceBench-labelled cache and 27 in an SEC-indexed cache. These labels do not recover the original download or PDF creation/conversion history. Forty-nine inputs were retained from the initial collection. A locally existing 3M 2023Q2 10-Q replaced an excluded Lockheed earnings release; no fresh download is established by that replacement record. Matched SEC filing HTML/index URLs identify filings and are not asserted to be direct PDF download URLs.

All 50 accessions are retained. Four reports lack an independent exact-accession match in the cached recent-submissions list; this is explicit in the CSV, while their retained exact-accession companyfacts/PDF binding supports the recorded accession. Unknown filing dates are not invented. Company labels follow the frozen manifest; CIK, accession, printed report and PDF hash establish the binding. The additional 40 are not an untouched holdout: known historical pilot exposure is disclosed, and other prior exposure is not asserted absent.

## Relocation and verification

Only the private local project-root path prefix in JSON strings/keys is replaced by `study://workspace/`. Every other JSON lexeme, financial number, source label, selector and embedded canonical hash is preserved. The build verifies an exact inverse transformation and semantic equality for every transformed JSON file. No original source file was edited.

A `study://` reference is a provenance identifier, not an Internet URL. References to rendered pages, source PDFs or supporting artifacts not listed as included remain external. Original canonical hashes refer to pre-relocation bytes; released hashes authenticate the relocated files. Thus relocated JSON byte hashes may differ by design. Prompt/schema canonical bindings are retained, not silently rewritten.

## Use and limitations

Obtain the source PDFs separately and check their hashes against `cohort.csv`. This bundle intentionally contains no PDFs, images, paid model responses, human audit answers, billing records or credentials. It permits inspection of the fixed source references and guidance. Recomputing measured model recall also requires the corresponding model extraction records and scoring implementation; neither model responses nor the full private execution archive are included. Original private execution gates are not bypassed by the relocated companion. Reference review statuses and prior-exposure disclosures remain as recorded; these annotations must not be presented as an independent blinded human gold standard.

The source financial filings remain subject to their original terms. The software license does not automatically license third-party filing content. Review redistribution terms before publishing the companion. No study data have been uploaded by this preparation step.
