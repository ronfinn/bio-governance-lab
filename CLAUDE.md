# CLAUDE.md

Guidance for working in this repository.

## What this project is

`bio-governance-lab` is a public portfolio project demonstrating
governance-as-code for **synthetic** life-sciences data. No real patient or
subject data ever belongs in this repository.

## Current milestone

Milestone 6: domain models, CLI, tests, CI, deterministic synthetic study
generation, YAML data contracts and contract validation, study-level
data-quality checks, a Nextflow pipeline that gates curation on both, and
OpenLineage provenance events for a successful run.

**Not yet implemented, and not to be added without being asked:** OpenMetadata,
DataHub, Marquez, MCP, AI-agent governance. The pipeline is local-execution
only: no Kubernetes, Seqera Platform, cloud executor, container registry or DSL2
module library. Data quality has no history, trends, drift detection,
thresholds, dashboard or database, and no numeric score. Lineage has no server,
HTTP or Kafka transport, database, catalogue sync, failed-run events or custom
facets, and does not use Nextflow's own experimental lineage feature.

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

# evaluate a whole study for data quality
uv run bio-gov dq run data/raw/BIO-001
uv run bio-gov dq run data/raw/BIO-001 --json-out results/BIO-001/quality/dq-report.json

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
- `contracts/` — the contract definitions themselves, as YAML. Committed.
- `pipelines/nextflow/` — `main.nf` holds the DSL2 workflow, `nextflow.config`
  its parameters and manifest. Nothing else belongs here.
- `src/bio_governance/cli.py` — the `bio-gov` Typer app. The `demo` sub-app
  hosts generation commands, the `contract` sub-app hosts validation, the `dq`
  sub-app hosts quality evaluation, and the `lineage` sub-app hosts emission.
- `tests/` — mirrors the source modules. Shared fixtures live in `conftest.py`.
- `docs/` — `architecture.md` (decisions), `governance-model.md` (meaning),
  `synthetic-data.md` (the generated study), `data-contracts.md` (the contract
  format and validation), `data-quality.md` (the checks and the evidence),
  `lineage.md` (OpenLineage job, run, datasets and transport).
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
- **The gates are structural.** `EMIT_OPENLINEAGE` consumes `CURATE`'s output
  channel, which consumes `RUN_DATA_QUALITY`'s, which consumes the samples
  gate's, which consumes the compounds gate's. Never give a downstream process a
  path to raw data that bypasses a gate, and never soften
  `errorStrategy = 'terminate'` — a governance failure is a verdict, not a
  transient failure.
- **Processes carry governance in their names.** `CONTRACT_GATE_*`,
  `RUN_DATA_QUALITY` and `EMIT_OPENLINEAGE`, so a run log shows where the
  decision happened and where its provenance was recorded.
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

Contract tests load the real YAML from `contracts/` rather than inline
definitions, so the shipped contracts are what is under test. Every `--inject-*`
option gets a test proving the contract catches it and names the right rule, and
one test proves all four are reported together. Contract-loading failures get
tests asserting the error message is clear.
