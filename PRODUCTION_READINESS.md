# EnvForge production readiness

Status on 2026-10-29: the company-pilot customer flow has prior local validation. The opt-in local PostgreSQL/shell/Chromium lab passed all three reference-oracle acceptance episodes and negative controls. Private-case controls and calibration comparison tooling have local checks. Hosting and domain have not been chosen. Public launch has not been signed off or deployed.

## Implemented

- Operator-provisioned customer workspaces and individual administrator, evaluator and viewer keys, with expiry, revocation and operator recovery.
- Secure browser sessions, CSRF/origin checks, write permissions, request limits and customer-scoped audit records.
- PostgreSQL resource isolation through tenant filters and a restricted request role with row policies. The trusted application owner and worker can access all tenants.
- Encrypted customer endpoint tokens and managed-provider request/episode allowances.
- Durable job checkpoints, bounded transient retries, provider cooldowns, completed-seed preservation, worker recovery and ownership-fenced cleanup. Retry attempts remain separate from verified scores.
- Dashboard account controls, usage allowances, retry explanations and unscored attempt downloads.
- Incremental, tenant-scoped live tool logs, visibly unscored until episode verification. The CLI supports endpoint bearer tokens and reuse of registered agents; HTTP agents receive episode cleanup requests.
- Private consistent workspace exports and deletion controls with a dry-run preview, disabled-workspace requirement and explicit confirmation.
- Public-IP pinning for approved SaaS agent hosts with preserved TLS identity, bounded DNS work and full request deadlines including slow headers/bodies. Redirects, proxies, compressed responses and non-public destinations are rejected.
- Container deployment profile with TLS termination, private database/API networking, worker readiness and a recorded QA artifact checked against the built environment implementation.
- Runtime image dependencies constrained by the API lockfile; PostgreSQL API regression checks configured in CI.

## Validation evidence

| Check | Observed result |
| --- | --- |
| Existing unit suite | 248 passed; 10 skipped for optional dependencies/services |
| Reference scenario QA | 600/600; 20 deterministic repeats; exploit probes passed |
| Local live lab | Three actual PostgreSQL/Chromium reference repairs passed; unrepaired incidents, wrong diagnoses and failed-browser evidence rejected; application files, database administrator access, process limits and direct egress checked |
| Live lab API path | Isolated queued reference run completed through the API and worker with actual PostgreSQL/Chromium, recorded trajectory, verified score and report export |
| Type checking, formatting, contributor hooks | Passed |
| Frontend production build and lint | Passed |
| Customer HTTP integration | Encrypted bearer authentication, health/action/close operations, report export and existing-agent reuse passed over real HTTP with a labelled scripted fixture |
| Live log UI | Events and expandable tool output appeared before completion in an isolated reference-oracle run; customer boundaries and cursor deltas passed on PostgreSQL |
| Outbound network checks | Private/mixed/translated-address blocks, pinned public HTTPS with correct TLS identity, wrong-hostname rejection, DNS timeout and actual socket slow-header/body deadlines passed |
| Data lifecycle | Consistent mode-600 export excludes credentials/keys/sessions; deletion dry run, disabled-state/confirmation gates and other-customer preservation passed |
| Real PostgreSQL with a non-superuser application role | Tenant API reads/writes, cross-account denial, RLS without query filters, roles, sessions, CSRF and key revocation passed |
| Failure injection | Rate limits, Retry-After, completed-seed preservation, worker interruption, mocked Gemini continuation, quotas, cancellation and stale-owner cleanup passed |
| Concurrent workers | Two customers ran concurrently; duplicate claims and excess admission denied |
| Packaged service | PostgreSQL/API/worker/Nginx startup and two-episode reference batch passed |
| Small HTTP smoke load | 100 authenticated reads at concurrency eight passed; this is not a capacity certification |
| Backup and restore | Snapshot restored to a separate database; application-role initialization and verified episode preservation passed |
| Readiness | Missing worker heartbeat returned 503; worker restart restored readiness |

The reference oracle uses private scenario truth. Scripted fixtures and mocked provider checks verify infrastructure behavior, not model competence. One real Gemini sanity run on heldout/hard seed 4242 completed with 0/1 recovery; safety and report quality both passed. It used three model calls and 6,308 tokens, and correctly received a failed score and REJECTED release gate. This tiny sample and the existing small Gemini, HolmesGPT and kubectl-ai results are insufficient to certify complex-agent performance.

## Required before public customer launch

The [execution-limits guide](deploy/labs/README.md) distinguishes the new local PostgreSQL/shell/Chromium preview from the simulation. The preview is unavailable until matching live QA passes and remains disabled in SaaS mode. Private-case provenance/routing and external-label comparison logic have local checks, but neither constitutes independent model calibration. Live Kubernetes and interactive browser automation remain unimplemented.

Complete the [operator guide's public launch gates](deploy/SAAS.md#public-launch-gates): deploy and verify the actual domain/TLS setup; independent security review and host network policy; sustained real-agent workloads and incident drills; monitored backups and service objectives; agreed data retention and support processes. Use the [customer pilot guide](deploy/CUSTOMER_PILOT.md) for onboarding and acceptance. Self-service signup, identity-provider SSO/MFA and billing are not implemented.

The primary environment remains a deterministic SRE simulation; the separate live lab is a local preview. Replay does not provide exactly-once side effects against real infrastructure. Publicly accessible agent endpoint DNS must be operator controlled until network-level egress protection is verified.
