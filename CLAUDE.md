# CLAUDE.md

Guidance for working in this repository.

## What this project is

`bio-governance-lab` is a public portfolio project demonstrating
governance-as-code for **synthetic** life-sciences data. No real patient or
subject data ever belongs in this repository.

## Current milestone

Milestone 2: domain models, CLI, tests, CI, plus deterministic synthetic study
generation.

**Not yet implemented, and not to be added without being asked:** data
contracts, data-quality checks, Nextflow, OpenLineage, OpenMetadata, DataHub,
MCP, AI-agent governance.

## Commands

```bash
uv sync                     # create/refresh the environment
uv run pytest               # tests
uv run ruff check .         # lint
uv run ruff format .        # format
uv run mypy src             # type-check
uv run bio-gov --help       # the CLI
uv run bio-gov demo generate  # write a synthetic study to data/raw/
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
- `src/bio_governance/cli.py` — the `bio-gov` Typer app. The `demo` sub-app
  hosts generation commands.
- `tests/` — mirrors the source modules. Shared fixtures live in `conftest.py`.
- `docs/` — `architecture.md` (decisions), `governance-model.md` (meaning),
  `synthetic-data.md` (the generated study).
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
  exist to create malformed data. Detecting it belongs to a later milestone.
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
