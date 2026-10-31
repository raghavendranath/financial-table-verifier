New staged reconstruction — development v0.0.1

UPDATE: The actual transport-injected staged pipeline is now implemented and tested.
See RUNTIME.txt for the current API, CLI, artifacts and freeze. The prerequisite
notes below describe the original offline scaffold; orchestration is now implemented.
Live execution and additive billing are managed separately by the parent runtime.

Purpose and boundary
This isolated package prepares a NEW separately preregistered reconstruction.
It does not recover or validate the submitted manuscript's original results.
It neither imports nor edits the historical fixed-schema extractor, frozen study,
references, raw calls, aggregate scores, release files or archives. It has no
default network transport. It includes actual staged orchestration plus tested
contracts/adapters/checkers. The official TEDS scorer is managed separately.
Parent-owned protocol and budget files live outside this development directory.

What is implemented
1. SourceGrid captures every physical label/numeric/header/blank cell, actual
   rowspan/colspan, row/column coordinates, normalized physical boxes, head/body
   row groups, PDF/image hashes, page, DPI, and source cell IDs. Bounding boxes may
   be null and are never invented. Strict checks
   reject missing blank slots, span overlap, spans beyond the grid, duplicate IDs,
   cell boxes outside the table, and rowspans crossing head/body boundaries.
   A span specifies covered slots, not repeated copies of a cell.
2. Source-only LayoutReference requires named annotation, reviewer, reviewed=true
   and prediction_blind=true at scoring admission. Agent-assisted/human review kind
   and blindness before NEW reconstruction outputs are explicitly distinguished;
   agent attestations do not imply independent human review.
3. grid_html preserves observed spans/blank cells/text in PubTabNet-style HTML.
   teds_inputs checks source identity and produces an HTML pair with score=null.
   It does NOT calculate a homemade score or relabel a hierarchy proxy as TEDS.
   Historical logical JSON cannot be converted because it never captured spans,
   physical label/header/blank columns or geometry. The adapter rejects that path.
4. StageManifest verifies preserved structure -> schema -> generated_prompt ->
   raw_extraction -> audited_cleaning order, input/output hash lineage and actual
   request/raw response artifact bytes. Full-document reconnaissance requires
   coverage of every physical page; sampling is not silently called full review.
   Example/evaluation issuers cannot overlap. Generated schema validation and
   safety review are explicit gates. This verifies declared artifacts, not that
   the LLM performed every semantic instruction or that reviewer claims are true.
5. Cleaning audit allows only explicitly logged role/path classification and
   column relocation of stable source cells. Every edit needs source evidence.
   It rejects changed, removed or added values/totals/labels/scales/periods/
   currency/scope, row/cell reordering, changed ownership, duplicate cell IDs,
   invalid classifications, duplicate/undeclared columns and omitted edit logs.
   The generic raw interchange must use tables[].columns[].column_index,
   tables[].rows[].{role,path,cells}, cells[].{source_cell_id,column_index,...}.
   All other source fields are immutable. No accounting-driven repair exists.
6. EPSLedger requires reviewed simple capital structure, explicit basis-matched
   common-holder numerator, actual weighted-average shares, reported EPS, exact
   entity/interval, explicit money/share scales, currency and rounding tolerance.
   Basic numerator cannot stand in for diluted adjusted numerator; ending shares
   cannot stand in for weighted-average shares. Complex cases are skipped from
   the faithful-original primary endpoint, not quietly added as a new extension.
7. RetainedEarningsLedger requires opening/closing retained earnings, exactly one
   net-income and explicit aggregate-dividend movement OR source-reviewed absence
   of dividends (not fabricated zero), all other movements,
   full/other-movement completeness review with source evidence, scope/currency/
   interval, and explicit signs/tolerance. Missing dividends are not zero. Signed
   losses remain losses; other stock-plan effects cannot be silently discarded.
   A synthetic regression reproduces Q2 parent-shareholder retained earnings,
   47966-6841-828-7=40290, opening3/31 to6/30. It is NOT a new
   reviewed source reference or an evaluated model prediction.
8. Every ledger candidate remains in the denominator report; skips carry reasons,
   and duplicate case IDs reject denominator inflation. Arithmetic consistency
   remains distinct from source transcription correctness.

Mixed units and currencies
Explicit supported scales can differ and are normalized exactly with Decimal
(e.g. USD millions / shares thousands). Unknown scales skip. Currency mismatches
skip; no currency conversion occurs. Unsupported/ambiguous number strings skip;
no footnote stripping, missing-value zero inference or correction occurs.

