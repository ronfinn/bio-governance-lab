# bio-governance-lab

Governance-as-code for synthetic life-sciences data.

This repository is a public portfolio project exploring how data governance —
ownership, classification, lineage, contracts and quality — can be expressed as
typed, tested, version-controlled code rather than as documents in a wiki.

> **Status: milestone 3 — data contracts.** This repository contains the core
> domain model, a deterministic generator for a small synthetic study, and YAML
> data contracts that validate the generated CSVs. There is no data-quality
> scoring, pipeline or catalogue integration yet. See
> [Deferred work](#deferred-work).

## What is here today

- A small, strict [Pydantic](https://docs.pydantic.dev/) domain model describing
  a governed data asset.
- A URI-style asset identifier: `bio://BIO-001/raw/samples`.
- A deterministic generator for a synthetic compound-perturbation study, with
  optional bad-data injection.
- Small YAML data contracts over the generated CSVs, and a validator that
  reports every violation rather than the first.
- A [Typer](https://typer.tiangolo.com/) CLI, `bio-gov`.
- A full test suite, lint, format and type checks, wired into GitHub Actions.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync
uv run bio-gov --help
uv run bio-gov demo generate
uv run bio-gov contract validate contracts/samples.v1.yaml data/raw/BIO-001/samples.csv
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

CI runs exactly these four commands on every push and pull request.

## Documentation

- [Architecture](docs/architecture.md) — layout and technical decisions.
- [Governance model](docs/governance-model.md) — what the domain model means.
- [Synthetic data](docs/synthetic-data.md) — the generated study, determinism
  and bad-data injection.
- [Data contracts](docs/data-contracts.md) — the YAML format, the two contracts,
  validation and exit codes.

## Deferred work

Deliberately **not** implemented in this milestone: data-quality scoring,
Nextflow, OpenLineage, OpenMetadata, DataHub, MCP and AI-agent governance. Each
will land as its own milestone on top of this foundation.

Contract validation is deliberately binary and structural. Grading a dataset —
drift, completeness trends, severity, thresholds — is a separate concern and
arrives with the milestone that has somewhere to record the results.

## Licence

MIT.
