# Paper code and reproducibility

The primary study extends sequential image/CSV extraction to 50 corporate reports.
Its original records include failures and partial outputs; rerunning a paid model is
not required to inspect the recorded results.

## Preserved source

`financial_table_verifier/paper_runtime/work/` retains the original relative layout:

- `reconstruction-v1/csv-first-v1/src/ftv_csv_reconstruction/`: active generated-schema
  CSV preparation, literal adjacent-page RAW context, row classification and replay.
- `reconstruction-v1/implementations/pipeline-v6-f255fad7/`: the frozen survey/structure
  dependency and the files its preservation receipt checks.
- `reconstruction-v1/csv-evaluation-v1/`: unchanged numeric, date, request-provenance
  and bootstrap helpers used by the current evaluation.
- `reconstruction-expansion-v1/evaluation-v1/`: actual full50 structural binding,
  source compiler, rounding-aware accounting screens, paired-cluster statistics,
  request provenance and original final-report driver, with their tests.

The active CSV procedure is
`59d762e497c652a2bc0cc770fa6a463bfe25ea86080fffec99f466b8e375cac4`.
No copied scientific source was edited to make it portable. `paper.py` is a separate
adapter that checks the source manifest and sets only import paths.

## Supported offline workflows

`ftv paper-demo` executes the frozen primary CSV functions with canned synthetic
survey/structure/schema/prompt/extraction artifacts. It then replays a CSV page and
uses the actual full50 source compiler/scorer on a tiny synthetic source population.

`ftv paper-tests` executes the portable frozen CSV and scoring test suites with network
connections blocked. One test that requires the separately archived full-cohort source
contract is explicitly listed as excluded; it is not reported as a passing test.
Optional official HTML/TEDS tests are not part of this primary-CSV command.

`ftv paper-score` requires a JSON object with:

```json
{
  "schema": "FTV-portable-paper-score-input-v1",
  "synthetic": false,
  "source_populations": {},
  "physical_map": {"physical_cells": []},
  "table_inventory": {"pages": []},
  "records": []
}
```

Use the nonempty demo input as a worked example of the required selectors and source
fields. Source values must be annotated before new primary predictions; the binder
uses structural labels/columns, not the expected amount, to choose a match. Accounting
checks remain conditioned on source eligibility and available operands. Extra model
values do not turn selected-target recall into complete-output precision.

## Original full-cohort provenance replay

The code repository is not the full study archive. The original `report_full50.py`
deliberately verifies absolute config/reference paths, prospective freeze fingerprints,
all 300 terminal slots and closed owner/lease evidence. Copying it into a fresh folder
does not authorize bypassing those checks. The archive preserves the original path-bound
manifests and all source/request/response evidence for controlled replay. The portable
scorer does not claim that its caller-supplied records passed those original gates.

`paper-inspect` checks the pinned final report's bytes and recounts saved numeric cells.
The original independently reviewed source/statistics verification remains in the
separate paper archive. It is not recreated by simply loading the saved report.

## CSV runtime API

The runtime's stages are available to an operator supplying a trusted, separately
budgeted transport. There is no default paid callback:

```python
from financial_table_verifier.paper import study_code

with study_code():
    from ftv_csv_reconstruction.contracts import CSVConfig
    from ftv_csv_reconstruction.preparation import prepare_document
    from ftv_csv_reconstruction.pipeline import extract_document
    # cfg = CSVConfig.model_validate(...)
    # transport = your_explicit_budgeted_transport
    # prepared = prepare_document(cfg, transport)
    # output = extract_document(cfg, prepared, transport)
```

CSV preparation validates supplied survey/structure artifacts against its preserved
earlier stage. All inputs must have matching hashes. A new paid study needs its own
reviewed cohort, reference freeze, generation controls, budget and failure policy.

## Interpretation

The paper reports 37 reports with six completed pipelines, 11 with partial pipelines
and two preparation blocks: 264 complete, 24 partial and 12 preparation-blocked slots.
Completion is not universal page validity. All selected targets remain in denominators.
Accounting passes, selected-number recall and complete hierarchy accuracy are distinct.
The observed sequential increases do not establish a significant general context
benefit after the planned correction. No original 84.6% reproduction or token-saving
claim follows from this code release.
