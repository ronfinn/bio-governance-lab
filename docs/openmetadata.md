# OpenMetadata

Milestone 7 publishes the assets the earlier milestones produced into a local
[OpenMetadata](https://open-metadata.org) instance, so the governance record is
discoverable by somebody who does not already know this repository exists.

```
synthetic study → contracts → data quality → curated data → OpenLineage JSONL
                                                                    │
                                                                    ▼
                                                            ┌──────────────┐
                                                            │ OpenMetadata │
                                                            └──────────────┘
```

Publication is an explicit post-run command. The Nextflow pipeline does not
contact OpenMetadata and still runs with the server switched off.

## Local Docker deployment

The server is the official Docker Compose quickstart, run from
`infra/openmetadata/`. That directory holds a README and nothing else: the
compose file is downloaded from the OpenMetadata release rather than vendored,
and it and its `docker-volume/` runtime state are git-ignored. See
[infra/openmetadata/README.md](../infra/openmetadata/README.md) for start, stop
and token commands.

The version this milestone was built and demonstrated against is **1.13.4**, the
latest 1.x stable release at the time. It is not 1.12.6: that release has been
superseded several times over, and 2.0.0 was eight days old, which is not what
"current stable quickstart" should mean for a deployment somebody else will
reproduce.

## Configuration

Two environment variables, and no configuration file:

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENMETADATA_HOST` | `http://localhost:8585/api` | API root. Every route is `/v1/...` beneath it. |
| `OPENMETADATA_JWT_TOKEN` | — | JWT for a bot or admin user. |

A token is never a command-line flag, so it cannot end up in a shell history or
a process listing, and it is never committed, logged or echoed. `health` reports
only that a token is set, its length and its last four characters — enough to
tell "no token" from "the wrong token", and not enough to be one.

Reachability is not an authenticated question, so `health` works without a
token: while a token is being obtained, "is the server up?" is exactly the
question worth being able to ask. Every write demands one, and the error names
the variable.

## Why containers, and why CustomStorage

Our governed assets are generated **files** — three CSVs, a JSON report — sitting
in a directory. OpenMetadata's model for that is a **container** belonging to a
**storage service**, so a study is published as seven containers of one service:

```
StorageService  bio_governance_lab   (serviceType: CustomStorage)
    Container   BIO-001_raw_samples
    Container   BIO-001_raw_compounds
    Container   BIO-001_raw_expression
    Container   BIO-001_curated_samples
    Container   BIO-001_curated_compounds
    Container   BIO-001_curated_expression
    Container   BIO-001_quality_dq-report
```

The service type is `CustomStorage` because that is the vocabulary's own answer
for a store OpenMetadata has no connector for. Registering the files as MySQL,
PostgreSQL or Snowflake tables would put a false statement into the catalogue,
and the whole point of the project is that the catalogue's statements are true.

There is nothing for OpenMetadata to connect *to*: the connection config carries
only its own discriminating `type`. This project pushes metadata; it does not ask
the catalogue to go and crawl a filesystem it cannot see.

## Two identity schemes, and why both survive

`bio://` is this project's identity for a governed asset. An OpenMetadata FQN is
the catalogue's address for an entity. They answer different questions, so
neither replaces the other:

| | Example | Owns |
| --- | --- | --- |
| `bio://` URI | `bio://BIO-001/raw/samples` | What the asset *is*, across contracts, quality reports and OpenLineage events. |
| Entity name | `BIO-001_raw_samples` | A name OpenMetadata's naming rules accept. |
| FQN | `bio_governance_lab.BIO-001_raw_samples` | Where the entity lives in *this* catalogue. |
| `fullPath` | `bio://BIO-001/raw/samples` | The canonical identity, carried into the catalogue unchanged. |

The derivation is one-way and deterministic — domain and path segments joined
with underscores, because an entity name may carry neither a scheme nor slashes
— and the canonical URI is then stored verbatim in the container's `fullPath`,
where it stays visible and searchable. An FQN is scoped to one deployment; move
to a second OpenMetadata and the FQNs change while the `bio://` identifiers do
not. That asymmetry is the reason `AssetIdentifier` was not replaced by an FQN.

## What is published per container

- the deterministic entity name and a readable display name
- a description saying what the file holds and what wrote it
- `fullPath` — the canonical `bio://` identifier
- `fileFormats` — `csv` for the three datasets, `json` for the report
- `size` in bytes and `numberOfObjects`
- a `dataModel` of columns, for `samples` and `compounds` only

The columns come from the shipped YAML contracts, not from a CSV header: the
contract is the file's *declared* structure, so publishing it puts the agreed
shape in the catalogue rather than whatever a header happened to say. Contract
types map to OpenMetadata's in three entries — `string→STRING`, `integer→INT`,
`number→DOUBLE` — which is a mapping, not a type system.

`expression.csv` is a wide generated matrix and has no contract. It is published
without a data model rather than with several hundred catalogue columns nobody
would read.

## Lineage

Six edges, all between containers, all explainable in a sentence:

```
raw/samples     ──→ curated/samples      ─┐
raw/compounds   ──→ curated/compounds     │ CURATE copies each raw file
raw/expression  ──→ curated/expression   ─┘

raw/samples     ──┐
raw/compounds   ──┼─→ quality/dq-report    the report judges all three files
raw/expression  ──┘
```

Nothing is inferred from the OpenLineage events. A run event lists three inputs
and four outputs, whose cross product is twelve edges, and this project can
explain six of them; the other six would be the catalogue asserting a dependency
nobody checked. So the events are read for one thing — the run ID, reported in
the summary so a reader can find the JSONL matching what the catalogue now holds
— and never turned into edges.

No OpenMetadata `Pipeline` entity is created yet. A pipeline entity wants a
pipeline service and a run history to be worth having, and the run history lives
in the OpenLineage events at present.

## Commands

```bash
# is the server up, and is a token configured?
uv run bio-gov catalog openmetadata health

# publish a study's seven assets and six edges
uv run bio-gov catalog openmetadata publish data/raw/BIO-001 results/BIO-001

# read them back out of the catalogue
uv run bio-gov catalog openmetadata get BIO-001
```

`publish` takes the raw study directory and the pipeline's results directory. It
expects `samples.csv`, `compounds.csv` and `expression.csv` in the first, and
`curated/`, `quality/dq-report.json` and `lineage/openlineage.jsonl` in the
second. Every file the catalogue is about to claim exists is checked *before* a
single request is sent, so a failed publication leaves nothing half-catalogued.

`get` is the verification half: it asks the catalogue what it holds rather than
trusting what was sent, fetching each container by FQN and the raw samples
container's lineage through the API.

Exit status is 0 on success and 2 when the catalogue could not be reached, the
token was rejected, or a claimed file is missing. There is no exit status 1:
publication has no failure *verdict* to report, in the way that a contract or a
quality run does.

## Idempotence

Every write is a `PUT`, and OpenMetadata's `PUT` routes are create-or-update.
Publishing twice therefore addresses the same entities rather than creating a
second set — for containers, for the service and for lineage edges alike — and
idempotence is a property of the requests rather than of bookkeeping this project
does. After two consecutive publications the service holds seven containers, all
still at entity version `0.1`, and `raw/samples` has two downstream edges.

## REST, not the SDK

OpenMetadata ships an official Python SDK, `openmetadata-ingestion`. This
project does not use it. Resolving it for this environment pulls in around 130
transitive packages — dbt-core, boto3, grpcio, numpy and the Kubernetes client
among them — to issue five kinds of request against four documented endpoints,
against a project whose entire dependency list otherwise fits on one line. The
REST API is the same interface the SDK calls, so the client calls it directly
over `httpx`:

| Purpose | Request |
| --- | --- |
| health | `GET /v1/system/version` |
| storage service | `PUT /v1/services/storageServices` |
| container | `PUT /v1/containers` |
| lineage edge | `PUT /v1/lineage` |
| read back | `GET /v1/containers/name/{fqn}` |
| read lineage | `GET /v1/lineage/container/name/{fqn}` |

The SDK becomes the right answer when this project needs ingestion workflows,
connectors or the entity models themselves. Publishing seven containers is not
that.

## Testing

`tests/test_catalog.py` needs no server: the HTTP layer is mocked with `respx`
and a fake OpenMetadata that keys entities the way the real one does, so a
duplicate would show up as a second entry. What is asserted is what this project
controls — the configuration defaults, the clear error when a token is missing,
the deterministic entity-name mapping, the seven prepared assets, the preserved
`bio://` identity, the file formats, the six-edge set, useful messages for
connection failures and rejected tokens, and that a second publication sends the
same requests as the first.

`tests/test_catalog_live.py` is the live demonstration, skipped unless
`OPENMETADATA_INTEGRATION_TEST=1`:

```bash
export OPENMETADATA_JWT_TOKEN=...
OPENMETADATA_INTEGRATION_TEST=1 uv run pytest tests/test_catalog_live.py
```

CI never starts OpenMetadata and never needs it.

## Deferred

- **Pipeline wiring.** Catalogue publication stays a post-run command; the
  Nextflow pipeline must keep running with the server off.
- **No OpenMetadata `Pipeline` entity**, no test-case or data-quality entities,
  no glossary, tags, tiers or owners, and no custom properties.
- **No DataHub, Marquez, MCP or AI-agent governance.**
- **No sync daemon and no reconciliation.** Publication is something a person or
  a later orchestration step runs; nothing polls, and nothing deletes a
  container whose file has gone.
- **No catalogue abstraction.** There is one integration. An interface with one
  implementation is a guess about the second.