Run with the existing project runtime, without installing or modifying packages
From the workspace root:
  PYTHONPATH=work/reconstruction-v1/development/src \
    /Users/raghav/Desktop/Workspace/financial-table-verifier/.venv/bin/python -B \
    -m unittest discover -s work/reconstruction-v1/development/tests -v

Inspect complete JSON schemas:
  PYTHONPATH=work/reconstruction-v1/development/src \
    /Users/raghav/Desktop/Workspace/financial-table-verifier/.venv/bin/python -B \
    -m ftv_reconstruction schemas

Other offline commands, same PYTHONPATH/runtime:
  -m ftv_reconstruction eps ledger.json
  -m ftv_reconstruction retained-earnings ledger.json
    Both take {"cases":[...]} and emit all pass/fail/skip entries.
  -m ftv_reconstruction teds-inputs pair.json
    Takes {"reference":LayoutReference,"prediction":SourceGrid}.
  -m ftv_reconstruction stages manifest.json --root NEW_RUN_DIRECTORY
  -m ftv_reconstruction cleaning audit.json
    Takes {"before":RAW_JSON,"after":CLEANED_JSON,"edits":[CleaningEdit,...]}.
Commands read inputs and print JSON; they do not write into experiment directories.

Remaining prerequisites before ANY new evaluation or model request
1. Freeze a new source-based evaluation population and table/page inclusion rules
   without consulting scores; identify previously exposed issuers honestly.
   Original manuscript files do not contain recoverable original ten-filing layout
   gold or original fifty-filing experiment records. Do not invent their identities.
2. Annotate physical source grids from actual selected page images including row-
   label columns, all numeric/header/blank cells, spans and table boundaries. Review
   independently before predictions; retain disagreements and instruction hashes.
   No present synthetic fixture is human annotation and no real reference is
   automatically marked reviewed. Decide empty tables, crop/multiple-table matching,
   continuations, malformed outputs, missing/extra tables and zero-score policy.
3. Pin official PubTabNet TEDS code revision/dependencies and tested canonical HTML
   conventions, structure_only setting, case/table matching, averaging and missing-
   prediction penalties. Both full-content and structure-only TEDS may be reported
   as distinct endpoints if preregistered. Physical grid TEDS does not validate
   accounting row semantics, entity, period, numeric scale or multilevel row paths.
4. Produce reviewed independent EPS eligibility/numerator/denominator and full RE
   bridge ledgers directly from sources BEFORE examining predictions. Review all
   candidate filings including ineligible/missing/complex cases. Do not choose
   cases that already happen to pass. Keep source references and model-extracted
   candidate ledgers separate; otherwise this only checks reference arithmetic.
5. Fix development-only illustrative examples, document structure/schema/prompt
   generation instructions, safety wrapper, generated-schema compatibility gate,
   context mode, rendering, model/provider/settings and raw artifact retention.
   Implement actual model orchestration and safe additive budget guard separately.
   A model-generated prompt cannot override source-only transcription or authorize
   changing numbers. Generated schemas require an independently reviewed adapter
   into this geometry contract; never complete missing spans from logical JSON.
6. Freeze deterministic cleaner rules and pre/post-cleaning endpoints on development
   sources only. Preserve both outputs and all edits. No choosing between raw and
   cleaned outcomes after seeing test scores. Malformed output remains a failure.
7. Register a modest, budgeted design first. Full-document vision reconnaissance of
   even ten filings can dominate cost and cannot be called faithful full-document
   review if only selected pages were sent. One model / few sources establishes a
   reconstruction demonstration, not the submitted three-model fifty-filing result.
   Generated stages are shared within a filing/context comparison only under an
   explicit design; accounting and numeric-reference values are never prompts.
8. Use additive new accounting for every request/unknown outcome with old exposure
   as baseline; preserve the historical budget ledger untouched. Parent owns the
   live budget protocol. A guarantee of cost below remaining headroom needs actual
   local payload/token bounds and provider price evidence; this scaffold makes no
   price quote and starts no paid request.

What the original method cannot recover retrospectively
The current schema/prompt/raw calls have no generated structure/schema/prompt stage,
CSV cleaner trace, physical spans, EPS eligibility documentation for every filing,
or complete retained-earnings bridge. Adding these now creates prospective evidence,
not proof of historical claims. Full-document review, actual generated stages, real
source-layout annotations, official TEDS scoring and new paid predictions remain
unperformed. No paper or release has been revised by this scaffold.
