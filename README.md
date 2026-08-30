# bio-governance-lab

Governance-as-code for synthetic life-sciences data.

This repository is a public portfolio project exploring how data governance —
ownership, classification, lineage, contracts and quality — can be expressed as
typed, tested, version-controlled code rather than as documents in a wiki.

> **Status: milestone 4 — pipeline.** This repository contains the core domain
> model, a deterministic generator for a small synthetic study, YAML data
> contracts that validate the generated CSVs, and a Nextflow pipeline that puts
> those contracts in front of curation as a gate. There is no data-quality
> scoring or catalogue integration yet. See [Deferred work](#deferred-work).

## What is here today

- A small, strict [Pydantic](https://docs.pydantic.dev/) domain model describing
  a governed data asset.
- A URI-style asset identifier: `bio://BIO-001/raw/samples`.
- A deterministic generator for a synthetic compound-perturbation study, with
  optional bad-data injection.
- Small YAML data contracts over the generated CSVs, and a validator that
  reports every violation rather than the first.
- A [Nextflow](https://www.nextflow.io/) pipeline in which raw data reaches the
  curated directory only by passing the contract gate.
- A [Typer](https://typer.tiangolo.com/) CLI, `bio-gov`.
- A full test suite, lint, format and type checks, wired into GitHub Actions.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync
uv run bio-gov --help
uv run bio-gov demo generate
uv run bio-gov contract validate contracts/samples.v1.yaml data/raw/BIO-001/samples.csv
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

## The pipeline

`pipelines/nextflow/` is a small DSL2 workflow whose only argument is that
governance can gate processing:

```
data/raw/<STUDY>/  ->  CONTRACT_GATE_COMPOUNDS  ->  CONTRACT_GATE_SAMPLES  ->  CURATE
                                                                              |
                                                            results/<STUDY>/curated/
```

Each gate runs `bio-gov contract validate`. `CURATE` consumes the samples gate's
output channel and nothing else, so it cannot start until both contracts pass —
and `bio-gov` exits non-zero on a violation, which terminates the run.

```bash
uv run bio-gov demo generate --study BIO-001
nextflow run pipelines/nextflow/main.nf
```

```
[6c/285aa1] Submitted process > CONTRACT_GATE_COMPOUNDS (BIO-001)
[b1/cc82f2] Submitted process > CONTRACT_GATE_SAMPLES (BIO-001)
[71/6979a2] Submitted process > CURATE (BIO-001)
```

`results/BIO-001/` then holds `curated/{samples,compounds,expression}.csv` and
the `contracts/*.contract.txt` reports the gates produced. Break the study and
the gate stops it:

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

Nextflow exits non-zero, `CURATE` is never submitted, and no
`results/BIO-002/curated/` directory is written.

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

## Deferred work

Deliberately **not** implemented in this milestone: data-quality scoring,
OpenLineage, OpenMetadata, DataHub, MCP and AI-agent governance. Each will land
as its own milestone on top of this foundation. The pipeline is local-execution
only — no Kubernetes, no cloud executor, no container registry.

Contract validation is deliberately binary and structural. Grading a dataset —
drift, completeness trends, severity, thresholds — is a separate concern and
arrives with the milestone that has somewhere to record the results.

## Licence

MIT.
