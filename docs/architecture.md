# Architecture

## Scope of this milestone

This repository is at milestone 4: a tested Python foundation, a deterministic
generator for a small synthetic study, YAML data contracts validated against the
generated CSVs, and a Nextflow pipeline that runs those contracts as a gate in
front of curation. No quality score is computed and no external catalogue is
contacted.

Everything that follows is designed to be added *on top of* this model rather
than to replace it.

## Layout

```
src/bio_governance/
    __init__.py            package version
    cli.py                 Typer entry point (bio-gov)
    models/
        __init__.py        public model exports
        enums.py           controlled vocabularies
        identifiers.py     AssetIdentifier (bio:// URIs)
        governance.py      Asset, Ownership, Provenance, ContractReference
    synthetic/
        __init__.py        public generator exports
        generator.py       deterministic synthetic study generation
    contracts/
        __init__.py        public contract exports
        models.py          DataContract, ColumnContract, Violation, result
        loader.py          YAML -> DataContract, with clear load failures
        validator.py       applying a contract to a CSV file
contracts/                 the contract definitions themselves (YAML)
pipelines/nextflow/
    main.nf                contract-gated curation workflow (DSL2)
    nextflow.config        parameters, manifest, error strategy
tests/                     pytest suite
data/                      generated output (git-ignored)
results/                   pipeline output (git-ignored)
docs/                      architecture and governance notes
.github/workflows/ci.yml   lint, format, type-check, test
```

The `src/` layout is deliberate: tests import `bio_governance` from the
installed package, so a packaging mistake fails the test run instead of hiding
behind the working directory.

## Decisions

**Pydantic v2 for the domain model.** Governance metadata has to survive a round
trip through JSON — into a catalogue, a contract file, a lineage event — so the
model needs validation and serialization from the same declaration. Pydantic
gives both, and its errors are specific enough to be useful in CI output.

**Models are frozen.** Every model sets `frozen=True`. A governance record
describes an asset at a point in time; changing it in place makes provenance
meaningless. Frozen models are also hashable, so identifiers work as dictionary
keys and set members. Producing a changed record means constructing a new one.

**Identifiers are a parsed type, not a string.** `AssetIdentifier` validates the
`bio://<domain>/<path>` form on construction, so a malformed identifier fails at
the boundary rather than deep inside a pipeline. It accepts its URI string on
input and serializes back to that string, which keeps the wire format flat and
readable while keeping `domain` and `path` addressable in code.

**`StrEnum` for controlled vocabularies.** Enums make the valid set explicit and
reject typos at validation time, and the string values stay legible in JSON,
catalogue UIs and logs without a translation layer.

**Typer for the CLI.** The CLI is the eventual surface for validation and
reporting commands. Typer derives it from type hints, so the signatures stay
checkable by mypy rather than drifting from hand-written argument parsing.

**uv for environments.** One lock file, one command (`uv sync`) locally and in
CI, so a green CI run means the same dependency set a contributor has.

**Strict mypy.** The whole point of the project is that governance rules are
machine-checkable. Loose typing in the tool making that argument would undercut
it.

**Generated data is reproducible, not stored.** The synthetic study is a fixture
for later milestones, and a fixture that drifts is worse than no fixture. The
generator is a pure function of its arguments — one seeded `random.Random`, a
fixed consumption order, no clock and no timestamps — so identical inputs give
identical bytes. That makes the output disposable: `data/` is git-ignored and
regenerated on demand instead of being committed and going stale.

**Malformed data is generated, never validated here.** The `--inject-*` options
write specific defects into `samples.csv`. Keeping detection out of the
generator means the quality milestone has an independent subject to check rather
than grading its own homework, and `study.json` records which defects were
injected so that check has an answer key.

**Contracts are data, not code.** A contract lives in `contracts/*.yaml`, not
in a Python module. That is the whole argument of governance-as-code: the
agreement about what a dataset must contain is a reviewable, diffable artefact
that a non-engineer can read in a pull request, and the validator is a generic
thing that applies it. Putting the rules in Python would make every contract
change a code change.

**The contract vocabulary is closed and small.** Columns, types, required,
unique, minimum, allowed values, pattern, foreign key. No expression language,
no inheritance, no plugins. The constraint is deliberate: every rule has to be
reportable as a *named* violation — `minimum`, `foreign_key` — because a
governance report that says "an expression returned false" tells a data steward
nothing. An expression language would also be a second, untested programming
language living inside YAML.

