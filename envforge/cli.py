"""EnvForge release QA and reproducible local episode CLI."""

import argparse
import json
import os
from pathlib import Path

from envforge.calibration import compare_labels
from envforge.client import evaluate_http_agent
from envforge.protocol.types import DifficultyTier, SuiteName
from envforge.qa import run_qa, solve


def main() -> None:
    parser = argparse.ArgumentParser(prog="envforge")
    commands = parser.add_subparsers(dest="command", required=True)
    lab_qa = commands.add_parser(
        "lab-qa", help="Run real local PostgreSQL, shell and Chromium acceptance probes"
    )
    lab_qa.add_argument("--output", type=Path, required=True)
    calibrate = commands.add_parser(
        "calibrate",
        help="Compare completed reports with externally supplied review labels",
    )
    calibrate.add_argument("--labels", type=Path, required=True)
    calibrate.add_argument("--report", type=Path, action="append", required=True)
    calibrate.add_argument("--output", type=Path, required=True)
    smoke = commands.add_parser("smoke", help="Run the explicit oracle baseline")
    smoke.add_argument("--seed", type=int, default=42)
    qa = commands.add_parser("qa", help="Run release QA gates and export the manifest")
    qa.add_argument("--instances", type=int, default=50)
    qa.add_argument("--repeats", type=int, default=20)
    qa.add_argument("--output", type=Path)
    evaluate = commands.add_parser(
        "evaluate", help="Evaluate a customer HTTP agent through the control plane"
    )
    evaluate.add_argument("--api-url", default="http://127.0.0.1:8000")
    agent_source = evaluate.add_mutually_exclusive_group(required=True)
    agent_source.add_argument("--agent-url")
    agent_source.add_argument(
        "--agent-id", help="Reuse an agent already registered in this workspace"
    )
    evaluate.add_argument(
        "--name", default="HTTP agent", help="Name for a newly registered agent"
    )
    evaluate.add_argument(
        "--agent-token-env",
        help="Environment variable containing the endpoint bearer token; never pass a raw token on the command line",
    )
    evaluate.add_argument(
        "--suite", choices=[s.value for s in SuiteName], default="smoke"
    )
    evaluate.add_argument(
        "--tier", choices=[t.value for t in DifficultyTier], default="easy"
    )
    evaluate.add_argument("--episodes", type=int, default=10)
    evaluate.add_argument("--seed", type=int, default=42)
    evaluate.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "lab-qa":
        from envforge.labs.qa import run_live_qa

        result = run_live_qa()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "reference_episodes": result["reference_episodes"],
                    "runtime_image_digest": result["runtime_image_digest"],
                }
            )
        )
        raise SystemExit(0 if result["status"] == "PASS" else 1)
    if args.command == "calibrate":
        try:
            result = compare_labels(args.labels, args.report)
            descriptor = os.open(
                args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w") as output:
                json.dump(result, output, indent=2)
                output.write("\n")
        except (OSError, ValueError, KeyError, TypeError):
            parser.error(
                "Calibration input is invalid, incomplete, or the output already exists"
            )
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "cases_reviewed": result["cases_reviewed"],
                    "disagreements": len(result["disagreements"]),
                    "independence_assurance": result["independence_assurance"],
                }
            )
        )
        raise SystemExit(2 if result["disagreements"] else 0)
    if args.command == "evaluate":
        if not 1 <= args.episodes <= 500:
            parser.error("episodes must be 1..500")
        credential = (
            os.environ.get(args.agent_token_env) if args.agent_token_env else None
        )
        if args.agent_token_env and (not credential or args.agent_id):
            parser.error(
                "--agent-token-env requires a configured variable and --agent-url"
            )
        result = evaluate_http_agent(
            args.api_url,
            args.agent_url,
            os.environ.get("ENVFORGE_API_TOKEN", ""),
            suite=args.suite,
            tier=args.tier,
            episodes=args.episodes,
            seed=args.seed,
            agent_id=args.agent_id,
            agent_name=args.name,
            credential=credential,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "evaluation_id": result["evaluation_id"],
                    "status": result["status"],
                    "metrics": result["metrics"],
                },
                indent=2,
            )
        )
        raise SystemExit(
            0
            if result["status"] == "COMPLETED"
            and result["metrics"]["success_rate"] == 1
            else 1
        )
    if args.command == "smoke":
        _, result = solve(args.seed, SuiteName.SMOKE, DifficultyTier.EASY)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["success"] else 1)
    if not 1 <= args.instances <= 500 or not 1 <= args.repeats <= 100:
        parser.error("instances must be 1..500 and repeats 1..100")
    result = run_qa(args.instances, args.repeats)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "instances"},
            indent=2,
        )
    )
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
