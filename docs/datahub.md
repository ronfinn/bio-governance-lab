# DataHub

The second catalogue. The same seven governed assets and the same six lineage
edges that `bio-gov catalog openmetadata publish` sends to OpenMetadata are sent
to a local [DataHub](https://datahubproject.io) Core instance by
`bio-gov catalog datahub publish`, and neither integration knows the other
exists.

That is the point. Publishing the same governance evidence into two catalogues
that model it differently is what makes the comparison in the next milestone a
comparison of *catalogues* rather than of two different studies. The two
integrations sit side by side in `src/bio_governance/catalog/`, sharing the
models and the evidence-reading and nothing else. There is no `CatalogAdapter`,
no `BaseCatalog` and no plugin registry: an interface written before its second
implementation is a guess, and this repository now has the second implementation
to look at instead.

> **A note on the word "MCP".** DataHub's own abbreviation for a *Metadata
> Change Proposal* is MCP. This repository also has a Model Context Protocol
> server, which is also abbreviated MCP. They are unrelated. This document
> writes "Metadata Change Proposal" in full whenever DataHub is meant, and the
> code does the same.

## Two models for the same seven files

The interesting difference is not the API. It is what each catalogue thinks a
thing *is*.

| | OpenMetadata | DataHub |
| --- | --- | --- |
| the container | `StorageService` → `Container` | `DataPlatform` → `Dataset` |
| the unit of a write | an entity, `PUT` whole | an **aspect**, proposed |
| the address | FQN, assigned by the server | URN, derived by the client |
| our identity lives in | `fullPath` | `qualifiedName` + a custom property |
| schema | `dataModel.columns` on the container | a `schemaMetadata` aspect |
| lineage | one `PUT /v1/lineage` per edge, in entity IDs | one `upstreamLineage` aspect per downstream dataset, in URNs |
| what "upsert" means | create-or-update the entity | replace that one aspect |

OpenMetadata models the *storage*: our files are not tables, so they are
containers of a custom storage service. DataHub does not have that distinction —
anything with fields is a Dataset — so the modelling question becomes *which
platform produced it*, and the answer is a dedicated `bio_governance_lab`
platform rather than a borrowed `s3` or `file` one, which would say something
untrue about where the data came from.

The consequence worth noticing is in lineage. OpenMetadata's lineage API takes
one edge at a time and works in server-assigned entity IDs, so the containers
must exist before an edge between them can be described. DataHub's works in
URNs, which are derived rather than assigned, so an upstream can be named before
it exists — but an `upstreamLineage` aspect is the *whole* upstream list of one
dataset, so the quality report's three raw inputs have to be sent together.
Sending them one at a time would leave the report with one upstream and no
error: each write would replace the last. Six edges therefore arrive as four
aspects.

## Identity: `bio://` down, URN never up

The rule is the one the OpenMetadata integration already states, applied to a
second catalogue:

> `bio://` is the project's identity. A catalogue's address is that catalogue's.

So the URI is *derived down* into a dotted dataset name, and carried back
unchanged as data:

```
bio://BIO-001/raw/samples
  dataset name       BIO-001.raw.samples
  URN                urn:li:dataset:(urn:li:dataPlatform:bio_governance_lab,BIO-001.raw.samples,PROD)
  qualifiedName      bio://BIO-001/raw/samples
  canonical_asset_id bio://BIO-001/raw/samples
```

The `bio://` URI *could* have been the dataset name directly — DataHub tolerates
slashes there, and S3 datasets are named by path. It is not, for two reasons.
The URN already names the platform, so a name beginning `bio://` would say it
twice; and DataHub derives browse paths by splitting the name on the platform's
delimiter, which for a `bio://…` name yields a `bio:` segment and an empty one.
The dotted form browses as **BIO-001 → raw → samples**, which is the shape the
governance model actually has.

The derivation is one-way and deterministic. Nothing reads a URN back into a
`bio://` identifier; the canonical URI is carried as data, in two places, so a
reader of the catalogue can get to it without knowing the rule.

## What is published

Seven datasets, one platform, four lineage aspects:

```
DataPlatform  urn:li:dataPlatform:bio_governance_lab
    BIO-001.raw.samples          csv    Raw File         bio://BIO-001/raw/samples
    BIO-001.raw.compounds        csv    Raw File         bio://BIO-001/raw/compounds
    BIO-001.raw.expression       csv    Raw File         bio://BIO-001/raw/expression
    BIO-001.curated.samples      csv    Curated File     bio://BIO-001/curated/samples
    BIO-001.curated.compounds    csv    Curated File     bio://BIO-001/curated/compounds
    BIO-001.curated.expression   csv    Curated File     bio://BIO-001/curated/expression
    BIO-001.quality.dq-report    json   Quality Report   bio://BIO-001/quality/dq-report
```

Each dataset carries:

- **`datasetProperties`** — the display name, the sentence describing what the
  file holds and which gate wrote it, `qualifiedName` set to the `bio://` URI,
  and four or five custom properties: `canonical_asset_id`, `study`,
  `lifecycle_stage`, `file_format` and, where the file was measured,
  `size_bytes`.
- **`subTypes`** — `Raw File`, `Curated File` or `Quality Report`, so the three
  lifecycle stages are distinguishable in a list that would otherwise be seven
  files.
- **`schemaMetadata`**, for `samples` and `compounds` only — the columns their
  YAML contract *declares*, not whatever a header happened to say on the day.
  The expression matrix is wide and generated; hundreds of schema fields would
  be noise, and no contract declares them.

And nothing else. There are no domains, glossary terms, owners, tags, assertions,
data products, structured properties or forms — not because DataHub does them
badly, but because inventing them on one side would distort the comparison the
next milestone is for. Ownership and classification become catalogue entities
when this project produces evidence for them.

The six edges are the six the project can explain in a sentence, exactly as in
OpenMetadata:

```
raw/samples     ──→ curated/samples      ─┐
raw/compounds   ──→ curated/compounds     │ CURATE copies each raw file
raw/expression  ──→ curated/expression   ─┘

raw/samples     ──┐
raw/compounds   ──┼─→ quality/dq-report    the report judges all three files
raw/expression  ──┘
```

Nothing is derived from the OpenLineage events' input-output cross product. The
events are read only for the run ID, which the summary prints so a reader can
find the JSONL corresponding to what the catalogue now holds.

## The SDK, and why here but not there

The OpenMetadata client in this package deliberately does **not** use that
project's SDK. `openmetadata-ingestion` resolves to around 130 transitive
packages — dbt-core, boto3, grpcio, the Kubernetes client — for five kinds of
request against four documented REST endpoints. The decision goes the other way
for DataHub, and the reasoning is worth keeping:

| | acryl-datahub | openmetadata-ingestion |
| --- | --- | --- |
| transitive packages | ~60 | ~130 |
| what it wraps | an aspect model generated from Avro schemas | a REST entity API |
| Python 3.14 | installs and runs clean | not attempted here |
| what hand-rolling costs | re-deriving a generated schema | building four JSON bodies |

The deciding argument is the second row. DataHub's write path is not a REST
entity API with a JSON body a person can read: it is a Metadata Change Proposal
carrying a versioned aspect, and the aspects are code-generated from Avro. Doing
that by hand would mean maintaining a copy of a schema the SDK already holds
correctly — the opposite of the trade the OpenMetadata client makes, where the
REST body *is* the readable thing.

So: **SDK for DataHub, REST for OpenMetadata**, decided per integration on what
each one costs, and now visible side by side in one repository.

Two practical notes from doing it:

- `acryl-datahub` 1.7.0.7 installs and runs on Python 3.14 without a pin, a
  conflict or a build. Its CLI prints a warning that versions above 3.11 are
  not actively tested; nothing in the emitter, the metadata model or the graph
  client misbehaved on it.
- The generated aspect classes carry no annotations on some constructors, so
  mypy's strict mode reads `StringTypeClass()` as an untyped call. That is
  handled with `untyped_calls_exclude = ["datahub"]` in `pyproject.toml` — a
  narrowing of one flag to one package, rather than a `type: ignore` in `src`.

The SDK costs about half a second to import, so `datahub_client.py` and
`datahub_publish.py` are imported *lazily*, inside the CLI commands that need
them, and are not re-exported from `bio_governance.catalog`. The six `bio-gov`
commands the Nextflow pipeline shells out to on every run must not pay for a
metadata model they never send — the same reasoning that already applies to the
Model Context Protocol SDK.

## Configuration

| Variable | Default |
| --- | --- |
| `DATAHUB_GMS_URL` | `http://localhost:8080` |
| `DATAHUB_GMS_TOKEN` | — |

Both are read from the environment; a token is never a flag, so it cannot end up
in a shell history or a process listing. A default local quickstart has
metadata-service authentication switched off, so a token is genuinely optional
here — unlike OpenMetadata, where every write demands one. It is still read, and
still never printed: `health` reports only that one is configured and its last
four characters.

## Commands

```bash
# is the server up?
uv run bio-gov catalog datahub health

# publish a study's seven datasets and six edges
uv run bio-gov catalog datahub publish data/raw/BIO-001 results/BIO-001

# read them back out of the catalogue
uv run bio-gov catalog datahub get BIO-001
```

`get` is the verification half: it fetches each dataset by its URN through the
SDK and prints the `bio://` identifier DataHub actually holds, then every
upstream DataHub actually holds, so the six edges can be confirmed through the
API rather than by looking at the UI. It exits 2 if a dataset is missing.

Exit statuses match the rest of the CLI: 0 for success, 2 when the catalogue
could not be reached or a file the catalogue would have claimed is missing.
There is no exit status 1, because publication has no failure *verdict* — a
catalogue does not judge a study.

## Idempotence

Publishing `BIO-001` twice leaves seven datasets and six edges, not fourteen and
twelve. This is not bookkeeping — nothing here records what was published
before, and nothing reads before it writes:

- Every URN is **derived** from a `bio://` identifier rather than assigned by
  the server, so the second run addresses the same entities as the first.
- Every proposal is an `UPSERT` of one aspect, which replaces that aspect's
  value.

Which is why the four lineage aspects matter: because an `upstreamLineage`
aspect is a replacement, re-publishing cannot accumulate duplicate edges, and
splitting the quality report's three upstreams across three proposals would have
lost two of them every time.

## The pipeline does not call this

Publication is an explicit post-run command, exactly as for OpenMetadata.
`main.nf` is unchanged by this milestone, and the governed pipeline runs to a
verdict with both catalogues switched off. A catalogue that could stop a
pipeline would be a catalogue holding governance hostage.

The Model Context Protocol server is unchanged too. Nothing about DataHub is
exposed to an AI client: the evidence an assistant reads is the evidence on
disk, and adding a second source of it is not this milestone's business.

## Testing

CI never starts DataHub. `tests/test_catalog_datahub.py` fakes the *emitter* —
the SDK boundary — and asserts on what this project actually controls: the
configuration defaults, the `bio://`-to-URN derivation, the seven prepared
datasets, the preserved canonical identity, the file formats, the contract-backed
schemas, the exact six edges as four aspects, useful messages for a stopped
server and a rejected token, and that a second publication proposes the same
upserts against the same URNs.

What is faked is the emitter, not the metadata model. Every proposal the tests
inspect is a real `MetadataChangeProposalWrapper` holding a real aspect class, so
a test cannot pass by agreeing with a dictionary this project invented. One test
asserts that the hand-built URN string equals what the SDK's `make_dataset_urn`
would have produced: the mapping builds URNs as strings so that deriving an
identity costs nothing to import, and that is only safe while the convention
agrees with DataHub's own.

The live demonstration lives in `tests/test_catalog_datahub_live.py` and skips
unless `DATAHUB_INTEGRATION_TEST=1`.

## Deferred

No ingestion sources, recipes or scheduled ingestion — this project *pushes*
metadata, and has nothing for DataHub's connectors to crawl. No Kafka emitter:
the REST emitter is synchronous, and a publication that has not reached the
server is not a publication. No domains, glossary, tags, owners, assertions,
data products, structured properties or forms. No `datahub` actions, no
policies, no soft deletes and no stateful ingestion. No DataHub Cloud.

And no catalogue abstraction. Milestone 11 compares these two integrations; it
does not need them behind an interface to do it, and putting them there first
would hide exactly the differences worth comparing.
