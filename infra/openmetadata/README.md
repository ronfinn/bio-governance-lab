# Local OpenMetadata

The official Docker Compose quickstart, for the milestone-7 catalogue
integration. Nothing here is committed except this file: the compose file is
downloaded from the OpenMetadata release and the runtime state it creates stays
git-ignored. Upstream is not vendored.

Docker Desktop (or another Docker engine) with **at least 6 GB of memory** is
the documented requirement; the stack below fits in 4 GB with the ingestion
container left out, which this integration does not need.

## Get the compose file

```bash
cd infra/openmetadata
curl -sLO https://github.com/open-metadata/OpenMetadata/releases/download/1.13.4-release/docker-compose.yml
```

## Start

```bash
cd infra/openmetadata
docker compose up -d mysql elasticsearch execute-migrate-all openmetadata-server
```

The `ingestion` service — Airflow, for running OpenMetadata's own connectors —
is deliberately omitted. This project pushes metadata over the REST API and has
nothing for Airflow to schedule.

First start is slow: Elasticsearch takes a couple of minutes to go green, then
migrations run, then the server boots. Compose's dependency wait can give up
before Elasticsearch is healthy on a cold start — if it reports
`dependency failed to start`, wait for `docker ps` to show it healthy and run
the same `up` command again.

Wait for the API:

```bash
curl -s http://localhost:8585/api/v1/system/version
# {"version":"1.13.4", ...}
```

The UI is at <http://localhost:8585>.

## Get a JWT token

`bio-gov catalog openmetadata health` needs no token, but every write does.

Either copy the **ingestion-bot** token from the UI — *Settings → Bots →
ingestion-bot → Token* — or ask the local server for an admin token, which is a
single request against a development instance:

```bash
export OPENMETADATA_JWT_TOKEN="$(curl -s -X POST http://localhost:8585/api/v1/users/login \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@open-metadata.org\",\"password\":\"$(printf %s "$OM_ADMIN_PASSWORD" | base64)\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["accessToken"])')"
```

Set `OM_ADMIN_PASSWORD` in your shell first — it is your local instance's admin
password, and it belongs in neither this repository nor its history. The token
that comes back is short-lived, which is the right shape for a demonstration.

Never commit either token. `.env` and `*.jwt` are git-ignored, and nothing in
this project prints more than a token's last four characters.

## Stop

```bash
cd infra/openmetadata
docker compose stop            # keep the data
docker compose down            # remove the containers, keep the volumes
docker compose down -v         # remove the volumes too
rm -rf docker-volume           # the MySQL data directory compose creates here
```

## Publish

```bash
uv run bio-gov catalog openmetadata health
uv run bio-gov catalog openmetadata publish data/raw/BIO-001 results/BIO-001
uv run bio-gov catalog openmetadata get BIO-001
```

See [docs/openmetadata.md](../../docs/openmetadata.md) for what those publish
and why.