**The validator does not import the generator's models.** `Sample` and
`Compound` describe what the generator writes; the contract describes what the
file must contain. If validation reused the generator's models, a passing run
would prove only that the generator is self-consistent — the generator would be
grading its own homework, which is exactly what milestone 2 avoided by keeping
detection out of it. The two descriptions agreeing is the finding.

**Validation is binary, and reports everything.** A contract result is a boolean
plus a list of violations; there is no score, severity or threshold. Structural
conformance has a yes/no answer, and something with a yes/no answer can gate a
pipeline. Grading — drift, completeness trends, plausibility — genuinely needs
thresholds and history, so it belongs to the quality milestone that also has
somewhere to record a series of results. Separately, every rule runs against
every row: a report naming one of four defects would send a steward round the
loop four times.

**Foreign keys resolve to a sibling file, and nothing else.** A `references`
block names a bare file name and a column, resolved next to the dataset being
validated. A generated study is a directory of files, so that is the honest
description of the relationship. Anything more — URIs, connectors, a registry to
look datasets up in — would be inventing an integration surface before there is
an integration to shape it.

**The pipeline calls the CLI, not the library.** Each gate process runs
`bio-gov contract validate` as a subprocess rather than importing
`validate_dataset`. The exit code is the interface — that is what the CLI's `0`,
`1`, `2` were for — and it is the interface every other orchestrator understands
too, so the contract gate is not coupled to Nextflow, to Python, or to this
process model. It also means the gate a reviewer reads in `main.nf` is exactly
the command they can run by hand.

**The gate is enforced by the dataflow, not by a check inside the step.**
`CURATE` takes its input from `CONTRACT_GATE_SAMPLES`'s output channel, which in
turn takes its input from `CONTRACT_GATE_COMPOUNDS`. There is no path by which a
raw file reaches curation without both gates having succeeded first, so the
guarantee is structural rather than a conditional somebody could remove. The
processes are named `CONTRACT_GATE_*` for the same reason: the run log should
show a reader where governance happened.

**The curation step is deliberately trivial.** `CURATE` copies three files into
`curated/`. The pipeline exists to demonstrate that governance can stop
processing, and a plausible-looking scientific transformation would only add
code that nobody can check and distract from the one claim being made. Nothing
about the gate changes when a real step replaces it.

**Local execution, and no executor configuration.** `nextflow.config` sets
parameters, a manifest and `errorStrategy = 'terminate'`, and nothing else. A
retry strategy would be actively wrong here — a contract violation is a verdict,
not a transient failure — and Kubernetes, cloud executors and container
registries would be infrastructure choices made before there is a workload to
shape them.

**Nextflow stays outside the Python environment.** It is a JVM tool installed
separately, so it is not a project dependency and CI does not install it. The
pipeline tests run it for real when it is on `PATH` and skip when it is not;
the static assertions about parameters and process names run everywhere.

**PyYAML, and nothing else new.** A YAML parser is the one thing reading a
contract requires that the standard library does not provide. Reading CSV,
matching patterns and comparing numbers it does provide, so no dataframe or
validation framework was added to do them.

**The generator borrows from the domain model without bending it.** It reuses
`AssetIdentifier` — `study.json` records the `bio://` identity of each file it
writes — and its own record types (`Compound`, `Sample`, `StudyMetadata`) follow
the same frozen-Pydantic conventions. `Asset` itself is untouched: describing a
row of a CSV is not what it is for.

## Deliberate non-goals for now

No repository or service abstraction layer, no plugin system, no configuration
framework, and no base classes beyond Pydantic's. Those interfaces should be
shaped by the first real integration, not guessed at ahead of it.

## Where this is heading

Later milestones will add data-quality checks, OpenLineage events, and
catalogue integration with OpenMetadata and DataHub, followed by MCP and
AI-agent governance. Each of those is expected to consume the
`Asset` model rather than define its own, and to run against the synthetic study
— including the deliberately broken versions of it.

`ContractValidationResult` is the seam those milestones are expected to use. It
is a structured object, not printed text, so emitting a quality event or setting
an `Asset.quality_status` reads the result rather than parsing a report. The CLI
is only one renderer of it.

The pipeline is the second seam. A lineage milestone has a place to emit run and
dataset events from, and a catalogue milestone has a producer of curated assets
to register — both without changing what the gate means.
