# Local DataHub

The official Docker quickstart, for the milestone-10 catalogue integration.
Nothing here is committed except this file: the compose file is downloaded by
DataHub's own CLI into `~/.datahub/quickstart/` and the runtime state it creates
stays in Docker volumes. Upstream is not vendored, and nothing DataHub writes
belongs in this repository.

The CLI that starts it, `datahub`, is already a dependency of this project —
`acryl-datahub` is what `bio-gov catalog datahub publish` publishes through — so
there is nothing extra to install.

## Memory

DataHub's quickstart **refuses to start** below 4.3 GB of Docker memory, and
that is a hard check rather than a warning:

```
Total Docker memory configured 3.92GB is below the minimum threshold 4.3GB.
```

Docker Desktop → Settings → Resources → Memory. 6 GB is enough for the six
containers below with room to publish into them.

If OpenMetadata is running, stop it first — the two stacks together will not fit
on a laptop. `docker compose stop` in `infra/openmetadata` keeps its data.

## Start

```bash
uv run datahub docker quickstart --version v1.7.0
```

First start pulls several GB of images and then brings the stack up in order:
OpenSearch, MySQL and Kafka, a `system-update` job that creates the indices and
runs the schema migrations, then GMS, the frontend and the actions container.
Allow ten minutes or so on a cold start — GMS alone takes a couple of minutes to
open its port after the job finishes. Subsequent starts are quick.

Seven containers, at `v1.7.0`:

| container | image |
| --- | --- |
| `datahub-opensearch-1` | `opensearchproject/opensearch` |
| `datahub-mysql-1` | `mysql` |
| `datahub-kafka-broker-1` | `confluentinc/cp-kafka` |
| `datahub-system-update-quickstart-1` | `acryldata/datahub-upgrade` (a setup job) |
| `datahub-datahub-gms-quickstart-1` | `acryldata/datahub-gms` |
| `datahub-frontend-quickstart-1` | `acryldata/datahub-frontend-react` |
| `datahub-datahub-actions-quickstart-1` | `acryldata/datahub-actions` |

GMS is the only one this project talks to.

Check it from this project:

```bash
uv run bio-gov catalog datahub health
# GMS: http://localhost:8080
# Token: not set (a default local DataHub does not require one)
# DataHub: <version>
```

The UI is at <http://localhost:9002>, behind the quickstart's own documented
default credentials (`datahub` / `datahub`) — a local demonstration login, not a
secret, and not one this project reads. The metadata service (GMS), which is
what this project talks to, is at <http://localhost:8080>.

## Publish

```bash
uv run bio-gov catalog datahub health
uv run bio-gov catalog datahub publish data/raw/BIO-001 results/BIO-001
uv run bio-gov catalog datahub get BIO-001
```

See [docs/datahub.md](../../docs/datahub.md) for what those publish and why, and
for the modelling differences from OpenMetadata.

## Authentication

A default local quickstart has metadata-service authentication switched off, so
no token is needed. If you enable it, mint a personal access token in the UI —
*Settings → Access Tokens* — and export it:

```bash
export DATAHUB_GMS_TOKEN=...     # never a flag, never committed
```

`DATAHUB_GMS_URL` overrides the GMS location if it is not on `localhost:8080`.
Never commit a token; nothing in this project prints more than a token's last
four characters.

## Stop

```bash
uv run datahub docker quickstart --stop   # stop the containers, keep the data
uv run datahub docker nuke                # remove the containers and the volumes
```

`--stop` is the one to use between sessions. `nuke` deletes every published
entity along with the deployment, which is fine — publishing is idempotent, so
`bio-gov catalog datahub publish` puts the study back exactly as it was.

## Both catalogues

The two integrations are independent and neither pipeline step needs either, but
they do not fit in memory at the same time on a small machine. Stop one before
starting the other:

```bash
cd infra/openmetadata && docker compose stop
uv run datahub docker quickstart --version v1.7.0
```

and the other way round:

```bash
uv run datahub docker quickstart --stop
cd infra/openmetadata && docker compose up -d mysql elasticsearch openmetadata-server
```
