# Architecture

## Scope of this milestone

This repository is at milestone 6: a tested Python foundation, a deterministic
generator for a small synthetic study, YAML data contracts validated against the
generated CSVs, study-level data-quality checks over the study as a whole, a
Nextflow pipeline that runs both as gates in front of curation, and OpenLineage
events recording what a successful run produced. No history is kept and no
external catalogue is contacted.

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
    quality/
        __init__.py        public quality exports
        models.py          QualityCheck, status, check result, report
        checks.py          the six study-level checks
    lineage/
        __init__.py        public lineage exports
        openlineage.py     job/dataset identity, START and COMPLETE emission
contracts/                 the contract definitions themselves (YAML)
pipelines/nextflow/
    main.nf                gated curation and lineage workflow (DSL2)
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

**Data quality is a second layer, not more contract rules.** A contract sees one
file, one row at a time; a quality check sees a study. Deleting the vehicle
controls from `samples.csv` breaks no contract rule — every remaining row is as
well-formed as it was — and leaves a study whose treatments have nothing to be
compared against, whose row count contradicts `study.json`, and whose expression
matrix measures samples the manifest has forgotten. None of that is visible from
inside a row. The corollary is a rule the checks follow strictly: a defect a
per-row contract rule already covers is not re-checked here, because two layers
enforcing the same thing means neither owns it.

**The quality checks do not import the generator either.** `VEHICLE_TREATMENT`
and the four file names are restated in `quality/checks.py`, and `study.json` is
read as plain JSON rather than through `StudyMetadata`. The argument is the one
that kept the contract validator independent: if the thing that wrote the data
also supplied the expectations, a passing report would say only that the
generator agrees with itself.

**A quality verdict is a status, not a score.** Three values — PASS, WARN, FAIL
— and `overall_status` is derived from the checks rather than stored, so a
report cannot claim a verdict its checks do not support. A number would need
weights nobody agreed on, would hide which check failed, and could not gate
anything without a threshold that is itself an ungoverned decision. `WARN`
exists for a finding that must not stop a pipeline; none of the six checks emits
one yet, and the alternative — a later non-blocking check choosing between
silence and failing the gate — is worse than an unused status.

**Repetitive defects are counted, not enumerated.** `expression_completeness`
reports "24 of 240 measurements are blank or not a finite number" rather than 24
findings. A matrix has thousands of cells, and the per-row reporting that works
for a contract would produce a report nobody reads.

**The pipeline calls the CLI, not the library.** Each gate process runs
`bio-gov contract validate` or `bio-gov dq run` as a subprocess rather than
importing `validate_dataset` or `evaluate_study`. The exit code is the interface
— that is what the CLI's `0`, `1`, `2` were for — and it is the interface every
other orchestrator understands too, so the gates are not coupled to Nextflow, to
Python, or to this process model. It also means the gate a reviewer reads in
`main.nf` is exactly the command they can run by hand.

**The gates are enforced by the dataflow, not by a check inside a step.**
`CURATE` takes its input from `RUN_DATA_QUALITY`'s output channel, which takes
its input from `CONTRACT_GATE_SAMPLES`, which takes its input from
`CONTRACT_GATE_COMPOUNDS`. There is no path by which a raw file reaches curation
without every gate having succeeded first, so the guarantee is structural rather
than a conditional somebody could remove. Structure is checked before
consistency because a malformed file cannot meaningfully be assessed for
consistency. The processes are named `CONTRACT_GATE_*` and `RUN_DATA_QUALITY`
for the same reason: the run log should show a reader where governance happened.

**The quality report is written before the exit status is decided.** A failing
study is exactly the one whose evidence somebody wants, so `--json-out` produces
a file either way. Nextflow will not publish the outputs of a process that
exited non-zero, so a failed gate's report stays in the work directory and the
readable form stays in the log — which is where a failure is read from anyway.

**The curation step is deliberately trivial.** `CURATE` copies three files into
`curated/`. The pipeline exists to demonstrate that governance can stop
processing, and a plausible-looking scientific transformation would only add
code that nobody can check and distract from the one claim being made. Nothing
about the gate changes when a real step replaces it.

