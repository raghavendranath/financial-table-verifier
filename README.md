# Financial Table Verifier

Repository: [https://github.com/raghavendranath/financial-table-verifier](https://github.com/raghavendranath/financial-table-verifier)

Runnable research software for extracting and checking financial tables. This release
contains **the actual CSV pipeline and scientific scoring code used in the INSPIRE
revision**, alongside the earlier general-purpose JSON table tool. Their metrics and
interfaces are deliberately distinct.

The software is a tested research release candidate, not a certified financial service.
It does not certify that an accounting pass means a table was extracted correctly.

## Start offline

Python 3.11 or newer, on macOS or Linux (the frozen study code uses POSIX file locks):

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
ftv --version
ftv paper-info
ftv paper-demo --output paper-demo-output
ftv paper-tests
python -m unittest discover -s tests -v
```

No key, filing download or paid API request is needed. `paper-demo` runs the preserved
CSV preparation, extraction, cleaning, replay and source-value scoring functions using
canned synthetic responses. Its successful score demonstrates working software, not
model accuracy. Use an empty output directory for each demo.

`ftv demo --output json-demo-output` separately demonstrates the older fixed-JSON tool.

## Which code produces which results?

| Interface | Purpose | Metric |
|---|---|---|
| `ftv paper-*` | Preserved paper CSV method and full50 scientific functions | Selected signed printed-number recall and accounting coverage/checks |
| `ftv run`, `evaluate`, `audit`, `compare` | General-purpose fixed-JSON extraction experiments | Full-reference JSON metrics and separate consistency diagnostics |

The JSON tool does not reproduce the paper's generated-schema CSV results. The paper
method does not measure exhaustive table precision, complete hierarchy or autonomous
accuracy certification. See [paper reproduction](docs/PAPER_REPRODUCTION.md).

### Inspect the saved full50 paper report

The paper archive is distributed separately from this code repository. After extracting
its full result JSON, verify and recount its saved numeric cells without any API calls:

```sh
ftv paper-inspect --report /path/to/final50-results.json \
  --sha256 e989c2c8edec9ab5a263bae39bed749c4d95ddc7ec33d48d9d549878e34aaaf3
```

This inspects all 12 RAW/CLEAN groups and retains all 300 outcomes. It is a saved-cell
recount, not fresh inference or an independent rereading of PDF references.

### Score supplied CSV records with the actual paper functions

```sh
ftv paper-score --input paper-demo-output/portable-score-input.json \
  --output another-paper-score.json
```

The input contains source populations, their physical-cell map, a table inventory and
parsed model records. The adapter calls the unchanged full50 compiler/binder/scorer.
It does not manufacture original request provenance or silently re-sign study manifests.

## Explicit live JSON experiments

The `plan` command is offline. `run` uploads selected page images to OpenRouter and
the selected provider, and requires an explicit opt-in:

```sh
# Supply your own filing PDF and edit the portable manifest/config examples first.
cp configs/manifest.example.json data-manifest.json
ftv plan --config configs/sequential.toml
# Set OPENROUTER_API_KEY in your local environment, not in tracked files.
ftv run --config configs/sequential.toml --allow-paid
```

Edit `manifest` in both TOML files to point to your manifest. Paths are resolved relative
to their containing config/manifest files. Model/provider capability is checked at run
time; no different model is silently substituted. `.env` files are not loaded automatically.

Request limits include retries. The known-cost stop is **not a guaranteed spending cap**:
one call may exceed it, and an unknown charge blocks later requests. Use provider/key
spending controls too. The transport rejects redirects and treats ambiguous TLS/send
failures as unknown outcomes rather than assuming they were free.

The preserved CSV runtime accepts a caller-owned transport and budget controls; it has
no default key reader or automatic paid launcher. See the study API example in
[paper reproduction](docs/PAPER_REPRODUCTION.md).

## Reproducibility and release contents

- Preserved scientific sources are fingerprinted in `paper_runtime/source-manifest.json`
  inside the Python package. `paper-info` checks every recorded source byte and the
  active CSV procedure hash before the portable adapters import them.
- New portability and transport fixes are outside the frozen scientific sources.
  Historical study files, responses, charges and results were not rewritten.
- Unit tests cover malformed output, wrong values that still balance, source/period/unit
  mismatch, missing denominators, immutable resume and failed-request accounting.
- No API key, real filing PDF, raw paid response, virtual environment or historical run
  directory is included in the public code candidate.
- `requirements-tested.txt` records this release's verification environment, not a claim
  that the historical study used those same installed package versions.

The primary CSV method is included. Optional ten-report HTML/TEDS scoring is not bundled
as a public runtime dependency; its original third-party files remain in the study
archive with their notices. It is separate from the paper's primary CSV evaluation.

## License

Our code is available under the [MIT License](LICENSE). Installed dependencies retain
their own licenses; see [third-party notices](THIRD-PARTY-NOTICES.md).

See [publishing instructions](PUBLISHING.md) for the repository URL, version tag,
software citation and paper availability statement.

## Acknowledgment

OpenAI Codex assisted software development. The author is responsible for the code.
