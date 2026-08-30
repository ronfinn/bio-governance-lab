# Data quality

Milestone 5 adds a second governance layer: six deterministic checks over a
whole generated study, a PASS/WARN/FAIL report, and a JSON artefact recording
what was checked and what was found.

## Contract validation is not data quality

The two layers answer different questions, and both answers are needed.

| | Contract validation | Data quality |
| --- | --- | --- |
| Question | Does this **file** conform to its declared structure? | Does this **study** hold consistent, usable data? |
| Scope | One CSV, one row at a time | Four files, compared against each other |
| Defined by | `contracts/*.yaml` | Named checks in `quality/checks.py` |
| Reports | Rule, row, column | Check, observed, expected |
| Verdict | Binary — pass or fail | PASS / WARN / FAIL |

The distinction is not theoretical. Delete every vehicle-control row from
`samples.csv` and the samples contract still passes: each remaining row has a
well-formed identifier, a valid compound reference, a non-negative dose and a
legal tissue. Nothing about a row is wrong. What is wrong is the study — the
treatments have nothing to be compared against, the manifest no longer matches
the count the study declares, and the expression matrix measures samples the
manifest has forgotten. No per-row rule can see any of that, because none of it
is visible from inside a single row of a single file.

So the quality checks are deliberately *not* a restatement of the contract
rules. A rule already enforced per row — dose is non-negative, `compound_id`
exists in the registry, `sample_id` is unique — has no reason to be checked
again here.

## The six checks

| Check | What it compares | Why a contract cannot |
| --- | --- | --- |
| `sample_count_consistency` | Rows in `samples.csv` against `sample_count` in `study.json` | The expectation lives in a different file |
| `vehicle_control_presence` | At least one sample treated with `vehicle` | A property of the set of rows, not of any row |
| `compound_coverage` | Every compound in `compounds.csv` has a treated sample | The contract's foreign key runs the other way |
| `expression_sample_alignment` | Sample columns of `expression.csv` against the non-blank sample IDs | Two files, compared in both directions |
| `expression_completeness` | Every measurement is a non-blank, finite number | `expression.csv` has one column per sample, so its shape is not fixed |
| `expression_gene_count` | Rows in `expression.csv` against `gene_count` in `study.json` | The expectation lives in a different file |

`expression_sample_alignment` reports both directions separately — samples
missing from the matrix, and measured columns nobody registered — because they
are different problems. `expression_completeness` reports a **count**, not one
finding per cell: a matrix has thousands of cells and a report with a thousand
findings is a report nobody reads.

Deliberately absent: biological plausibility, outlier detection, distribution
drift, and any comparison against a previous run.

## PASS, WARN and FAIL

A check reports one status. The report's `overall_status` is derived from them:

- **FAIL** if any check failed,
- **WARN** if none failed and at least one warned,
- **PASS** otherwise.

It is derived rather than stored, so a report cannot claim a status its checks
do not support. There is no numeric score — a score needs weights nobody agreed
on, and a number cannot tell a steward what to do next.

None of the six checks currently emits `WARN`; every defect they look for makes
the study unusable. The status exists so that a later non-blocking check has
somewhere to land without having to choose between silence and stopping a
pipeline.

## Running it

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

A passing check prints its name alone; a failing one prints what it found:

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

Exit status is `0` for PASS or WARN, `1` for FAIL, and `2` when the study could
not be read at all.

## JSON evidence

`--json-out` writes the structured report, which is what a pipeline, a
catalogue or a later lineage event should read rather than parsing the text:

```bash
uv run bio-gov dq run data/raw/BIO-001 --json-out results/BIO-001/quality/dq-report.json
```

```json
{
  "study_id": "BIO-001",
  "checks": [
    {
      "check_id": "vehicle_control_presence",
      "status": "pass",
      "message": "2 sample(s) carry the 'vehicle' control treatment",
      "observed": "2",
      "expected": "at least 1"
    }
  ],
  "overall_status": "pass"
}
```

Every check appears in the report whether it passed or not: evidence that a
check ran and found nothing is evidence. The file is written **before** the exit
status is decided, so a failing study still leaves its report behind — that is
the run somebody will want to read.

## In the pipeline

`RUN_DATA_QUALITY` sits between the contract gates and curation:

```
CONTRACT_GATE_COMPOUNDS -> CONTRACT_GATE_SAMPLES -> RUN_DATA_QUALITY -> CURATE
```

The order is the argument. Structure is checked first, because a file that is
not well-formed cannot meaningfully be assessed for consistency. `CURATE`
consumes `RUN_DATA_QUALITY`'s output channel and nothing else, so there is no
path from raw data to the curated directory that skips either gate.

A published run leaves `results/<STUDY>/quality/dq-report.json` beside the
contract reports. A failed one does not: Nextflow does not publish the outputs
of a process that exited non-zero. The report is still in the run's work
directory and the readable form is in the log, which is where a failure is read
from anyway.

## What is deferred

No history, no trends, no drift detection, no thresholds to tune, no dashboard
and no database. Those need somewhere to record a series of results and a reason
to compare them — which arrives with catalogue and lineage integration, not
before. What exists now is a single run's evidence, and a gate that acts on it.
