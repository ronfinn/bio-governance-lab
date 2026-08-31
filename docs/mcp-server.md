# The MCP governance server

Eight milestones built a machine that decides. Contracts said whether each file
conformed to its declared structure. Data quality said whether the study hung
together across its files. The pipeline gated curation on both. OpenLineage
recorded what a successful run produced. OpenMetadata made it discoverable. The
governance layer read all of that back and returned one word: `READY`, `REVIEW`
or `BLOCKED`.

This milestone does one thing: it lets an AI assistant *read* that.

```bash
uv run bio-gov mcp serve
```

A [Model Context Protocol](https://modelcontextprotocol.io) server, over stdio,
exposing six tools and two resources over the evidence in `results/`. Any MCP
host — Claude Desktop, an IDE extension, a script holding an MCP client — can
now ask a governed study what its decision is and what stands behind it.

## Deterministic code decides. AI explains.

The principle the governance milestone established is the reason this one is
shaped the way it is. It is worth being precise about what "read-only" means
here, because it is a stronger claim than "no `write_file` tool".

There is no tool on this server that computes a governance decision, overrides
one, approves an asset, edits a quality report, rewrites a contract result,
emits lineage, publishes to a catalogue, or writes any file at all. Every tool
is a reader, and each is annotated `readOnlyHint` so a host sees that before it
calls anything:

```python
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
```

`open_world_hint` is false because the answers come from one local directory and
no network at all.

But the guarantee does not rest on the tool list, which is only a promise about
what was built. It rests on where the decision comes from.
`get_governance_report` does not read a `"decision"` field out of a JSON file
and pass it on. It deserializes the file into the evaluator's own
`GovernanceReport`, whose `decision` is a **computed field** derived from the
checks:

```python
report = GovernanceReport.model_validate(json.loads(path.read_text()))
```

The `"decision"` the file happens to carry is never read. Edit
`governance-report.json` by hand to claim `"decision": "ready"` while
`lineage_evidence` reads `fail`, and the MCP server still answers `blocked` —
because it recomputes nothing and reads nothing but the checks. A model
consuming this server can describe a verdict and has no route to the thing that
produced it. That is tested directly, in
`test_the_decision_is_derived_even_when_the_evidence_file_claims_otherwise`.

```
Nextflow
   ↓  runs the gates
evidence files          contracts/, quality/, curated/, lineage/
   ↓  read by
deterministic governance engine     bio-gov governance evaluate
   ↓  writes
governance/governance-report.json
   ↓  read by
MCP server              bio-gov mcp serve   (read-only)
   ↓  serves
MCP host / AI assistant
```

Every arrow points down. There is no arrow back up, and this milestone is the
one where somebody would have been tempted to draw one.

## The tools

Six, all read-only, all confined to the results root.

| Tool | Input | Returns |
| --- | --- | --- |
| `list_studies` | — | Every governed study under the results root, with its decision |
| `get_governance_report` | `study_id` | The `GovernanceReport`: the decision and its five checks |
| `get_quality_report` | `study_id` | The `QualityReport`: six checks and an overall status |
| `get_contract_results` | `study_id` | Both `ContractValidationResult`s, samples and compounds |
| `get_lineage_summary` | `study_id` | The curation run's identity and its `bio://` datasets |
| `why_not_ready` | `study_id` | Which checks stand between the study and `READY` |

Every one returns an existing model. Nothing here defines a second way to
describe a contract violation or a quality finding, so what an AI client sees is
what `bio-gov` writes to disk and what the pipeline gates on.

### `list_studies`

```json
[{"study_id": "BIO-001", "decision": "ready", "detail": "5 of 5 governance checks passed"}]
```

`decision` is `null` when a study has no governance report. That is not a
verdict of any kind — it is a run that stopped at a gate, and the study a
steward most wants to be shown. `detail` says which of the two it is, so a blank
never has to be interpreted.

A directory qualifies as a study when it is named for one — the
`AssetIdentifier` domain convention, `BIO-001` — and holds at least one piece of
the evidence this project produces. A scratch folder in `results/` is not
listed.

### `get_lineage_summary`

The raw JSONL is a poor thing to hand a model: two events that largely repeat
each other, wrapped in facets nothing here reads. The summary is the same
information as identities.

```json
{
  "study_id": "BIO-001",
  "run_id": "58de1d46-00c4-40ae-8fc9-d6104ba3c3bd",
  "job_namespace": "bio-governance-lab",
  "job_name": "curate-study",
  "event_types": ["START", "COMPLETE"],
  "complete": true,
  "inputs":  ["bio://BIO-001/raw/compounds", "bio://BIO-001/raw/expression", "bio://BIO-001/raw/samples"],
  "outputs": ["bio://BIO-001/curated/compounds", "..."]
}
```

Events that do not describe a single run cannot be summarised as one, and that
is reported as an evidence problem — pointing at `get_governance_report`, which
is where the incoherence is a `FAIL` and the study is `BLOCKED`. The summary
describes; it does not judge.

### `why_not_ready`

This is the tool that most looks like an explanation, and most carefully is not
one. It calls no language model, invokes no heuristic and invents no finding. It
takes the report's existing checks and partitions them by the statuses they
already carry:

```json
{
  "study_id": "BIO-001",
  "decision": "blocked",
  "summary": "BIO-001 is BLOCKED: 2 checks (curated_outputs, lineage_evidence) failed.",
  "blocking": [{"check_id": "curated_outputs", "status": "fail", "message": "..."}],
  "review": []
}
```

| Decision | `blocking` | `review` |
| --- | --- | --- |
| `READY` | empty | empty |
| `REVIEW` | empty | the checks that warned |
| `BLOCKED` | the checks that failed | any that also warned |

A `BLOCKED` study may have warnings too, and they are reported. Flattening them
away would be tidier and less honest.

The same study always yields the same answer. That is the convenience being
offered: what a person would conclude from reading the report, without their
having to read it. The prose an assistant writes on top of it is the part a
model is for.

## The resources

Two, because a governance report and a quality report are *documents* —
addressable, quotable things a host may want to attach to a conversation:

```
governance://studies/{study_id}/report
quality://studies/{study_id}/report
```

Both are `application/json` and both carry the same content as the corresponding
tool. Not every tool has a resource twin: duplicating all six would demonstrate
nothing that these two do not. The point is to use the second MCP concept where
it genuinely fits, not to show it twice.

## Transport

stdio, and only stdio. It is what a local MCP host launches a server with, it
needs no port, no certificate and no account, and this server has nothing to
offer a remote caller that a local one does not.

```bash
uv run bio-gov mcp serve
uv run bio-gov mcp serve --results-root results
```

The startup line goes to stderr; stdout is the JSON-RPC stream and carries
nothing else.

For an MCP host, the server is a command:

```json
{
  "mcpServers": {
    "bio-governance": {
      "command": "uv",
      "args": [
        "run", "--project", "/path/to/bio-governance-lab",
        "bio-gov", "mcp", "serve",
        "--results-root", "/path/to/bio-governance-lab/results"
      ]
    }
  }
}
```

`--results-root` defaults to `results`, which is relative to whatever directory
the host launched the server in — so give an absolute path in a host
configuration. A root that does not exist fails at startup with a message,
rather than serving an empty listing.

### The Inspector

The official [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
wraps any stdio server, so it needs nothing added to this repository:

```bash
npx @modelcontextprotocol/inspector uv run bio-gov mcp serve
```

The SDK's own `uv run mcp dev` expects a Python file with a module-level server
object. There is deliberately none here: the results root is a parameter of
`build_server()`, not a global, so no client and no tool can point the server at
another directory.

## The results root is the boundary

A study identifier arrives from an MCP client, which is to say from outside. It
is validated as an `AssetIdentifier` domain before it is joined to a path at
all:

```python
AssetIdentifier.parse(f"bio://{study_id}/raw/samples")
```

`BIO-001` passes. `../etc`, `/etc/passwd`, `BIO-001/../../data`, `..` and `a/b`
all fail, because the domain pattern is `^[A-Z0-9]+(?:-[A-Z0-9]+)*$` and admits
no separator to traverse with. Traversal is refused by the identifier convention
the whole project already uses, rather than by a filter over strings that has to
anticipate every encoding of `..`.

The resolved path is then required to still be inside the root, so a symlink
planted in `results/` cannot do what a string could not.

And there is no `read_file` tool. The server names every file it opens; a client
chooses a study, never a path. Together those are the confinement: the results
root is the only directory this server can reach, and the six tools are the only
things in it a client can ask for.

## Missing evidence is a sentence, not a stack

A repository where a run may have stopped at a gate has missing evidence as a
*normal* state, not an exceptional one. Every ordinary problem is an
`EvidenceError` inside, and reaches the client as an anticipated tool error —
the message, and one log line rather than a traceback:

```
Error executing tool get_governance_report: unknown study 'BIO-404' under results
Error executing tool why_not_ready: governance report for BIO-001 is missing: results/BIO-001/governance/governance-report.json
Error executing tool get_quality_report: results/BIO-001/quality/dq-report.json is not valid JSON: Expecting property name ...
Error executing tool get_governance_report: '../etc' is not a study identifier: expected a code such as 'BIO-001'
```

## Layout

```
src/bio_governance/mcp/
    evidence.py   reading the results root; no MCP import anywhere in it
    server.py     the tools, the resources, and the read-only annotations
```

The split is the same one the rest of the project makes between what something
*is* and how it is *transported*. `evidence.py` is ordinary Python over
ordinary files, tested as such; `server.py` is the protocol surface. Nothing in
`evidence.py` knows that MCP exists.

## What is deferred

No HTTP or SSE transport, no authentication, no OAuth, no reverse proxy and no
container. A local read-only server over local files needs none of them, and
adding an auth story to a server that exposes synthetic data would be theatre.

No write tools, of any kind, ever, for governance state. If a later milestone
wants an assistant to *request* something — a re-run, a review, a note on a
study — that is a request into a queue a person or a deterministic process acts
on, and it will not be a tool that mutates a report.

No prompts. Tools and resources are the two concepts worth demonstrating here,
and a prompt template nothing needs would be a third for its own sake.

No catalogue search. The server reads local evidence; it does not call
OpenMetadata, and the pipeline still runs with OpenMetadata switched off.
Exposing a catalogue query is a plausible later milestone and is not this one.

And still no model anywhere in the decision path. `why_not_ready` is the closest
this repository comes to an explanation, and it is a partition of a tuple.
