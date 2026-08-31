# CLAUDE.md

Guidance for working in this repository.

## What this project is

`bio-governance-lab` is a public portfolio project demonstrating
governance-as-code for **synthetic** life-sciences data. No real patient or
subject data ever belongs in this repository.

## Current milestone

Milestone 8: domain models, CLI, tests, CI, deterministic synthetic study
generation, YAML data contracts and contract validation, study-level
data-quality checks, a Nextflow pipeline that gates curation on both,
OpenLineage provenance events for a successful run, publication of the governed
assets into a local OpenMetadata instance, and one deterministic
READY/REVIEW/BLOCKED governance decision derived from that evidence.

**Not yet implemented, and not to be added without being asked:** DataHub,
Marquez, MCP, AI-agent governance. The pipeline is local-execution only: no
Kubernetes, Seqera Platform, cloud executor, container registry or DSL2 module
library. Data quality has no history, trends, drift detection, thresholds,
dashboard or database, and no numeric score. Lineage has no server, HTTP or
Kafka transport, database, failed-run events or custom facets, and does not use
Nextflow's own experimental lineage feature. The catalogue integration is one
local OpenMetadata over REST: no `openmetadata-ingestion` SDK, no OpenMetadata
`Pipeline`, glossary, tag, tier, owner or custom-property entities, no sync
daemon or reconciliation, no catalogue abstraction interface, and no pipeline
wiring — publication stays an explicit post-run command.

## Commands

```bash
uv sync                     # create/refresh the environment
uv run pytest               # tests
uv run ruff check .         # lint
uv run ruff format .        # format
uv run mypy src             # type-check
uv run bio-gov --help       # the CLI
uv run bio-gov demo generate  # write a synthetic study to data/raw/

# validate generated data against a contract
uv run bio-gov contract validate contracts/samples.v1.yaml data/raw/BIO-001/samples.csv
uv run bio-gov contract validate contracts/samples.v1.yaml data/raw/BIO-001/samples.csv \
  --json-out results/BIO-001/contracts/samples.contract.json

# evaluate a whole study for data quality
uv run bio-gov dq run data/raw/BIO-001
uv run bio-gov dq run data/raw/BIO-001 --json-out results/BIO-001/quality/dq-report.json

# publish the governed assets to a local OpenMetadata (needs a running server)
export OPENMETADATA_JWT_TOKEN=...            # never a flag, never committed
uv run bio-gov catalog openmetadata health
uv run bio-gov catalog openmetadata publish data/raw/BIO-001 results/BIO-001
uv run bio-gov catalog openmetadata get BIO-001

# decide whether a study's evidence makes it ready to use
uv run bio-gov governance evaluate results/BIO-001
uv run bio-gov governance evaluate results/BIO-001 \
  --json-out results/BIO-001/governance/governance-report.json

# emit OpenLineage provenance for a curation run
uv run bio-gov lineage emit data/raw/BIO-001 results/BIO-001/curated \
  --quality-report results/BIO-001/quality/dq-report.json \
  --output results/BIO-001/lineage/openlineage.jsonl

# run the governance-gated pipeline (needs Nextflow and a JVM, installed separately)
nextflow run pipelines/nextflow/main.nf
nextflow run pipelines/nextflow/main.nf --study_dir data/raw/BIO-002   # a broken study
```

CI runs `ruff check .`, `ruff format --check .`, `mypy src` and `pytest`. Run
all four before committing.

## Layout

- `src/bio_governance/models/` — the domain model. `enums.py` for controlled
  vocabularies, `identifiers.py` for `AssetIdentifier`, `governance.py` for
  `Asset` and its parts. Export new public models from `models/__init__.py`.
- `src/bio_governance/synthetic/` — `generator.py` holds the deterministic
  synthetic study generator and its record models. Export new public names from
  `synthetic/__init__.py`.
- `src/bio_governance/contracts/` — `models.py` for the contract definition and
  result models, `loader.py` for YAML loading, `validator.py` for applying a
  contract to a CSV. Export new public names from `contracts/__init__.py`.
- `src/bio_governance/quality/` — `models.py` for the check and report models,
  `checks.py` for the study-level checks. Export new public names from
  `quality/__init__.py`.
- `src/bio_governance/lineage/` — `openlineage.py` holds the job, dataset and
  run identities and `emit_curation_lineage`. Export new public names from
  `lineage/__init__.py`.
