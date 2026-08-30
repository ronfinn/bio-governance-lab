# Data contracts

Milestone 3 adds data contracts: a small YAML description of what a generated
CSV file is expected to contain, and a validator that checks a file against it
and reports every way in which it does not.

The point is separation. The generator writes the data; the contract states,
independently, what correct data looks like. Nothing in `contracts/` is derived
from the generator's Python models, and the validator never imports `Sample` or
`Compound`. If it did, a passing run would only prove that the generator agrees
with itself.

## What a data contract is here

A contract is a **file-level agreement**, versioned and reviewable in git,
describing one CSV file:

- its identity — `contract_id`, `version`, and the `bio://` asset it governs;
- the columns the file must carry, and what the contract says about columns it
  does not declare;
- for each column, what a value has to look like: present, typed, unique, at or
  above a minimum, drawn from a fixed set, matching a shape;
- which column values must exist in another file.

That is the whole vocabulary. There is no expression language, no inheritance
and no plugin mechanism, because every rule has to be reportable as a named
violation — `minimum`, `foreign_key` — rather than as "an expression returned
false".

## The YAML shape

```yaml
contract_id: bio.samples          # lower-case dotted identity
version: 1.0.0                    # exact version; nothing resolves ranges
asset: bio://BIO-001/raw/samples  # the governed asset
description: One row per treated well.
extra_columns: forbid             # forbid (default) | allow

columns:
  - name: sample_id
    type: string                  # string (default) | integer | number
    description: Study-scoped identity of the well.
    required: true                # values must be non-blank
    unique: true                  # values must not repeat
    pattern: "^[A-Z0-9]+(?:-[A-Z0-9]+)*-S\\d{3,}$"

  - name: dose
    type: number
    required: true
    minimum: 0                    # numeric columns only

  - name: dose_unit
    type: string
    required: true
    allowed_values: [uM]          # a closed vocabulary

  - name: compound_id
    type: string
    required: false               # blanks are legitimate here
    references:                   # foreign key onto a sibling file
      file: compounds.csv
      column: compound_id
```

Two distinctions matter and are easy to conflate:

**A declared column must always be in the header.** `required` is about
*values*, not the header. A column the contract declares but the file lacks is a
`column_missing` violation regardless of `required`.

**A blank value in a column that permits blanks is not checked further.** A
vehicle control has no compound; running the type, pattern and foreign-key rules
over that absence would report noise, not defects. Leading and trailing
whitespace is stripped before every check, so a whitespace-only cell is blank.

### Rules

| Rule | Trigger |
| --- | --- |
| `column_missing` | A declared column is absent from the header. |
| `column_unexpected` | An undeclared column is present and `extra_columns: forbid`. |
| `required` | A `required` column has a blank value. |
| `type` | A value in an `integer` or `number` column does not parse. |
| `unique` | A value in a `unique` column repeats. |
| `minimum` | A numeric value is below `minimum`. |
| `allowed_values` | A value is outside the declared set. |
| `pattern` | A value does not match the declared regular expression. |
| `foreign_key` | A value is absent from the referenced file, or that file cannot be read. |

## The two contracts

### `contracts/samples.v1.yaml` — `bio.samples@1.0.0`

Governs `<study>/samples.csv`.

| Column | Rules |
| --- | --- |
| `sample_id` | string, non-blank, unique, matches `<STUDY>-S###` |
| `study_id` | string, non-blank, matches an upper-case study code |
| `compound_id` | string, **blank allowed**, matches `CMP-###`, references `compounds.csv` → `compound_id` |
| `treatment` | string, non-blank |
| `dose` | number, non-blank, minimum `0` |
| `dose_unit` | string, non-blank, one of `uM` |
| `tissue` | string, non-blank, one of `liver`, `kidney`, `lung`, `heart` |
| `replicate` | integer, non-blank, minimum `1` |

Extra columns are forbidden: a field arriving unannounced is a contract change,
not a detail.

`compound_id` is where the governance question lives. Vehicle controls are
treated with the solvent alone and reference no test article, so a blank is
correct and must not be reported. A *non-blank* value, however, has to exist in
the study's registry.

### `contracts/compounds.v1.yaml` — `bio.compounds@1.0.0`

Governs `<study>/compounds.csv`, and exists mainly so the registry is a usable
key table for the foreign key above.

| Column | Rules |
| --- | --- |
| `compound_id` | string, non-blank, unique, matches `CMP-###` |
| `compound_name` | string, non-blank, unique |
| `mechanism_class` | string, non-blank |

