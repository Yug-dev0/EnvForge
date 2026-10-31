# Execution limits: live lab and private evaluation controls

The deterministic SRE simulation and the live PostgreSQL lab are separate environments. The live lab is an opt-in **local preview**. It is disabled in SaaS mode, and it does not execute against a production database or Kubernetes cluster.

## Build and validate the live lab

With Docker Desktop running, from the repository root:

```sh
docker build -f deploy/labs/Dockerfile.postgres -t envforge-postgres-lab:local .
uv run --project apps/api envforge lab-qa --output data/live-lab-qa.json
```

Setup downloads official Debian PostgreSQL/Chromium packages and the official uv binary. On first use, the v1 runtime may also build its Alpine/iptables networking helper. During evaluation, external network access is denied. Each episode owns a disposable container, one CPU, 1 GiB of memory and a 256-process ceiling. No host directories or Docker socket are mounted into the workload.

`lab-qa` runs three reference-oracle repairs against actual PostgreSQL and Chromium. It checks unrepaired incidents, incorrect diagnoses, failed-browser evidence, protected application files, database administrator access, process limits and direct egress. Its failed-browser control deliberately edits a captured test trace. These are acceptance checks, not model benchmarks. The preview stays unavailable until the recorded QA matches both the installed implementation and exact image ID.

Then restart the local API and worker with `ENVFORGE_ENABLE_LABS=1`. For an in-process local worker:

```sh
ENVFORGE_ENABLE_LABS=1 uv run --env-file .env.local --project apps/api envforge-api
```

In **Scenario Catalog**, select **Live PostgreSQL Recovery Lab**. It supports smoke/standard, easy tier and up to three episodes per run. Choose a Gemini agent or an HTTP agent that supports the displayed `execute_shell`, `browse` and report tools. Native simulator bridges do not automatically support this toolset. The reference oracle remains a labelled setup-only option.

The shell runs as an unprivileged engineer. PostgreSQL and the application run as different users, and application source is read-only to the engineer. The browser tool renders the local page in headless Chromium and checks executed JavaScript; it does not provide interactive click/typing automation. Chromium runs without its own sandbox inside the disposable container, so this preview is **not suitable for hostile customer code**. Public SaaS execution requires a separately reviewed VM/sandbox design.

After actions stop, trusted controller probes check actual HTTP health/latency, preserved database data, the reported canonical fault and successful shell/browser evidence. Prose quality is not independently calibrated. Invalid agent decisions are scored failures; runtime/provider errors remain infrastructure failures. Live shell actions are never replayed on worker recovery. Interrupted live runs require a new evaluation. A process killed without cleanup can leave resources requiring the v1 runtime's operator cleanup procedure.

Live Kubernetes, interactive browser control, arbitrary customer terminals and VM-grade multi-tenant execution remain unimplemented.

## Operator-held holdout selections

Create a JSON file outside source control. Its structure is:

```json
{
  "bundle_id": "company-review-1",
  "secret": "REPLACE_WITH_A_FRESH_64_CHARACTER_RANDOM_HEXADECIMAL_SECRET_VALUE_HERE",
  "cases": [
    {"seed": 42001, "tier": "easy", "family": "cache_saturation"},
    {"seed": 42002, "tier": "hard", "family": "bad_deployment", "secondary_fault": "worker_backlog"}
  ]
}
```

Generate the real secret with `uv run --project apps/api python -c 'import secrets; print(secrets.token_hex(32))'`. Keep the file private (mode 600). Set `ENVFORGE_PRIVATE_SCENARIOS` to its path on both API and worker. Use the heldout suite, an included seed/tier and a contiguous range of included seeds for batches.

Selections are loaded into a per-run snapshot. Admission rejects missing cases; the worker rejects a bundle that differs from the queued provenance. Manifests expose only bundle identity, count and a keyed commitment. They do not expose the secret or answer table, and paired comparisons require the same bundle. Private prompts default to a generic incident description; an operator can supply a custom `prompt`. Direct fault/secondary-fault fields are removed from exported task descriptors for private runs.

Underlying fault mechanisms remain public. Agents see legitimate symptoms and customers retain their observed traces and submitted answers. This is operator-held case selection, **not a guarantee against memorization, information inference or training on prior results**. The public 600-case QA report does not establish coverage of a private bundle.

## Compare with external reviewer labels

Obtain completed report exports and labels from a reviewer or test oracle that did not simply copy EnvForge's checks. Label files contain `reviewer`, an `independence_statement`, and a `labels` array. Each label contains `evaluation_id`, `seed`, and `expected` boolean values for `recovery`, `safety` and `report_quality`.

```sh
uv run envforge calibrate --labels /secure/reviewer-labels.json \
  --report /secure/agent-a-report.json --report /secure/agent-b-report.json \
  --output /secure/calibration-comparison.json
```

Reports must have matching environment/verifier/implementation and bundle provenance. The command reports confusion matrices and disagreements, writes a private file without overwriting an existing output, and exits 2 for disagreements. It labels independence as **operator attested, not verified**. Synthetic logic checks and reference-oracle agreement do not complete independent model calibration; qualified external labels, representative model/workload coverage and review are still required.
