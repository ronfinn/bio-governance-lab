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
reproduce. Milestone 13's live validation and reclassification ran against the
same 1.13.4 quickstart; the integration was not upgraded to do it.

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
- a tag label for the study's classification, from its validated governance
  declaration — `bio_governance_classification.internal` for BIO-001 — set by a
  JSON Patch straight after the container's `PUT` (see
  [Classification lifecycle](#classification-lifecycle))

The columns come from the shipped YAML contracts, not from a CSV header: the
contract is the file's *declared* structure, so publishing it puts the agreed
shape in the catalogue rather than whatever a header happened to say. Contract
types map to OpenMetadata's in three entries — `string→STRING`, `integer→INT`,
`number→DOUBLE` — which is a mapping, not a type system.

`expression.csv` is a wide generated matrix and has no contract. It is published
without a data model rather than with several hundred catalogue columns nobody
would read.

## Classification, and why not ownership

Milestone 12 gave each study a validated governance declaration — owner,
steward, contact and classification — and OpenMetadata receives half of it.

The **classification** has an exact home. OpenMetadata's word for a controlled,
mutually exclusive tag vocabulary is a *Classification*, so publication
upserts one, `bio_governance_classification`, with `mutuallyExclusive: true`
and a tag for each of the project's four values, and each container carries the
declared value as a `Classification`-sourced, `Manual`, `Confirmed` tag label.
The tags are upserted before any container is classified because a container
cannot carry a tag that does not exist.

The **ownership** does not. An OpenMetadata owner is an entity reference whose
`id` is required: the UUID of a User or Group-type Team the server already
holds. The declaration names people, and this project provisions no accounts,
so no owner is sent — and neither of the workarounds is taken: a bot's `PUT`
reverts a changed description on an existing entity, and a custom property
would put a second, disagreeing "owner" beside OpenMetadata's own. Ownership
stays canonical in `governance/studies/`. `publish` refuses, before any request,
a study whose declaration evidence is missing or did not validate. Read back
with `fields=tags,owners`, every container reports `"owners": []` — asked for,
and empty, rather than merely not returned.

## Classification lifecycle

Two different things happen to a container's classification, and milestone 13
separated them after observing both on the live 1.13.4 server.

**Initial publication** — `none → internal`, and republishing `internal →
internal` — worked with the milestone-12 `PUT`, as predicted. On a `PUT`,
OpenMetadata *merges* the request's tags into the ones the container already
holds, then enforces mutual exclusivity, so a `PUT` can add the first value and
re-add the same one harmlessly.

**Reclassification** — `internal → confidential` — did not. Observed, with a
validated `confidential` declaration published over `internal` containers:

```
PUT /v1/containers  →  HTTP 400
Tag labels bio_governance_classification.internal and
bio_governance_classification.confidential are mutually exclusive and can't be
assigned together
```

`bio-gov catalog openmetadata publish` exited 2, and the server's state was
unchanged: the first container's `PUT` was refused, so no container moved. No
form of `PUT` can fix it — sending no `tags`, or `"tags": []`, also leaves the
held tags where they are — because a merge can add a label but never remove one.

So the classification is now set by a different request. The container `PUT`
carries no tags at all, and straight after it `classify_container` sends:

```
GET   /v1/containers/{id}?fields=tags           what the container holds now
PATCH /v1/containers/{id}                       Content-Type: application/json-patch+json
      [{"op": "add", "path": "/tags", "value": [ ...every label outside
         bio_governance_classification, exactly as returned...,
         {"tagFQN": "bio_governance_classification.confidential", ...} ]}]
```

A `PATCH` *replaces* the tag list, so the old value is removed in the same
request that adds the new one, and the server never sees two values of the
mutually exclusive classification at once. The list is computed by
`classified_tags()` in `mapping.py`, which owns exactly one namespace: labels
whose source is `Classification` and whose FQN is under
`bio_governance_classification.`. Everything else on a container — `PII`,
`Tier`, a glossary term, a tag a steward added in the UI — goes back unchanged.
The catalogue's *current* project classification is read only to be discarded;
the value that goes back is always the validated declaration's.

The same `PATCH` is sent on every publication, changed or not. Observed: a
`PATCH` of the tags a container already holds is a no-op — no new version, an
empty change description — so there is no read-then-decide step, and a
republication sends the same requests as the last one.

| Transition | After publication | Observed live |
| --- | --- | --- |
| none → internal | `internal` only | yes |
| internal → internal | `internal` only, nothing changed | yes |
| internal → confidential | `confidential` only; `internal` gone | yes |
| confidential → restricted | `restricted` only; `confidential` gone | yes |
| restricted → public | `public` only; `restricted` gone | yes |

In every row a `PII.NonSensitive` tag applied beforehand, as a steward would,
survives, and publishing the resulting declaration a second time leaves the
read-back — tags, owners, `fullPath` and entity versions — identical.

Two candidates were rejected on evidence. OpenMetadata's bulk
`PUT /v1/tags/{id}/assets/remove` returns a `jobId` and removes the tag in the
background — immediately afterwards the tag was still there — so a publication
would race its own request. And deleting every tag before re-adding the
declared one would destroy exactly the labels this project does not own.

A new container is created untagged and classified by the next request; a
failure between the two leaves one unclassified container and exits 2, and the
next publication classifies it. OpenMetadata's own entity versions are not the
record of any of this: through the whole walk above, containers stayed at
version 0.2, consistent with the server consolidating one user's successive
changes into one version. The history that matters is the declaration's, in
Git.

The full account, with DataHub's contrasting model, is in
[governance-metadata.md](governance-metadata.md).

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

Every write but one is a `PUT`, and OpenMetadata's `PUT` routes are
create-or-update. Publishing twice therefore addresses the same entities rather
than creating a second set — for containers, the service, the classification,
its tags and lineage edges alike. The one other write, the classification
`PATCH`, sets the tag list to a value rather than changing it by a delta, so
repeating it is a no-op. Idempotence is a property of the requests rather than
of bookkeeping this project does: no record of what was published is kept.

Observed on 1.13.4 in milestone 13: two consecutive publications of BIO-001 left
seven containers, one classification with four tags and six edges, with a
read-back identical in every field the tests read, entity versions included.
(The milestone-7 containers were at version 0.1; the first publication that
classified them moved them to 0.2, and identical republications left them
there.)

## REST, not the SDK

(The DataHub integration added in milestone 10 goes the other way and uses
that project's SDK. The two decisions are argued from what each API actually
is — see [datahub.md](datahub.md).)

OpenMetadata ships an official Python SDK, `openmetadata-ingestion`. This
project does not use it. Resolving it for this environment pulls in around 130
transitive packages — dbt-core, boto3, grpcio, numpy and the Kubernetes client
among them — to issue ten kinds of request, six of them writes,
against a project whose entire dependency list otherwise fits on one line. The
REST API is the same interface the SDK calls, so the client calls it directly
over `httpx`:

| Purpose | Request |
| --- | --- |
| health | `GET /v1/system/version` |
| storage service | `PUT /v1/services/storageServices` |
| classification | `PUT /v1/classifications` |
| classification tag | `PUT /v1/tags` |
| container | `PUT /v1/containers` |
| a container's current tags | `GET /v1/containers/{id}?fields=tags` |
| a container's classification | `PATCH /v1/containers/{id}` (one `add` of `/tags`) |
| lineage edge | `PUT /v1/lineage` |
| read back | `GET /v1/containers/name/{fqn}?fields=tags,owners` |
| read lineage | `GET /v1/lineage/container/name/{fqn}` |

One publication of BIO-001 is 33 requests: 1 service, 1 classification, 4 tags,
7 × (container `PUT`, tag `GET`, classification `PATCH`) and 6 edges — 26 of
them writes.

The SDK becomes the right answer when this project needs ingestion workflows,
connectors or the entity models themselves. Publishing seven containers is not
that.

## Testing

`tests/test_catalog.py` needs no server: the HTTP layer is mocked with `respx`
and a fake OpenMetadata that keys entities the way the real one does, so a
duplicate would show up as a second entry. What is asserted is what this project
controls — the configuration defaults, the clear error when a token is missing,
the deterministic entity-name mapping, the seven prepared assets, the preserved
`bio://` identity, the file formats, the six-edge set, the mutually exclusive
classification with its four tags, the declared tag on every container, the
absence of any owner, tags existing before any container is classified, the
refusal of missing, failed or wrong-study governance evidence, useful messages
for connection failures and rejected tokens, and that a second publication sends
the same requests as the first.

The fake's tag semantics are the ones observed live — a `PUT` merges and
refuses a second value of a mutually exclusive classification with the server's
own HTTP 400 message, a `PATCH` replaces — and it ships the system `PII`
classification, as a real server does
(`test_the_fake_refuses_a_put_reclassification_as_the_live_server_did`).
Against that fake, `test_publication_sets_exactly_the_declared_classification`
walks the five transitions in the table above with a steward's `PII` tag on
every container, `test_classified_tags_*` pin the namespace rule without HTTP,
and `test_a_reclassification_that_did_not_validate_sends_nothing` proves a
failed declaration still sends no request. Run against the milestone-12
`PUT`-only publication, the three reclassification cases fail with the same
HTTP 400 the live server returned.

`tests/test_catalog_live.py` is the live demonstration, skipped unless
`OPENMETADATA_INTEGRATION_TEST=1`:

```bash
export OPENMETADATA_JWT_TOKEN=...
OPENMETADATA_INTEGRATION_TEST=1 uv run pytest tests/test_catalog_live.py
```

It publishes BIO-001 twice and asserts an identical read-back, the `bio://`
identity in every `fullPath`, the `internal` tag, `"owners": []` and the six
edges; then it strips the project classification, tags every container
`PII.NonSensitive` as a steward would, and walks `none → internal → internal →
confidential → restricted → public`, publishing each state twice. It finishes by
restoring the committed declaration and removing the steward's tag, so the
deterministic BIO-001 entities are left as the declaration describes them.
Milestone 13 ran it against the local 1.13.4 quickstart: 3 passed.

CI never starts OpenMetadata and never needs it.

## Deferred

- **Pipeline wiring.** Catalogue publication stays a post-run command; the
  Nextflow pipeline must keep running with the server off.
- **No OpenMetadata `Pipeline` entity**, no test-case or data-quality entities,
  no glossary, tiers, owners, users or teams, and no custom properties. One
  Classification and its four tags are the only governance entities.
- **One `PATCH`, not a patch framework.** The only `PATCH` the client sends is
  the single `add` of `/tags` that sets the classification. There is no general
  JSON Patch support, no diffing of entities and no other field patched.
- **No Marquez or AI-agent governance.**
- **No sync daemon and no reconciliation.** Publication is something a person or
  a later orchestration step runs; nothing polls, nothing deletes a container
  whose file has gone, and a classification changed by hand in the UI stays
  changed until the next publication sets it back.
- **No catalogue abstraction.** The DataHub integration sits beside this one,
  not behind a shared interface; reclassification is a further difference
  between them, not a reason to add one (see
  [catalog-comparison.md](catalog-comparison.md)).