`mechanism_class` is required but its vocabulary is not policed. Whether a
compound really is a kinase inhibitor is not something a CSV contract can
assert, and pretending otherwise would be the kind of governance theatre this
project is arguing against.

## How foreign-key validation works

A `references` block names a **bare file name and a column**:

```yaml
references:
  file: compounds.csv
  column: compound_id
```

The file is resolved *beside the dataset being validated*. Validating
`data/raw/BIO-001/samples.csv` reads `data/raw/BIO-001/compounds.csv`. Paths and
parent traversal are rejected at load time.

That is the entire resolution rule. There are no data-source connectors, no URIs
to fetch and no registry to look a dataset up in; a generated study is a
directory of files, so a sibling file name is the honest description of the
relationship.

The referenced key column is read once into a set, and each non-blank value is
checked against it. If the referenced file is missing or lacks the key column,
that is a single file-level violation naming the real problem, and every row
then fails the foreign key as a consequence.

## The validation CLI

```bash
uv run bio-gov contract validate contracts/samples.v1.yaml data/raw/BIO-001/samples.csv
uv run bio-gov contract validate contracts/compounds.v1.yaml data/raw/BIO-001/compounds.csv
```

Clean data:

```
Contract: bio.samples@1.0.0
Dataset: data/raw/BIO-001/samples.csv

PASS
Rows checked: 20
```

The same study generated with all four milestone-2 injection options:

```
Contract: bio.samples@1.0.0
Dataset: data/raw/BIO-001/samples.csv

FAIL
Rows checked: 21

4 violations

row 3   sample_id    required     value is blank
row 4   dose         minimum      -1.00 is below 0
row 5   compound_id  foreign_key  CMP-000 not found in compounds.csv column 'compound_id'
row 22  sample_id    unique       duplicate BIO-001-S001 (first seen at row 2)
```

`row` is the line number in the file, so the header is row 1 and the first data
row is row 2 — the number an editor shows. File-level violations print `file`
instead.

| Exit status | Meaning |
| --- | --- |
| `0` | The dataset satisfies the contract. |
| `1` | The dataset breaks the contract. |
| `2` | The contract or the dataset could not be read. |

Every rule is evaluated for every row. Validation never stops at the first
failure: a report that describes one defect out of four is not a report, and the
milestone-2 injections exist precisely to prove the four are found together.

## The result is structured, not printed

The CLI is a renderer. `validate_dataset` returns a
`ContractValidationResult` — contract id and version, the dataset path, the
number of rows checked, and a tuple of `Violation` records each carrying a
`rule`, an optional `column`, an optional `row`, the observed `value` and a
message. The command-line report is one way to display that; a later milestone
emitting quality events or catalogue metadata will read the same object.

```python
from pathlib import Path

from bio_governance.contracts import load_contract, validate_dataset

contract = load_contract(Path("contracts/samples.v1.yaml"))
result = validate_dataset(contract, Path("data/raw/BIO-001/samples.csv"))

result.passed  # False
result.violations[0].rule  # <Rule.REQUIRED: 'required'>
result.violations[0].row  # 3
```

## Contract validation is not a data-quality check

These are different jobs and this milestone only does the first.

**Contract validation is binary and structural.** The file either matches the
agreed shape or it does not. There is no score, no severity and no threshold:
`ContractValidationResult.passed` is a boolean, and a single violation fails the
dataset. It answers "is this file what it claims to be?"

**Data quality is statistical and graded.** Distribution drift, completeness
trending down over time, an outlier dose that is legal but implausible, a
replicate count that has quietly halved — none of those are contract breaches,
and none of them have a clean yes/no answer. They need thresholds, history and
severity, which means they need somewhere to record a series of results.

Collapsing the two would make both worse: a scored contract cannot gate a
pipeline, and a binary quality check has to pick an arbitrary cut-off. The
grading system belongs to the milestone that also brings somewhere to keep the
scores.

## Intentionally deferred

Not in this milestone, and not to be added without being asked:

- **Scoring, severity and thresholds** — see above; quality is its own
  milestone.
- **Contract inheritance, composition or a base contract.** Two contracts is not
  enough evidence to guess at what they share.
- **An expression language.** Rules are named so violations can be named.
- **JSON Schema generation, SQL, or a dataframe layer.** The data is CSV and the
  standard library reads CSV.
- **A contract registry or semantic version resolution.** A contract is a file
  path; `1.0.0` means `1.0.0`.
- **Multiple backends and remote storage.** Files beside files.
- **Validating `study.json` and `expression.csv`.** The contract format
  describes CSV tables; a wide matrix and a JSON document are different shapes
  and neither is needed to demonstrate the idea.
- **Great Expectations, a policy engine, or a plugin system.**
