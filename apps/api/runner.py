"""Durable SQLite job claims, bounded execution, cancellation, and provenance."""

import json
import logging
import time
import uuid
from functools import partial

from apps.api.jobs import (
    LEASE_SECONDS,
    LeaseLost,
    finish_attempt,
    load_checkpoint,
    recover_expired_jobs,
    save_checkpoint,
    worker_heartbeat,
)
from apps.api.secrets import endpoint_credential
from apps.api.settings import max_active_runs, saas_mode, validate_deployment
from apps.api.storage import database
from apps.api.usage import call_provider
from envforge.agents import (
    AgentConfig,
    AgentPending,
    AgentProtocolError,
    close_agent,
    http_action,
    request_agent,
)
from envforge.gemini_runner import VERSION as GEMINI_RUNNER_VERSION
from envforge.gemini_runner import GeminiRunner, provider_request
from envforge.protocol.integrity import digest
from envforge.protocol.types import Action, DifficultyTier, SuiteName
from envforge.provenance import implementation_digest
from envforge.worlds.sre.environment import SREIncidentResponseEnvironment
from envforge.worlds.sre.reference.solver import SREReferenceSolver
from envforge.worlds.sre.scenarios.generator import SREScenarioGenerator
from envforge.worlds.sre.tools.dispatcher import tool_definitions
from envforge.worlds.sre.verifier.verifier import SREVerifier

logger = logging.getLogger(__name__)