- `src/bio_governance/governance/` — `models.py` for the decision, status,
  check and report models, `evaluate.py` for the five checks over a results
  directory. Export new public names from `governance/__init__.py`.
- `src/bio_governance/catalog/` — `models.py` for the configuration and result
  models, `mapping.py` for the `bio://`-to-OpenMetadata mapping (no IO, no
  HTTP), `client.py` for the REST client, `publish.py` for orchestration.
  Export new public names from `catalog/__init__.py`.
- `contracts/` — the contract definitions themselves, as YAML. Committed.
- `pipelines/nextflow/` — `main.nf` holds the DSL2 workflow, `nextflow.config`
  its parameters and manifest. Nothing else belongs here.
- `src/bio_governance/cli.py` — the `bio-gov` Typer app. The `demo` sub-app
  hosts generation commands, the `contract` sub-app hosts validation, the `dq`
  sub-app hosts quality evaluation, the `lineage` sub-app hosts emission, and
  `catalog openmetadata` hosts `health`, `publish` and `get`.
- `infra/openmetadata/` — the README for the official local Docker quickstart,
  and nothing else. The compose file is downloaded, not vendored, and it and its
  `docker-volume/` are git-ignored.
- `tests/` — mirrors the source modules. Shared fixtures live in `conftest.py`.
- `docs/` — `architecture.md` (decisions), `governance-model.md` (meaning),
  `synthetic-data.md` (the generated study), `data-contracts.md` (the contract
  format and validation), `data-quality.md` (the checks and the evidence),
  `lineage.md` (OpenLineage job, run, datasets and transport),
  `openmetadata.md` (containers, identity mapping, auth, idempotence, lineage),
  `governance-evaluation.md` (the decision model, the five checks, exit codes).
- `data/` — generated output. Git-ignored; never commit generated data.
- `results/` — pipeline output. Git-ignored, like `work/` and `.nextflow*`.

## Conventions

- **Models are frozen.** Every Pydantic model sets `frozen=True`. Build a new
  instance rather than mutating one; governance records describe a point in
  time.
- **Prefer immutable collection types** (`tuple[...]`) on models so frozen
  models stay hashable.
- **Use enums for closed vocabularies.** `StrEnum`, lower-case string values.
- **mypy runs strict over `src`.** Do not add `Any` or `type: ignore` to `src`
  to get a check to pass; fix the type. In tests, a narrow `type: ignore` on a
  deliberately invalid input is fine.
- **Do not add abstraction layers ahead of a concrete second use case.** No
  repository interfaces, plugin systems or base classes until an integration
  actually demands one.
- **Generated data is reproducible.** Given the same study ID, sample count,
  compound count and seed, the generator must produce byte-for-byte identical
  files. No timestamps, no clock, no environment, no unordered iteration, and
  fixed format strings for every number. Draw only `random()` from a seeded
  `random.Random`; the other methods carry no reproducibility guarantee.
- **The generator does not validate its own output.** The `--inject-*` options
  exist to create malformed data. The contracts detect it.
- **Contracts are data, and independent of the generator.** Rules live in
  `contracts/*.yaml`, never in Python. The validator must not import `Sample` or
  `Compound` — if the generator defined correctness, a pass would prove nothing.
  Use the generator to build test fixtures, never to supply expectations.
- **The contract vocabulary is closed.** Columns, types, `required`, `unique`,
  `minimum`, `allowed_values`, `pattern`, `references`, `extra_columns`. Adding a
  rule means adding a named `Rule` member so violations stay reportable. No
  expression language, inheritance, plugins or contract registry.
- **Contract validation is binary.** `ContractValidationResult.passed` is a
  boolean and one violation fails the dataset. No scores, severities or
  thresholds — that is the data-quality milestone.
- **Every rule runs against every row.** Validation never stops at the first
  failure; the report has to describe every defect in one pass.
- **A blank value in a column that permits blanks is skipped by every other
  rule.** Vehicle controls have no compound; reporting a type or foreign-key
  failure for that absence is noise.
- **Foreign keys resolve to a bare sibling file name** next to the dataset. No
  paths, URIs, connectors or remote storage.
- **Standard library only for generation.** Do not add pandas or numpy without a
  concrete need.
- **Contracts and data quality are different layers.** A contract asks whether
  one file conforms to its declared structure, one row at a time. A quality
  check asks whether the study is consistent and usable, across files. Do not
  restate a contract rule as a quality check: if a per-row rule already covers
  it, the contract owns it.
