# bio-governance-lab

Governance-as-code for synthetic life-sciences data.

This repository is a public portfolio project exploring how data governance —
ownership, classification, lineage, contracts and quality — can be expressed as
typed, tested, version-controlled code rather than as documents in a wiki.

> **Status: milestone 5 — data quality.** This repository contains the core
> domain model, a deterministic generator for a small synthetic study, YAML data
> contracts over the generated CSVs, study-level data-quality checks, and a
> Nextflow pipeline that puts both in front of curation as gates. There is no
> catalogue or lineage integration yet. See [Deferred work](#deferred-work).

## What is here today

- A small, strict [Pydantic](https://docs.pydantic.dev/) domain model describing
  a governed data asset.
- A URI-style asset identifier: `bio://BIO-001/raw/samples`.
- A deterministic generator for a synthetic compound-perturbation study, with
  optional bad-data injection.
- Small YAML data contracts over the generated CSVs, and a validator that
  reports every violation rather than the first.
- Six deterministic data-quality checks over the study as a whole, reported as
  PASS/WARN/FAIL with a JSON evidence file.
- A [Nextflow](https://www.nextflow.io/) pipeline in which raw data reaches the
  curated directory only by passing both gates.
- A [Typer](https://typer.tiangolo.com/) CLI, `bio-gov`.
- A full test suite, lint, format and type checks, wired into GitHub Actions.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync
uv run bio-gov --help
uv run bio-gov demo generate
uv run bio-gov contract validate contracts/samples.v1.yaml data/raw/BIO-001/samples.csv
uv run bio-gov dq run data/raw/BIO-001
nextflow run pipelines/nextflow/main.nf
```

## Synthetic data

`bio-gov demo generate` writes a small, entirely invented compound-perturbation
study to `data/raw/<STUDY-ID>/`:

```bash
uv run bio-gov demo generate --study BIO-001 --samples 48 --compounds 4 --seed 42
```

| File | Contents |
| --- | --- |
| `study.json` | Study metadata: organism, seed, counts, asset identifiers. |
| `compounds.csv` | `CMP-001`-style test articles and their mechanism class. |
| `samples.csv` | One row per well: compound, dose, tissue, replicate. |
| `expression.csv` | A 12-gene by *n*-sample matrix of invented values. |

The same study ID, sample count, compound count and seed always produce
byte-for-byte identical files, so generated data is regenerated on demand rather
than committed — `data/` is git-ignored.

Four options deliberately write malformed data: `--inject-missing-sample-id`,
`--inject-invalid-dose`, `--inject-duplicate-sample` and
`--inject-unknown-compound`. The generator only creates the defects; the
contracts below detect them.

See [Synthetic data](docs/synthetic-data.md) for the full description.

## Data contracts

`contracts/` holds a small YAML description of what each generated CSV must
contain — columns, types, required and unique fields, minimums, closed
vocabularies and foreign keys. Contracts are written independently of the
generator's Python models, so validation is a real check rather than the
generator grading itself.

```bash
uv run bio-gov contract validate contracts/samples.v1.yaml data/raw/BIO-001/samples.csv
```

```
Contract: bio.samples@1.0.0
Dataset: data/raw/BIO-001/samples.csv

PASS
Rows checked: 20
```

Generate the same study with all four injection options and every defect is
reported, not just the first:

```
FAIL
Rows checked: 21

4 violations

row 3   sample_id    required     value is blank
row 4   dose         minimum      -1.00 is below 0
row 5   compound_id  foreign_key  CMP-000 not found in compounds.csv column 'compound_id'
row 22  sample_id    unique       duplicate BIO-001-S001 (first seen at row 2)
```

Exit status is `0` for pass, `1` for fail and `2` if the contract or dataset
could not be read. Validation is binary — there is no score. `validate_dataset`
returns a structured `ContractValidationResult`; the CLI only renders it.

See [Data contracts](docs/data-contracts.md) for the YAML format, how foreign
keys resolve, and why contract validation is not the same thing as a
data-quality check.

## Data quality

A contract asks whether one file conforms to its declared structure. Data
quality asks a different question: whether the study as a whole is consistent
and usable. Six checks compare the four files against each other and against
what `study.json` declares.

```bash
uv run bio-gov dq run data/raw/BIO-001
```

```
Study: BIO-001
Data quality: PASS

PASS  sample_count_consistency
PASS  vehicle_control_presence
PASS  compound_coverage
PASS  expression_sample_alignment
PASS  expression_completeness
PASS  expression_gene_count
```

The two layers really are different. Delete every vehicle-control row from
`samples.csv` and the samples contract still passes — every remaining row is
well-formed — but the study is unusable:

```
Study: BIO-003
Data quality: FAIL

FAIL  sample_count_consistency     samples.csv holds 18 rows but study.json declares 20
FAIL  vehicle_control_presence     no sample carries the 'vehicle' control treatment
PASS  compound_coverage
FAIL  expression_sample_alignment  2 not in samples.csv (BIO-003-S001, BIO-003-S011)
PASS  expression_completeness
PASS  expression_gene_count
```

Exit status is `0` for PASS or WARN, `1` for FAIL, and `2` if the study could
not be read. `--json-out` writes the structured report, including for a failing
study — that is the run whose evidence somebody wants:

```bash
uv run bio-gov dq run data/raw/BIO-001 --json-out results/BIO-001/quality/dq-report.json
```

There is no numeric score, no history and no drift detection. See
[Data quality](docs/data-quality.md) for the six checks and what each one sees
that a contract cannot.

## The pipeline

`pipelines/nextflow/` is a small DSL2 workflow whose only argument is that
governance can gate processing:

```
data/raw/<STUDY>/
  -> CONTRACT_GATE_COMPOUNDS -> CONTRACT_GATE_SAMPLES -> RUN_DATA_QUALITY -> CURATE
                                                                              |
                                                            results/<STUDY>/curated/
```

Structure is checked first — a malformed file cannot meaningfully be assessed
for consistency — then the study as a whole. Each process consumes the previous
one's output channel and nothing else, so `CURATE` cannot start until both
contracts and all six checks have passed, and `bio-gov` exits non-zero on a
failure, which terminates the run.

```bash
uv run bio-gov demo generate --study BIO-001
nextflow run pipelines/nextflow/main.nf
```

```
[8e/0b21cd] Submitted process > CONTRACT_GATE_COMPOUNDS (BIO-001)
[40/47536f] Submitted process > CONTRACT_GATE_SAMPLES (BIO-001)
[ed/3d5e9c] Submitted process > RUN_DATA_QUALITY (BIO-001)
[45/06ad48] Submitted process > CURATE (BIO-001)
```

`results/BIO-001/` then holds `curated/{samples,compounds,expression}.csv`, the
`contracts/*.contract.txt` reports and `quality/dq-report.json`. Break the study
and the gate stops it:

```bash
uv run bio-gov demo generate --study BIO-002 --inject-invalid-dose --inject-unknown-compound
nextflow run pipelines/nextflow/main.nf --study_dir data/raw/BIO-002
```

```
ERROR ~ Error executing process > 'CONTRACT_GATE_SAMPLES (BIO-002)'

  FAIL
  Rows checked: 20

  2 violations

  row 4  dose         minimum      -1.00 is below 0
  row 5  compound_id  foreign_key  CMP-000 not found in compounds.csv column 'compound_id'
```

Nextflow exits non-zero, `RUN_DATA_QUALITY` and `CURATE` are never submitted,
and no `results/BIO-002/curated/` directory is written.

Data that satisfies every contract can still be stopped, one process later. Take
a clean study, delete its vehicle-control rows, and both contract gates pass
before `RUN_DATA_QUALITY` refuses it:

```
[28/bb9d93] Submitted process > CONTRACT_GATE_SAMPLES (BIO-003)
[48/572e3a] Submitted process > RUN_DATA_QUALITY (BIO-003)
ERROR ~ Error executing process > 'RUN_DATA_QUALITY (BIO-003)'
  Data quality: FAIL
  FAIL  vehicle_control_presence     no sample carries the 'vehicle' control treatment
```

| Parameter | Default |
| --- | --- |
| `--study_dir` | `data/raw/BIO-001` |
| `--samples_contract` | `contracts/samples.v1.yaml` |
| `--compounds_contract` | `contracts/compounds.v1.yaml` |
| `--outdir` | `results` |

The pipeline needs [Nextflow](https://www.nextflow.io/docs/latest/install.html)
and a Java runtime; nothing else is added to the Python environment. `work/`,
`results/` and `.nextflow*` are git-ignored.

## The domain model

```python
from datetime import UTC, datetime

from bio_governance.models import (
    Asset,
    AssetIdentifier,
    AssetType,
    Classification,
    LifecycleStage,
    Ownership,
    Provenance,
)

asset = Asset(
    identifier=AssetIdentifier.parse("bio://BIO-001/raw/samples"),
    name="Raw samples",
    asset_type=AssetType.DATASET,
    lifecycle_stage=LifecycleStage.RAW,
    classification=Classification.INTERNAL,
    ownership=Ownership(
        owner="Translational Data Platform",
        steward="Ron Finn",
        contact="steward@example.org",
    ),
    provenance=Provenance(
        source_system="synthetic-generator",
        generated_by="bio-gov",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    ),
)

asset.model_dump()["identifier"]  # 'bio://BIO-001/raw/samples'
```

| Model | Purpose |
| --- | --- |
| `Asset` | A governed data asset and its metadata. |
| `AssetIdentifier` | Stable `bio://<domain>/<path>` identity. |
| `AssetType` | What kind of thing the asset is. |
| `LifecycleStage` | Raw → curated → derived → published → archived. |
| `Classification` | Sensitivity of the contents. |
| `Ownership` | Accountable owner and responsible steward. |
| `Provenance` | Source system, producer, timestamp, upstream assets. |
| `ContractReference` | Pointer to the data contract the asset must satisfy. |
| `QualityStatus` | Result of the most recent quality evaluation. |
| `GovernanceStatus` | Where the asset sits in its review cycle. |

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

CI runs exactly these four commands on every push and pull request. The pipeline
tests in `tests/test_pipeline.py` run Nextflow for real when it is installed and
skip when it is not, so CI checks the pipeline's shape and a developer with
Nextflow checks its behaviour.

## Documentation

- [Architecture](docs/architecture.md) — layout and technical decisions.
- [Governance model](docs/governance-model.md) — what the domain model means.
- [Synthetic data](docs/synthetic-data.md) — the generated study, determinism
  and bad-data injection.
- [Data contracts](docs/data-contracts.md) — the YAML format, the two contracts,
  validation and exit codes.
- [Data quality](docs/data-quality.md) — contracts versus quality, the six
  checks, PASS/WARN/FAIL and the JSON evidence.

## Deferred work

Deliberately **not** implemented in this milestone: OpenLineage, OpenMetadata,
DataHub, MCP and AI-agent governance. Each will land as its own milestone on top
of this foundation. The pipeline is local-execution only — no Kubernetes, no
cloud executor, no container registry.

Data quality here is a single run's evidence and a gate that acts on it. There
is no numeric score, no stored history, no drift detection and no dashboard:
those need somewhere to record a series of results and a reason to compare them,
which arrives with catalogue and lineage integration.

## Licence

MIT.