def run_evaluation_task(eval_id: str) -> bool:
    owner = uuid.uuid4().hex
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if (
            conn.execute(
                "SELECT COUNT(*) FROM evaluations WHERE status='RUNNING'"
            ).fetchone()[0]
            >= max_active_runs()
        ):
            return False
        candidate = conn.execute(
            "SELECT tenant_id FROM evaluations WHERE id=?", (eval_id,)
        ).fetchone()
        if candidate is None:
            return False
        if (
            saas_mode()
            and conn.execute(
                "SELECT COUNT(*) FROM evaluations WHERE tenant_id=? AND status='RUNNING'",
                (candidate["tenant_id"],),
            ).fetchone()[0]
        ):
            return False
        claimed = conn.execute(
            """UPDATE evaluations SET status='RUNNING',worker_id=?,lease_until=?,updated_at=?
                                  WHERE id=? AND status IN ('QUEUED','RETRY_WAIT') AND next_attempt_at<=?""",
            (owner, time.time() + LEASE_SECONDS, time.time(), eval_id, time.time()),
        ).rowcount
        if not claimed:
            return False
        row = conn.execute(
            "SELECT * FROM evaluations WHERE id=?", (eval_id,)
        ).fetchone()
    if row["environment_id"].startswith("live."):
        from apps.api.labs import run_lab_task

        return run_lab_task(row, owner)
    env = None
    config = None
    credential = None
    checkpoint = None
    rescheduled = False
    try:
        env = SREIncidentResponseEnvironment()
        manifest = json.loads(row["manifest_json"])
        private = (
            env.private_scenarios.provenance()
            if env.private_scenarios and manifest["suite"] == "heldout"
            else None
        )
        if manifest.get("private_scenario_bundle") != private:
            raise ValueError("Private scenario bundle changed; start a new run")
        checkpoint = load_checkpoint(row["checkpoint_json"], manifest["digest"])
        expected = {
            "environment_id": env.ENVIRONMENT_ID,
            "environment_version": env.VERSION,
            "scenario_version": SREScenarioGenerator.VERSION,
            "verifier_version": SREVerifier.VERSION,
            "implementation_digest": implementation_digest(),
        }
        if any(
            manifest.get(key) != value for key, value in expected.items()
        ) or manifest.get("digest") != digest(
            {key: value for key, value in manifest.items() if key != "digest"}
        ):
            raise ValueError("Queued run does not match the installed implementation")
        config = AgentConfig.model_validate(manifest["agent_config"])
        credential = endpoint_credential(config.credential_ref, row["tenant_id"])
        if (
            config.adapter == "gemini"
            and config.metadata.get("runner_version") != GEMINI_RUNNER_VERSION
        ):
            raise ValueError("Gemini runner version changed; register a new agent")
        if config.metadata.get("integration") in {"holmesgpt", "kubectl_ai"}:
            runtime = config.metadata.get("runtime")
            if not runtime:
                raise ValueError(
                    "Test the native agent connection before launching a run"
                )
            health = request_agent(config, {"operation": "health"}, token=credential)
            if not isinstance(health, dict) or any(
                health.get(key) != value for key, value in runtime.items()
            ):
                raise ValueError(
                    "Native agent runtime changed; test connection and start a new run"
                )
        for seed in manifest["seeds"]:
            with database() as conn:
                if conn.execute(
                    "SELECT 1 FROM episodes WHERE evaluation_id=? AND seed=?",
                    (eval_id, seed),
                ).fetchone():
                    continue
                run = conn.execute(
                    "SELECT status FROM evaluations WHERE id=? AND worker_id=?",
                    (eval_id, owner),
                ).fetchone()
                if run is None or run["status"] != "RUNNING":
                    return True
            initial = env.reset(
                f"sre:{manifest['suite']}:{manifest['tier']}:{seed}",
                seed,
                suite=SuiteName(manifest["suite"]),
                tier=DifficultyTier(manifest["tier"]),
            )
            ep_id = next(iter(env.active_episodes))
            resumed = checkpoint if checkpoint and checkpoint["seed"] == seed else None
            if resumed:
                restored = env.active_episodes.pop(ep_id)
                ep_id = resumed["episode_id"]
                restored["episode_id"] = ep_id
                env.active_episodes[ep_id] = restored
            ep = env.active_episodes[ep_id]
            solver = (
                SREReferenceSolver(ep["task"])
                if config.adapter == "reference_solver"
                else None
            )
            direct_runner = (
                GeminiRunner(
                    config,
                    request=partial(call_provider, row["tenant_id"], provider_request),
                )
                if config.adapter == "gemini"
                else None
            )
            started = resumed["started"] if resumed else time.time()
            deadline = (
                resumed["deadline"]
                if resumed
                else started + ep["task"].limits["timeout_seconds"]
            )
            obs = env.observe(ep_id)
            planned = None
            if resumed:
                obs = env.replay(ep_id, resumed["history"])
                if direct_runner:
                    direct_runner.restore(resumed["runner"])
                if solver:
                    previous = None
                    for entry in resumed["history"]:
                        expected_action = solver.next_action(previous)
                        if (
                            expected_action is None
                            or expected_action.model_dump()
                            != Action.model_validate(
                                {
                                    k: entry[k]
                                    for k in ("action_id", "tool", "arguments")
                                }
                            ).model_dump()
                        ):
                            raise ValueError(
                                "Reference checkpoint actions do not match the installed solver"
                            )
                        previous = entry["output"]
                if resumed.get("planned_action"):
                    planned = Action.model_validate(resumed["planned_action"])
                    if solver:
                        expected_action = solver.next_action(obs.tool_output)
                        if (
                            expected_action is None
                            or expected_action.model_dump() != planned.model_dump()
                        ):
                            raise ValueError(
                                "Reference checkpoint pending action changed"
                            )

            episode_context = {
                "manifest_digest": manifest["digest"],
                "seed": seed,
                "episode_id": ep_id,
                "started": started,
                "deadline": deadline,
            }

            def persist(
                pending: Action | None = None,
                context=episode_context,
                ep=ep,
                direct_runner=direct_runner,
            ):
                nonlocal checkpoint
                checkpoint = {
                    **context,
                    "history": ep["action_history"],
                    "planned_action": pending.model_dump(mode="json")
                    if pending
                    else None,
                    "runner": direct_runner.checkpoint() if direct_runner else None,
                }
                save_checkpoint(eval_id, owner, checkpoint)

            persist(planned)
            calls = 0
            protocol_error = None
            while not ep["done"]:
                calls += 1
                if calls > ep["max_steps"] * 2:
                    protocol_error = (
                        "Agent repeatedly reused actions without making progress"
                    )
                    break
                with database() as conn:
                    run = conn.execute(
                        "SELECT status FROM evaluations WHERE id=? AND worker_id=?",
                        (eval_id, owner),
                    ).fetchone()
                    if run is None or run["status"] != "RUNNING":
                        return True
                    conn.execute(
                        "UPDATE evaluations SET lease_until=?, updated_at=? WHERE id=? AND worker_id=? AND status='RUNNING'",
                        (time.time() + LEASE_SECONDS, time.time(), eval_id, owner),
                    )
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("Episode wall-clock budget exceeded")
                try:
                    if planned:
                        action = planned
                        planned = None
                    elif solver:
                        action = solver.next_action(obs.tool_output)
                    elif direct_runner:
                        action = direct_runner.next_action(obs, min(20, remaining))
                    else:
                        action = http_action(
                            config,
                            ep_id,
                            obs.model_dump(),
                            tool_definitions(),
                            min(20, remaining),
                            token=credential,
                        )
                except AgentPending:
                    calls -= 1
                    time.sleep(min(0.25, max(0, deadline - time.time())))
                    continue
                except AgentProtocolError as exc:
                    protocol_error = str(exc)
                    break
                if action is None:
                    break
                # Test-only tampering tool must not be exposed by the control plane.
                if action.tool not in {t["name"] for t in tool_definitions()}:
                    protocol_error = "Agent selected an unavailable tool"
                    break
                try:
                    persist(action)
                    result = env.step(ep_id, action.action_id, action)
                except ValueError:
                    protocol_error = "Action ID reused with different content"
                    break
                obs = result.observation
                persist()
                if result.done:
                    break
            sealed = env.seal(ep_id)
            verification = env.verify(sealed, ep["hidden_truth"])
            if protocol_error:
                verification.success = False
                verification.reward = 0
                verification.reason = protocol_error
                verification.checks["agent_protocol_valid"] = False
            trajectory = {
                "schema_version": "1",
                "environment_version": env.VERSION,
                "task": ep["task"].model_dump(
                    mode="json",
                    exclude={"fault_family", "secondary_fault", "initial_symptoms"}
                    if private
                    else set(),
                ),
                "initial_observation": initial.model_dump(
                    exclude={"hidden_truth", "system_state"}
                ),
                "initial_state": initial.system_state,
                "action_history": sealed.action_history,
                "event_log": sealed.event_log,
                "final_state": sealed.final_state_snapshot,
                "state_digest": sealed.state_digest,
                "duration_seconds": round(time.time() - started, 4),
            }
            if direct_runner:
                trajectory["agent_run"] = {
                    "runner_version": GEMINI_RUNNER_VERSION,
                    "usage": direct_runner.usage,
                    "decisions": direct_runner.decisions,
                    "recovery_checks": direct_runner.recovery,
                }
            with database() as conn:
                conn.execute("BEGIN IMMEDIATE")
                status = conn.execute(
                    "SELECT status FROM evaluations WHERE id=? AND worker_id=?",
                    (eval_id, owner),
                ).fetchone()
                if status is None or status["status"] != "RUNNING":
                    return True
                conn.execute(
                    "INSERT INTO episodes(id,evaluation_id,task_id,seed,status,success,reward,trajectory_json,verification_json,created_at,tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ep_id,
                        eval_id,
                        ep["task"].task_id,
                        seed,
                        "COMPLETED",
                        int(verification.success),
                        verification.reward,
                        json.dumps(trajectory),
                        verification.model_dump_json(),
                        time.time(),
                        row["tenant_id"],
                    ),
                )
                conn.execute(
                    """UPDATE evaluations SET completed_episodes=completed_episodes+1,
                                success_count=success_count+?,hard_failures_count=hard_failures_count+?,updated_at=?
                                WHERE id=? AND worker_id=? AND status='RUNNING'""",
                    (
                        int(verification.success),
                        int(bool(verification.hard_failures)),
                        time.time(),
                        eval_id,
                        owner,
                    ),
                )
            save_checkpoint(eval_id, owner, None)
            checkpoint = None
            close_agent(config, ep_id, token=credential)
            if env is not None:
                env.close(ep_id)
        with database() as conn:
            conn.execute(
                "UPDATE evaluations SET status='COMPLETED',lease_until=NULL,checkpoint_json=NULL,error=NULL,updated_at=? WHERE id=? AND worker_id=? AND status='RUNNING'",
                (time.time(), eval_id, owner),
            )
    except LeaseLost:
        logger.info("Evaluation %s no longer belongs to this worker", eval_id)
    except Exception as exc:  # noqa: BLE001 -- persist every worker-boundary failure without logging credentials
        logger.error("Evaluation %s interrupted: %s", eval_id, type(exc).__name__)
        rescheduled = finish_attempt(row, owner, exc, checkpoint)
    finally:
        # A recovered job reuses its native session. A former owner must never
        # close that session after the new worker has taken over.
        may_close = False
        if (
            config is not None
            and not rescheduled
            and env is not None
            and env.active_episodes
        ):
            try:
                with database() as conn:
                    may_close = bool(
                        conn.execute(
                            "UPDATE evaluations SET lease_until=CASE WHEN status='RUNNING' THEN ? ELSE NULL END WHERE id=? AND worker_id=? AND status IN ('RUNNING','CANCELLED','INFRA_FAILED')",
                            (time.time() + LEASE_SECONDS, eval_id, owner),
                        ).rowcount
                    )
            except Exception:  # noqa: BLE001 -- abandoned native sessions expire at the bridge if ownership cannot be checked
                may_close = False
        for ep_id in list(env.active_episodes) if env is not None else []:
            if config is not None and may_close:
                close_agent(config, ep_id, token=credential)
            if env is not None:
                env.close(ep_id)
    return True


def worker_once() -> bool:
    recover_expired_jobs()
    with database() as conn:
        eligibility = (
            " AND NOT EXISTS (SELECT 1 FROM evaluations active WHERE active.tenant_id=e.tenant_id AND active.status='RUNNING')"
            if saas_mode()
            else ""
        )
        row = conn.execute(
            "SELECT id FROM evaluations e WHERE status IN ('QUEUED','RETRY_WAIT') AND next_attempt_at<=?"
            + eligibility
            + " ORDER BY created_at LIMIT 1",
            (time.time(),),
        ).fetchone()
    return run_evaluation_task(row["id"]) if row else False


def main() -> None:
    validate_deployment()
    with worker_heartbeat():
        while True:
            if not worker_once():
                time.sleep(1)


if __name__ == "__main__":
    main()
