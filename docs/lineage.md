# Lineage

Contracts say a file is well-formed. Data quality says a study is usable. Neither
says where a curated file *came from* — and that is the question a data steward
is asked when a result is questioned six months later.

`bio-gov lineage emit` answers it by writing
[OpenLineage](https://openlineage.io/) events: a standard, versioned description
of which datasets a run read and which it produced.

## Why OpenLineage

Provenance is only worth recording if something else can read it. OpenLineage is
an open specification with a maintained Python client, so the evidence this
project writes is the same shape that Airflow, Spark, dbt and Marquez emit and
consume. The alternative — a bespoke `lineage.json` of our own design — would be
a private format that no catalogue understands and that has to be re-invented
the moment one is introduced.

Concretely: `openlineage-python` is a dependency, the events are built from its
`event_v2` models, and they are written by its own `FileTransport`. Nothing in
`src/bio_governance/lineage/` defines a schema.

## Job, run, dataset

Three terms, and the distinction between the first two is the point.

| Term | Meaning here |
| --- | --- |
| **Job** | The curation *activity*, identified by namespace and name: `bio-governance-lab` / `curate-study`. Stable across every execution. |
| **Run** | One execution of that job, identified by a UUID. New every time. |
| **Dataset** | A thing the run read or wrote, identified by namespace and name. |

A job that changed identity per execution could not be asked "how often does
this fail"; a run that reused an identity could not be asked "what did *that*
one read". Keeping them separate is what makes both questions answerable.

Datasets use the single namespace `bio-governance-lab` and the project's
existing `bio://` identifier as the name:

```
inputs                              outputs
bio://BIO-001/raw/samples     -->   bio://BIO-001/curated/samples
bio://BIO-001/raw/compounds   -->   bio://BIO-001/curated/compounds
bio://BIO-001/raw/expression  -->   bio://BIO-001/curated/expression
                                    bio://BIO-001/quality/dq-report
```

There is deliberately no second identifier convention. `AssetIdentifier` already
validates the `bio://<domain>/<path>` form the generator, `study.json` and the
domain model use, so the lineage layer builds its dataset names from it rather
than inventing a parallel scheme a catalogue would then have to reconcile.

The quality report appears as an output because it is one: `RUN_DATA_QUALITY`
produced it, and it is the evidence that let curation happen at all.

## Raw to curated

The run's inputs are the raw study's three files and its outputs are the curated
copies of them, so the events state exactly the relationship the pipeline
created: *this* curated directory came from *that* raw study, by way of a job
that only runs when both gates pass.

Every file the events name is checked to exist before anything is emitted.
Lineage that claims provenance for a file nobody wrote is worse than no lineage,
because it reads as evidence.

## A run is two events

```
START     eventTime, run ID, job, inputs, outputs
COMPLETE  eventTime, run ID, job, inputs, outputs
```

Both carry the same run ID; that is what makes them one execution rather than
two. Unlike generated data, lineage events are **not** reproducible
byte-for-byte, and should not be: a UUID and a UTC timestamp are precisely what
distinguish this execution from the last one.

## Local JSONL transport

Both events are written as the two lines of one JSON Lines file:

```
results/<STUDY>/lineage/openlineage.jsonl
```

This is OpenLineage's own `FileTransport`, configured with `append` so it writes
the path it was given rather than one file per event. The file is truncated
first, so a run's evidence is that run's two events.

A file, not a server. An HTTP transport to Marquez or a catalogue would be a
deployment to stand up and maintain before there is anything to ask it, and the
JSONL is exactly what such a transport would send — the same events, the same
schema. Swapping transports later is a configuration change, not a rewrite.

## The CLI

```bash
uv run bio-gov lineage emit \
  data/raw/BIO-001 \
  results/BIO-001/curated \
  --quality-report results/BIO-001/quality/dq-report.json \
  --output results/BIO-001/lineage/openlineage.jsonl
```

```
Study: BIO-001
Run ID: 9b000a58-c152-4f67-ac69-df2afe381215
  in   bio://BIO-001/raw/samples
  in   bio://BIO-001/raw/compounds
  in   bio://BIO-001/raw/expression
  out  bio://BIO-001/curated/samples
  out  bio://BIO-001/curated/compounds
  out  bio://BIO-001/curated/expression
  out  bio://BIO-001/quality/dq-report

Lineage: results/BIO-001/lineage/openlineage.jsonl
```

The study identifier is read from the raw directory's name. `--quality-report`
is optional; when given, the report must exist and is added as a fourth output.
`--run-id` reuses an identity instead of minting one, which is what the tests
use to assert on a known value.

Exit status is `0` on success and `2` when a required file is missing or the
events could not be written. There is no `1`: emitting provenance is not a
verdict, so there is nothing for it to fail.

## In the pipeline

`EMIT_OPENLINEAGE` runs last:

```
CONTRACT_GATE_COMPOUNDS -> CONTRACT_GATE_SAMPLES -> RUN_DATA_QUALITY -> CURATE -> EMIT_OPENLINEAGE
```

It takes the curated directory from `CURATE`'s output channel and the report
from `RUN_DATA_QUALITY`'s, so it is unreachable for a run that was stopped at a
gate — the same structural argument that keeps `CURATE` behind the gates. A
contract failure or a quality failure therefore leaves no
`results/<STUDY>/lineage/` directory at all.

## What is deferred

**Failed-run lineage.** A stopped pipeline emits nothing today. OpenLineage has
a `FAIL` state and emitting it would be genuinely useful — a catalogue could
show that a curation was attempted and refused — but it needs the emission to
happen from somewhere that survives the failure, which means either an
`errorStrategy` that does not terminate or a Nextflow completion handler. Both
are decisions about the pipeline's failure semantics, and this milestone
deliberately did not make them.

**Catalogue integration.** No OpenMetadata, DataHub or Marquez, and no HTTP
transport, Kafka or database. The events on disk are what those integrations
would consume; standing one up is its own milestone, with its own questions
about identity mapping and ownership sync.

**Nextflow's own lineage.** Recent Nextflow versions have an experimental
built-in lineage feature (`-with-lineage`, `lineage.enabled`). It is not used
here, on purpose. It describes lineage in Nextflow's terms and is tied to
Nextflow's lifecycle, whereas the point of this milestone is that the provenance
is *orchestrator-agnostic* — the same argument that made the gates shell out to
`bio-gov` rather than import it. Mixing the two would give two lineage records
of one run, with no rule for which is authoritative.

**Richer facets.** Events carry job, run and dataset identities and nothing
else: no schema facet, no column-level lineage, no data-quality facet. Each is
worth adding when something reads it, and inventing facets nobody consumes is
how a lineage layer becomes decoration.
