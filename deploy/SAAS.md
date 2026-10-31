# EnvForge multi-customer deployment

This profile is for an API-first, invitation-only B2B service. An operator creates customer workspaces and issues individual access keys. Administrators can issue and revoke role-scoped keys. The dashboard exchanges an access key for an eight-hour, secure HttpOnly cookie, with origin and CSRF checks on writes. Keys expire within 90 days; losing all administrator keys requires operator recovery.

## Configure and start

Use a dedicated host with Docker Compose, public DNS and ports 80/443. Store secrets in a private environment file outside the repository, with mode 600. Supply:

| Variable | Value |
| --- | --- |
| `ENVFORGE_DOMAIN` | The public hostname, without a scheme or path |
| `ENVFORGE_DB_ADMIN_PASSWORD` | A unique, strong database administrator password |
| `ENVFORGE_APP_PASSWORD` | A separate random hexadecimal password, at least 32 bytes, safe inside a database URL |
| `ENVFORGE_SECRET_KEY` | A Fernet encryption key generated with `uv run --project apps/api python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |
| `GEMINI_API_KEY` | Managed provider credential, with provider-side billing limits |
| `ENVFORGE_AGENT_HOSTS` | Exact, operator-controlled public HTTPS agent hosts, comma separated; `disabled.invalid` disables customer endpoints |

Validate the configuration without displaying secrets, then start:

```sh
docker compose --env-file /secure/envforge.env -f deploy/compose.saas.yaml config --quiet
docker compose --env-file /secure/envforge.env -f deploy/compose.saas.yaml up --build -d
```

The image checks the recorded QA artifact against its installed implementation and includes it in the dashboard. After changing environment code, regenerate it on the build host with `uv run envforge qa --instances 50 --repeats 20 --output deploy/qa-report.json` before building. The full reference suite runs outside the image build; a stale artifact fails the build immediately.

Caddy obtains TLS certificates for the configured hostname. The database and API have no published ports. The database bootstrap creates an application owner without superuser privileges and a restricted request role. API resource requests switch to the restricted role and PostgreSQL row policies enforce workspace boundaries. Authentication and the trusted worker use the application owner. This protects against missing tenant filters; it does not isolate a compromised application process from its database credentials.

Bootstrap runs only for an empty PostgreSQL volume. Preserve the volume between releases. Existing database installations must provision equivalent roles and schema permissions before enabling SaaS mode. Do not reuse the disposable local validation database or enable trust authentication in production.

Provision an account through the operator console:

```sh
docker compose --env-file /secure/envforge.env -f deploy/compose.saas.yaml exec api uv run --no-project --no-sync envforge-admin create-workspace --name 'Customer workspace' --owner 'Named administrator'
```

The command prints one access key once. Deliver it through an approved private channel, never logs or source control. Other operator commands are `issue-key --workspace ID --name NAME`, `set-allowance --workspace ID --episodes 500 --model-calls 500`, and `disable-workspace --workspace ID`. Disabling a workspace rejects sessions and keys, cancels unfinished runs and blocks new managed provider calls. A request already dispatched may still finish.

## Customer data lifecycle

`envforge-admin export-workspace --workspace ID --output /tmp/customer-export.json` creates a consistent, private JSON snapshot with mode 600. It excludes endpoint credentials, access keys and browser sessions, and refuses to overwrite an existing file. In containers, run it through `uv run --no-project --no-sync` and transfer the file securely to the authorized recipient. It contains customer traces and must not be attached to public support issues.

`envforge-admin delete-workspace --workspace ID` previews row counts without deleting data. To apply deletion, first disable the workspace, then use `delete-workspace --workspace ID --confirm ID`. The exact ID and disabled state are required. Deletion removes the tenant's live records and credentials; the operator retains only an opaque workspace ID and deletion timestamp in its receipt. Backups and previously exported files follow their separately agreed retention schedule and are not erased by this command. Do not run deletion against a customer's workspace without authorization.

## Runtime behavior

Runs admit at most two active jobs globally by default and one per customer. The shared PostgreSQL queue, worker leases, bounded retries, checkpoints and unique evaluation/seed records preserve completed episodes across restarts. Provider 429 responses establish a shared cooldown. Transient failures get at most three retries, honor bounded Retry-After hints and remain subject to the episode deadline. Invalid agent output and failed scores are not retried as infrastructure errors. Interrupted attempts are available separately from verified episodes.

Resume replays recorded actions only inside the deterministic SRE simulator. It does not offer exactly-once execution for real production infrastructure or guarantee that an external agent service retains its own state after restart. EnvForge currently measures simulated incident response, not real Kubernetes operation.

While a run is active, the dashboard polls the tenant-scoped progress API for incremental public tool events. These are unscored logs; only completed, independently verified episodes contribute to pass rates. Both generic HTTP agents and native bridges receive an episode `close` request when cleanup is authorized. Agent services must expire abandoned sessions as well.

Allowances cover admitted episodes and attempted managed model requests per UTC day. Returned token counts are recorded; these are not invoices or exact monetary budgets. Failed attempts consume request allowance. Provider health probes inspect model availability without generating completions. Use provider-side spending caps as well. The API limits authenticated traffic to 600 requests per minute per individual key; sign-in is limited to 20 per minute per immediate network peer. Reverse proxies may cause that sign-in limit to be shared across users.

Monitor `/ready` for database access and a worker heartbeat newer than 30 seconds; `/health` is basic process/storage liveness. Alert on sustained unready responses, infrastructure failures, queue age, disk growth, backup age and provider spend. Worker heartbeats alone do not prove jobs are making progress. Structured worker logs avoid provider bodies and credentials; unscored attempt traces may contain customer agent outputs and must be treated as private customer data.

## Backup, restore and upgrades

Create a database snapshot before each upgrade:

```sh
docker compose --env-file /secure/envforge.env -f deploy/compose.saas.yaml exec -T postgres pg_dump -U envforge_admin -d envforge -Fc > /secure/envforge-backup.dump
```

Encrypt backups outside the host, restrict their permissions and test restoration to a separate database with `pg_restore --no-owner --no-acl`, connecting as `envforge_app` so it owns the restored tables. Provision the PostgreSQL roles first and make that application role the target database owner. Back up `ENVFORGE_SECRET_KEY` separately; losing it makes endpoint credentials unrecoverable. A complete host recovery also requires PostgreSQL role bootstrap, application images and environment settings. Never test restore against the live database. Define backup frequency and retention against customer RPO/RTO before launch. Schema initialization is additive and serialized; take a snapshot and test the new image on a restored copy before upgrading. Restore the snapshot with the previous image if an upgrade is incompatible.

Credential key rotation accepts a comma-separated key ring: the first key encrypts new values; subsequent keys decrypt existing values. Retain old keys until all stored credentials have been rewritten or re-registered. Removing an old key early breaks those credentials.

## Public launch gates

The deployment profile is not proof of public production readiness. Before accepting customer data:

- Validate TLS, cookie security, customer isolation and invitation/recovery procedures on the actual public deployment. Arrange independent security review and dependency/image scanning; pin deployed images by digest.
- Verify host-level outbound restrictions as a second layer against private, link-local and metadata destinations. The SaaS HTTP client permits approved HTTPS hosts on port 443, rejects non-public/mixed DNS answers, pins a validated IP while preserving TLS hostname checks, and disables redirects/proxies. It bounds DNS concurrency and request time, including slow headers and bodies. These application controls do not replace independent network policy or security review. Do not allow arbitrary user-supplied domains.
- Exercise concurrent workload capacity, provider failures, disk pressure, database outage and backup restoration on the chosen host. Establish measured service objectives and on-call ownership.
- Define customer data retention/deletion, encrypted storage, privacy terms, abuse handling, support and incident response. Audit records currently share the application database and are not tamper-proof external logs.
- Add identity-provider SSO/MFA and self-service onboarding before offering that account experience. There is no email/password signup, password recovery, organization invitation email or billing integration in this release.
- Evaluate both native agents and the managed runner across representative difficult seeds and repeated trials. Existing small model benchmarks do not establish reliability for large or complex agents. Passing infrastructure tests cannot guarantee correct agent decisions.