- **Quality checks are deterministic and study-local.** They read the four files
  of one directory and nothing else — no clock, no database, no previous run.
- **Quality does not import the generator either**, for the same reason the
  validator does not. `VEHICLE_TREATMENT` and the file names are restated in
  `quality/checks.py`.
- **The check vocabulary is closed.** Adding a check means adding a named
  `QualityCheck` member so a finding stays reportable.
- **`overall_status` is derived, never stored**, and there is no numeric score.
  A finding that must not stop a pipeline is a `WARN`.
- **A quality report is counted, not enumerated, where a defect is repetitive.**
  One finding saying "24 of 240 measurements are unusable", never one per cell.
- **The pipeline shells out to `bio-gov`.** A gate process runs
  `bio-gov contract validate`, `bio-gov dq run` or `bio-gov lineage emit` and
  lets the exit code decide; it never imports `validate_dataset`,
  `evaluate_study` or `emit_curation_lineage`. The exit code is the
  orchestrator-agnostic interface.
- **The gates are structural.** `EVALUATE_GOVERNANCE` joins every upstream
  process's output channel; `EMIT_OPENLINEAGE` consumes `CURATE`'s, which
  consumes `RUN_DATA_QUALITY`'s, which consumes the samples gate's, which
  consumes the compounds gate's. Never give a downstream process a
  path to raw data that bypasses a gate, and never soften
  `errorStrategy = 'terminate'` — a governance failure is a verdict, not a
  transient failure.
- **Processes carry governance in their names.** `CONTRACT_GATE_*`,
  `RUN_DATA_QUALITY`, `EMIT_OPENLINEAGE` and `EVALUATE_GOVERNANCE`, so a run log
  shows where the decision happened, where its provenance was recorded and where
  the verdict was reached.
- **The curated step stays trivial.** It copies files. Do not invent a
  scientific transformation to make the pipeline look substantial.
- **Lineage uses OpenLineage's models, never our own.** Events are built from
  `openlineage.client.event_v2` and written by its `FileTransport`. Do not
  hand-build the JSON, and do not add a facet nothing reads.
- **One namespace, and the existing identifier convention.** Job and datasets
  both live in the `bio-governance-lab` namespace, and a dataset's name is the
  `bio://` URI built through `AssetIdentifier`. Do not invent a second asset-ID
  scheme.
- **A run is START then COMPLETE, sharing one run ID.** The job identity is
  stable across executions; the run ID is not.
- **Lineage is not reproducible, and must not be made so.** A run ID and a UTC
  timestamp are what make an event describe one execution. This is the single
  deliberate exception to the determinism rule above.
- **Provenance is only claimed for files that exist.** Every raw and curated
  file the events name is checked before anything is emitted; a missing one is a
  `LineageError` and exit status 2. Emission has no failure verdict, so there is
  no exit status 1.
- **Failed runs emit nothing.** Do not add `FAIL`-event orchestration, an
  `onComplete` handler, or an `errorStrategy` change to work around it.
- **Nextflow is not a Python dependency.** It is installed separately and CI does
  not install it.
- **The catalogue is published to over REST, not through the SDK.**
  `openmetadata-ingestion` resolves to around 130 transitive packages for five
  kinds of request. Use `httpx` against the documented endpoints.
- **Storage service and containers, and never a false service type.** Our assets
  are generated files, so they are containers of one `CustomStorage` storage
  service. Registering them as MySQL, PostgreSQL or Snowflake would put a false
  statement in the catalogue.
- **`bio://` identity is not replaced by an OpenMetadata FQN.** The entity name
  is derived from the identifier one-way, and the canonical URI is carried
  unchanged in the container's `fullPath`. An FQN is scoped to one deployment;
  `bio://` is not.
- **Every catalogue write is a create-or-update `PUT`.** Idempotence is a
  property of the requests, not of bookkeeping. Do not add a read-then-decide
  step or a local record of what was published.
- **Only lineage edges that can be explained in a sentence.** Six per study: each
  raw file to its curated copy, and all three raw files to the quality report.
  Do not derive edges from an OpenLineage event's input-output cross product.
- **Provenance is only claimed for files that exist**, as in the lineage layer:
  every file a container will name is checked before the first request, so a
  failed publication leaves nothing half-catalogued.
- **A token is configuration, never an argument.** Read `OPENMETADATA_HOST` and
  `OPENMETADATA_JWT_TOKEN` from the environment. Never hard-code, commit, log or
  echo a token — `token_hint` is the only thing that may be printed. `health` is
  answerable without one; every write demands one and the error names the
  variable.
