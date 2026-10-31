# Connect and evaluate a company agent

EnvForge evaluates an agent's actions in a deterministic SRE simulation. It does not operate the customer's production cluster. A pilot should measure a specific agent version on agreed scenarios and report its failures as well as successes.

## Account and endpoint

The operator creates a company workspace and supplies a named administrator key through a private channel. The administrator can issue evaluator/viewer keys. Use an evaluator key for CI. Each person or integration should have its own key; do not share one administrator credential across a team.

The customer hosts their agent behind an authenticated HTTPS POST endpoint. For public SaaS, the operator approves its exact hostname; port 443 and a publicly routable DNS destination are required. Private-network agents need a separately designed connectivity arrangement rather than an exception to the public service's network rules.

The endpoint supports three operations:

| Operation | Input | Response |
| --- | --- | --- |
| `health` | `protocol_version: "1"`, `operation: "health"` | `{"status":"healthy"}` |
| `act` | Episode ID, public observation and tool JSON schemas | One typed `action`, `{"action":null}` to stop, or `{"pending":true}` while reasoning |
| `close` | Episode ID and `operation: "close"` | Release episode resources, return `{"status":"closed"}` |

Example action response:

```json
{"action":{"action_id":"episode-step-1","tool":"read_logs","arguments":{"service":"checkout-api","lines":20}}}
```

The agent chooses from the supplied tools; EnvForge executes the tool in the simulation and returns its output with the next observation. Keep memory separate for each episode ID. Cache reasoning results by episode ID and observation step so repeated polling or worker recovery does not start duplicate model requests. Action IDs must be stable for retries of the same action and unique for new actions.

Long reasoning jobs should return `pending` promptly. Requests have a bounded time budget, and JSON responses must be objects no larger than 64 KiB, without compression or redirects. Return an appropriate HTTP error and optional Retry-After on transient overload. Expire abandoned episode state after the evaluation deadline even if `close` never arrives. The platform records tool inputs and outputs, not the agent service's private reasoning or internal application logs.

## Register and run

In the dashboard, choose **Launch Evaluation → Register an agent → HTTP action endpoint**. Supply the URL and endpoint token, register it, select **Test connection**, then launch a small smoke batch. A successful health check proves connectivity, not incident-solving ability.

For CI, configure `ENVFORGE_API_TOKEN` with the workspace evaluator key and `CUSTOMER_AGENT_TOKEN` with the endpoint bearer token through your CI secret store. Do not put raw tokens in shell history, workflow files or command arguments.

```sh
uv run envforge evaluate --api-url https://your-envforge-domain.example \
  --agent-url https://your-agent-domain.example/agent \
  --agent-token-env CUSTOMER_AGENT_TOKEN --name 'Company agent v1' \
  --suite smoke --tier easy --episodes 3 --output ./envforge-report.json
```

Save the registered agent ID from the exported report. Reuse it in subsequent runs with `--agent-id ID` instead of the URL and endpoint-token options. The command waits for completion, exports evidence, exits zero only when every episode passes, and requests cancellation on Ctrl-C or its wait timeout.

## Inspect the evidence

Open **Evaluations & Runs**, then select the run. Active episodes show **Live episode log** with tool arguments and outputs. Those events are explicitly unscored. Infrastructure errors and interrupted traces appear under **Infrastructure attempts**. Completed episodes expose **Explore**, which opens the action timeline, tool outputs, state changes and verifier checks. **Export report** downloads scores and recorded provenance.

Start with connectivity and a small smoke batch, then agree a representative mix of standard/adversarial/hard cases and repeated seeds before making a buying or release decision. Compare agent versions on matching scenario versions and seeds. The built-in release policy is a starting policy, not an external certification. The scenario generator is public, so these results cannot establish resistance to benchmark memorization.

## Acceptance and support

Before accepting a paid pilot, identify the customer owner, agent endpoint/version, expected workload, chosen hosting region, data retention period, support contact and success criteria. Verify customer isolation, endpoint authentication, cancellation/retry behavior, report exports and backup restoration on that deployment. Agree how upgrades and data deletion are authorized. Keep provider failures separate from agent scores.

Reference-oracle and scripted integration checks are setup evidence. They are not paid-model benchmarks and must not be presented as proof that the customer's agent is reliable. This release has no self-service billing, enterprise SSO/MFA or production-infrastructure execution.
