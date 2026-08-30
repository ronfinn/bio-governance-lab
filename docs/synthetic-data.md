# Synthetic data

Milestone 2 adds a generator for a small, deterministic, entirely invented
life-sciences study. It exists so that later milestones — data contracts,
quality checks, lineage, catalogue integration — have something concrete to run
against, including data that is deliberately wrong.

Nothing in this repository is derived from real patients, subjects, samples or
proprietary compounds, and nothing ever will be.

## What the study represents

A **compound-perturbation screen**: cells are treated either with a vehicle
control or with one of several test compounds at a range of doses, and a small
gene-expression readout is measured for every sample.

That shape is chosen because it is the smallest experiment that still exercises
the governance questions worth demonstrating — a controlled vocabulary of
mechanisms, a control condition that must exist, referential integrity between
samples and compounds, replicate structure, and a numeric measurement matrix
keyed by sample.

The values themselves are not a model of transcriptomics. Each gene gets a
baseline level, a per-compound response scaled by dose, and a little noise. It
produces a plausible *shape*, nothing more, and it is not intended to support
any biological conclusion.

## Generated files

Files are written to `<output>/<STUDY-ID>/`, by default `data/raw/BIO-001/`.
Generated data is git-ignored; regenerate it on demand rather than committing
it.

### `study.json`

Study-level metadata: `study_id`, `name`, `description`, `organism`,
`model_system`, `seed`, `sample_count`, `compound_count`, `gene_count`, a
`synthetic` flag that is always true, the list of `injected_defects`, and the
`bio://` asset identifiers the four files correspond to — the same
`AssetIdentifier` type the governance model uses, so generated data is already
addressable by the milestone-1 vocabulary.

`sample_count` records the number of samples *requested*. Injecting a duplicate
adds a row to `samples.csv` without changing it; that disagreement is the
defect.

### `compounds.csv`

The study's compound registry: `compound_id` (`CMP-001`, `CMP-002`, …),
`compound_name`, `mechanism_class`. Names are assembled from syllable fragments
so they are unique and pronounceable while being obviously not real molecules.
`mechanism_class` is drawn from a fixed vocabulary — a closed set that a later
contract can assert against.

### `samples.csv`

One row per well: `sample_id` (`BIO-001-S001`), `study_id`, `compound_id`,
`treatment`, `dose`, `dose_unit`, `tissue`, `replicate`.

Samples are assigned round-robin across the study's conditions — vehicle first,
then each compound at each dose — so the requested sample count is honoured
exactly and any study with at least one sample has a control.

Vehicle controls carry an **empty** `compound_id`: they are treated with the
solvent alone and reference no test article. `treatment` is `vehicle` and `dose`
is `0.00`. Treated samples carry both the compound's ID and its name.

### `expression.csv`

A deliberately small matrix: one row per gene, one column per sample.

| gene_id | gene_symbol | BIO-001-S001 | … |
| --- | --- | --- | --- |
| `SYNG001` | `SYNA1` | `7.012` | … |

Gene identifiers are synthetic and prefixed `SYN` so they cannot be confused
with real symbols. The gene count is fixed at 12 and is not exposed on the CLI;
this is a fixture, not a dataset.

## CLI usage

```bash
uv run bio-gov demo generate

uv run bio-gov demo generate \
    --study BIO-001 \
    --samples 48 \
    --compounds 4 \
    --seed 42
```

Every option has a default, so the bare command works. `--output` (default
`data/raw`) chooses the directory the study folder is written into. Re-running
with the same arguments overwrites the previous files with identical content.

## Determinism

Given the same study ID, sample count, compound count and seed, the generated
files are **byte-for-byte identical**. This is asserted by the test suite, not
merely intended.

It holds because:

- Every random value comes from a single `random.Random` seeded with a string
  built from all the inputs, and only `random()` is drawn from it — the one
  method whose stream Python guarantees to be reproducible.
- Random values are consumed in a fixed order: compounds, then samples, then the
  expression matrix gene by gene.
- Nothing is derived from the clock, the filesystem, the environment or process
  state. In particular **`study.json` carries no timestamp**; a generated-at
  field would make identical inputs produce different bytes, and provenance
  timestamps belong to the run that produces an `Asset`, not to the fixture.
- Numbers are written through fixed format strings (`%.2f` for doses, `%.3f` for
  expression), so float repr never leaks into the output.
- Files are written with an explicit UTF-8 encoding and `\n` line endings,
  independent of platform.

Only the standard library is used. Neither pandas nor numpy is a dependency;
neither is needed at this size, and both would add a source of version-dependent
formatting.

## Bad-data injection

Four options make the generator write deliberately malformed data:

| Option | Defect written into `samples.csv` |
| --- | --- |
| `--inject-missing-sample-id` | One sample's `sample_id` is blank. |
| `--inject-invalid-dose` | One sample's `dose` is `-1.00`. |
| `--inject-duplicate-sample` | One sample row is repeated verbatim at the end. |
| `--inject-unknown-compound` | One sample references `CMP-000`, absent from `compounds.csv`. |

```bash
uv run bio-gov demo generate --inject-invalid-dose --inject-unknown-compound
```

Notes on the design:

- All four defects are sample-level and confined to `samples.csv`;
  `compounds.csv` and `expression.csv` are unchanged, so a check that fires has
  an unambiguous cause.
- Each defect targets a different row where the study is large enough, so
  several injections at once stay independently observable.
- The requested injections are recorded in `study.json` under
  `injected_defects`, which gives a later quality milestone an answer key.
- `CMP-000` is a *well-formed* identifier that the generator can never produce,
  since real ones count from `CMP-001`. The defect is therefore purely a
  dangling reference, not a malformed value, so a referential-integrity check
  and a format check cannot both take credit for it.

**The generator does not validate its own output.** Detecting these defects is
the point of the next milestone; producing them cheaply and repeatably is the
point of this one.

## Intentionally deferred

Still not implemented, and not to be added without being asked: data contracts,
data-quality validation, Nextflow orchestration, OpenLineage events,
OpenMetadata and DataHub integration, MCP, and AI-agent governance.

Also deliberately out of scope *within* the generator: biologically realistic
expression, batch or plate effects, missing-value patterns beyond the injections
above, time courses, multiple omics layers, and any configuration file format.
Those arrive when a consumer needs them.