- **The pipeline must run with OpenMetadata offline.** Publication is an explicit
  post-run command, not a process in `main.nf`.
- **Deterministic code decides; AI explains.** The governance decision is
  computed from files on disk — no clock, network, catalogue, randomness or
  model. A later milestone may have an LLM explain a report; it must never
  calculate or override one, and `decision` being a computed field is what makes
  that impossible rather than merely discouraged.
- **The decision is derived, never stored.** Any `FAIL` gives `BLOCKED`,
  otherwise any `WARN` gives `REVIEW`, otherwise `READY`. There is no numeric
  governance score. A report cannot claim a verdict its checks do not support.
- **The governance check vocabulary is closed**, like the contract rules and the
  quality checks. Adding a check means adding a named `GovernanceCheck` member.
  Five checks do not need a policy engine, a rule language or Rego.
- **A check must read real evidence.** Ownership, classification, retention,
  access control and catalogue presence are not checks yet, because nothing in
  this project produces evidence for them and a check that reads nothing always
  passes.
- **Absent or incoherent evidence is a `FAIL`, not an exception.** Exit status 2
  is reserved for a results directory that cannot be read as a study at all —
  there is no verdict to give. Everything else is `BLOCKED`.
- **Evidence is written before the exit status is decided**, in every layer.
  `contract validate --json-out` and `dq run --json-out` both write, pass or
  fail, because the failing run is the one whose evidence is wanted.
- **The contract JSON is the existing `ContractValidationResult`.** No second
  contract-result model; `passed` is a computed field so the evidence states its
  own verdict.
- Line length is 100. Ruff owns formatting.

## Testing

Every model gets tests for both the valid case and the rejected case —
demonstrating that invalid governance metadata fails is the point of the
project, not an afterthought. CLI changes get a smoke test.

Generator tests write to `tmp_path` and never leave files in the repository.
Every bad-data injection gets a test proving it introduces its intended defect,
and determinism is asserted by comparing bytes across two runs.

Quality tests generate a study and then damage it in one specific way, so each
test names the check it is about. Several defects trip more than one check —
that is the honest behaviour — so assert on a named check's status rather than
only on the report. One test proves the point of the whole layer: a study with
its vehicle controls deleted passes the samples contract and fails data quality.
Shared CSV-damaging helpers live in `conftest.py`.

Pipeline tests run Nextflow for real, under `tmp_path`, and skip when Nextflow
is not on `PATH`; the static assertions about parameters, process names and the
gate ordering run everywhere. All three outcomes are tested: clean data reaches
`CURATE` and `EMIT_OPENLINEAGE`, contract-invalid data stops at the contract
gate, and contract-valid but low-quality data stops at `RUN_DATA_QUALITY`. Each
failing case asserts that no curated *and* no lineage directory is written.

Lineage tests assert on the event structure the spec defines, never on bytes:
two events, START then COMPLETE, one shared run ID, the job identity, the
producer, and the raw and curated dataset names. An explicit `run_id` is how a
test asserts on a known value. The CLI gets a test writing a real JSONL file and
one proving a missing source file exits 2.

Catalogue tests mock HTTP with `respx` and must never need a server: CI does
not start OpenMetadata. A fake server keys entities the way the real one does, so
a duplicate shows up as a second entry rather than an overwrite. Assert on the
configuration defaults, the clear error when a token is missing, the entity-name
mapping, the seven prepared assets, the preserved `bio://` identity, the file
formats, the six-edge set, useful messages for connection and token failures,
and that a second publication sends the same requests as the first. The live
demonstration lives in `tests/test_catalog_live.py` and skips unless
`OPENMETADATA_INTEGRATION_TEST=1`.

Governance tests evaluate evidence that was actually produced: `build_results`
in `conftest.py` runs the same commands `main.nf` does, and a test then damages
one piece of it and asserts on the named check and the decision. Do not
hand-author an evidence file into the shape the evaluator hopes to find, and do
not change the generator to manufacture a warning — edit the quality report. The
decision tests prove derivation directly: a report handed `decision=READY`
alongside a failing check still reads `BLOCKED`.

Contract tests load the real YAML from `contracts/` rather than inline
definitions, so the shipped contracts are what is under test. Every `--inject-*`
option gets a test proving the contract catches it and names the right rule, and
one test proves all four are reported together. Contract-loading failures get
tests asserting the error message is clear.