**Lineage is OpenLineage, not a format of our own.** Provenance is only worth
recording if something other than this repository can read it. OpenLineage is an
open, versioned specification with a maintained Python client, and the events
here are built from its `event_v2` models and written by its own
`FileTransport` — nothing in `lineage/` defines a schema. A bespoke
`lineage.json` would have been quicker and would have had to be thrown away the
first time a catalogue was introduced.

**A stable job, a fresh run.** The job — `bio-governance-lab` / `curate-study` —
is the curation *activity* and never changes; a run is one execution of it, a
new UUID each time, described by a START and a COMPLETE event that share that
ID. Collapsing the two would make one of the two obvious questions
unanswerable: a job that changed identity per execution cannot be asked how
often it fails, and a run that reused one cannot be asked what *that* execution
read.

**Datasets reuse `AssetIdentifier`, in one namespace.** A dataset's OpenLineage
name is the `bio://<STUDY>/<stage>/<dataset>` URI the generator, `study.json`
and the domain model already use, built through `AssetIdentifier` rather than
formatted by hand. The namespace is the single string `bio-governance-lab`,
because the URI already carries the study and the lifecycle stage. Minting a
second identifier convention for lineage would guarantee a reconciliation
problem the moment a catalogue had to join the two.

**Lineage is the one thing here that is deliberately not reproducible.**
Generated data is a pure function of its arguments; a lineage event is not, and
must not be. A run ID and a UTC timestamp are exactly what make an event
describe *this* execution rather than the last one, so the tests assert on event
structure — states, identities, dataset names — and never on bytes.

**A file, not a server.** `FileTransport` in append mode writes both events as
the two lines of `results/<STUDY>/lineage/openlineage.jsonl`. An HTTP transport
to Marquez or a catalogue would be infrastructure to stand up and maintain
before there is anything to ask it, and it would send these same events under
this same schema — so swapping later is configuration, not a rewrite.

**Provenance is only claimed for files that exist.** Every raw and curated file
the events name is checked before anything is emitted; a missing one is a
`LineageError` and exit status 2. Lineage that asserts a file was produced when
it was not is worse than no lineage, because it reads as evidence. There is no
exit status 1: emitting provenance is a record, not a verdict, so it has nothing
to fail.

**Failed runs emit nothing, for now.** `EMIT_OPENLINEAGE` consumes `CURATE`'s
output channel, so a run stopped at a gate never reaches it and no
`lineage/` directory is published. OpenLineage has a `FAIL` state and emitting
it would be useful, but doing so requires emission from somewhere that survives
the failure — a softened `errorStrategy` or a completion handler — and both are
decisions about the pipeline's failure semantics that this milestone chose not
to make on the way past.

**Nextflow's own experimental lineage is deliberately not used.** Recent
versions ship a built-in lineage feature. It describes a run in Nextflow's terms
and is tied to Nextflow's lifecycle, whereas the argument this milestone is
making is that provenance is orchestrator-agnostic — the same argument that made
the gates shell out to `bio-gov` rather than import it. Running both would
produce two lineage records of one run with no rule for which is authoritative.

**Events carry identities and nothing else.** No schema facet, no column-level
lineage, no data-quality facet. Facets are worth adding when something reads
them; adding them first is how a lineage layer becomes decoration that drifts
from the data it claims to describe.

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

Later milestones will add catalogue integration with OpenMetadata and DataHub,
followed by MCP and AI-agent governance. Each of those is expected to consume the
`Asset` model rather than define its own, and to run against the synthetic study
— including the deliberately broken versions of it.

`ContractValidationResult` is the seam those milestones are expected to use. It
is a structured object, not printed text, so emitting a quality event or setting
an `Asset.quality_status` reads the result rather than parsing a report. The CLI
is only one renderer of it.

`QualityReport` is the same kind of seam, and the one an `Asset.quality_status`
should be set from. It is already serialized as JSON evidence beside every run,
so a catalogue integration reads a file rather than re-deriving a verdict.

The pipeline is the third seam. The lineage milestone emitted its run and
dataset events from the end of it without changing what the gates mean, and a
catalogue milestone has a producer of curated assets to register on the same
terms.

`openlineage.jsonl` is the fourth. It is already the wire format a catalogue
would be sent, so an OpenMetadata or DataHub integration reads events off disk —
or swaps the transport — rather than re-deriving the raw-to-curated relationship
from the pipeline.
