# CLAUDE.md

Guidance for working in this repository.

## What this project is

`bio-governance-lab` is a public portfolio project demonstrating
governance-as-code for **synthetic** life-sciences data. No real patient or
subject data ever belongs in this repository.

## Current milestone

Milestone 3: domain models, CLI, tests, CI, deterministic synthetic study
generation, plus YAML data contracts and contract validation.

**Not yet implemented, and not to be added without being asked:** data-quality
scoring, Nextflow, OpenLineage, OpenMetadata, DataHub, MCP, AI-agent
governance.

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
- `contracts/` — the contract definitions themselves, as YAML. Committed.
- `src/bio_governance/cli.py` — the `bio-gov` Typer app. The `demo` sub-app
  hosts generation commands; the `contract` sub-app hosts validation.
- `tests/` — mirrors the source modules. Shared fixtures live in `conftest.py`.
- `docs/` — `architecture.md` (decisions), `governance-model.md` (meaning),
  `synthetic-data.md` (the generated study), `data-contracts.md` (the contract
  format and validation).
- `data/` — generated output. Git-ignored; never commit generated data.

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
- Line length is 100. Ruff owns formatting.

## Testing

Every model gets tests for both the valid case and the rejected case —
demonstrating that invalid governance metadata fails is the point of the
project, not an afterthought. CLI changes get a smoke test.

Generator tests write to `tmp_path` and never leave files in the repository.
Every bad-data injection gets a test proving it introduces its intended defect,
and determinism is asserted by comparing bytes across two runs.

Contract tests load the real YAML from `contracts/` rather than inline
definitions, so the shipped contracts are what is under test. Every `--inject-*`
option gets a test proving the contract catches it and names the right rule, and
one test proves all four are reported together. Contract-loading failures get
tests asserting the error message is clear.
