# Architecture

## Scope of this milestone

This repository is at milestone 2: a tested Python foundation plus a
deterministic generator for a small synthetic study. No pipeline runs, no
contract is enforced, no quality check executes, and no external catalogue is
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
tests/                     pytest suite
data/                      generated output (git-ignored)
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

Later milestones will add data contracts, quality checks, Nextflow
orchestration, OpenLineage events, and catalogue integration with OpenMetadata
and DataHub, followed by MCP and AI-agent governance. Each of those is expected
to consume the `Asset` model rather than define its own, and to run against the
synthetic study — including the deliberately broken versions of it.
