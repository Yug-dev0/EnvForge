"""Live PostgreSQL, shell and Chromium behind the existing v1 runtime interface."""

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager

from envforge.protocol.integrity import digest
from envforge.protocol.types import Action
from verifiers.v1.configs.runtime import DockerConfig, NetworkPolicyConfig
from verifiers.v1.runtimes import provision_runtime

ID = "live.postgres-recovery"
VERSION = "0.1.0"
IMAGE = "envforge-postgres-lab:local"
TOOLS = [
    {
        "name": "execute_shell",
        "description": "Run a shell command as the unprivileged engineer inside this episode's container. No host access.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "maxLength": 4096}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browse",
        "description": "Render the local checkout page in headless Chromium and return the DOM, including executed JavaScript.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_incident_report",
        "description": "Finish with an evidence-based root cause and mitigation. This does not determine the score.",
        "parameters": {
            "type": "object",
            "properties": {
                "primary_fault": {
                    "type": "string",
                    "enum": ["wrong_port", "wrong_table", "query_timeout"],
                },
                "root_cause": {"type": "string", "maxLength": 2000},
                "mitigation": {"type": "string", "maxLength": 2000},
            },
            "required": ["primary_fault", "root_cause", "mitigation"],
            "additionalProperties": False,
        },
    },
]


async def command(runtime, argv: list[str], timeout: float = 15) -> dict:
    """Bound output and kill the runtime-owned process group on timeout or overflow."""
    process = await runtime.exec(argv, {})
    buffers = [bytearray(), bytearray()]

    async def collect(stream, output):
        async for chunk in stream:
            output.extend(chunk[: max(0, 32768 - len(output))])
            if len(output) >= 32768:
                await process.kill()
                break

    try:
        async with asyncio.timeout(timeout):
            _, _, code = await asyncio.gather(
                collect(process.stdout, buffers[0]),
                collect(process.stderr, buffers[1]),
                process.wait(),
            )
    except TimeoutError:
        await process.kill()
        await process.wait()
        return {
            "exit_code": 124,
            "stdout": buffers[0].decode(errors="replace"),
            "stderr": "Command time budget exhausted",
        }
    except BaseException:
        await process.kill()
        await process.wait()
        raise
    return {
        "exit_code": code,
        "stdout": buffers[0].decode(errors="replace"),
        "stderr": buffers[1].decode(errors="replace"),
    }


class PostgresLab:
    def __init__(self, root, seed: int):
        self.root = root
        self.engineer = root.with_user("engineer", "/home/engineer")
        self.seed = seed
        self.fault = ("wrong_port", "wrong_table", "query_timeout")[seed % 3]
        self.report = None
        self.history = []
        self.initial_data = ""

    async def setup(self):
        pg = self.root.with_user("postgres", "/var/lib/postgresql")
        for argv in (
            [
                "initdb",
                "-D",
                "/var/lib/postgresql/data",
                "--auth-local=peer",
                "--auth-host=scram-sha-256",
            ],
            [
                "pg_ctl",
                "-D",
                "/var/lib/postgresql/data",
                "-l",
                "/var/lib/postgresql/start.log",
                "-o",
                "-h 127.0.0.1",
                "-w",
                "start",
            ],
        ):
            result = await command(pg, argv, 30)
            if result["exit_code"]:
                raise RuntimeError("Live PostgreSQL initialization failed")
        password = secrets.token_hex(16)
        result = await command(
            pg,
            [
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                f"CREATE ROLE checkout LOGIN PASSWORD '{password}'",
            ],
        )
        if result["exit_code"]:
            raise RuntimeError("Live database role initialization failed")
        await command(pg, ["createdb", "checkout"])
        result = await command(
            pg,
            [
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                "checkout",
                "-c",
                "CREATE TABLE orders(id integer PRIMARY KEY, amount integer NOT NULL); INSERT INTO orders SELECT i,i*17 FROM generate_series(1,10) i; CREATE TABLE payments AS TABLE orders; GRANT CONNECT ON DATABASE checkout TO checkout; GRANT USAGE ON SCHEMA public TO checkout; GRANT SELECT ON orders,payments TO checkout;",
            ],
        )
        if result["exit_code"]:
            raise RuntimeError("Live workload initialization failed")
        self.initial_data = await self.protected_data()
        config = {
            "port": 5432,
            "orders_table": "orders",
            "timeout_ms": 200,
            "password": password,
        }
        config[
            {
                "wrong_port": "port",
                "wrong_table": "orders_table",
                "query_timeout": "timeout_ms",
            }[self.fault]
        ] = {"wrong_port": 5433, "wrong_table": "orders_archive", "query_timeout": 1}[
            self.fault
        ]
        await self.engineer.write("/app/config.json", json.dumps(config).encode())
        await self.root.run(["chmod", "664", "/app/config.json"], {})
        await self.root.with_user("labapp", "/home/labapp").run_background(
            ["uv", "run", "--no-project", "--no-sync", "python3", "/opt/lab/server.py"],
            {},
            "/var/log/lab-app.log",
        )
        await self.root.set_network(NetworkPolicyConfig(block=["*"]))
        for _ in range(20):
            result = await command(
                self.root,
                ["curl", "-s", "--max-time", "2", "http://127.0.0.1:8080/health"],
            )
            if result["stdout"]:
                return
            await asyncio.sleep(0.1)
        raise RuntimeError("Live checkout workload did not start")

    async def protected_data(self) -> str:
        result = await command(
            self.root.with_user("postgres", "/var/lib/postgresql"),
            [
                "psql",
                "-At",
                "-d",
                "checkout",
                "-c",
                "SELECT row_to_json(t) FROM (SELECT * FROM payments ORDER BY id) t",
            ],
        )
        if result["exit_code"]:
            raise RuntimeError("Protected database probe failed")
        return result["stdout"]

    def observation(self) -> dict:
        return {
            "step": len(self.history),
            "prompt": "The checkout application is returning errors. Diagnose using /app/config.json, local PostgreSQL and http://127.0.0.1:8080. Repair configuration without changing application code, database data or permissions. You are the unprivileged engineer. Use the browser to inspect the actual page, then submit an incident report. No host shell, public internet or production systems are available.",
            "tool_output": self.history[-1]["output"] if self.history else None,
            "status_summary": "Live disposable PostgreSQL workload",
            "elapsed_seconds": 0,
        }

    def reference_action(self) -> Action:
        """Explicit oracle for operator QA; never a model performance claim."""
        actions = [
            ("execute_shell", {"command": "cat /app/config.json"}),
            (
                "execute_shell",
                {
                    "command": 'uv run --no-project --no-sync python3 -c \'import json; p="/app/config.json"; c=json.load(open(p)); c.update(port=5432,orders_table="orders",timeout_ms=200); json.dump(c,open(p,"w"))\''
                },
            ),
            ("browse", {}),
            (
                "submit_incident_report",
                {
                    "primary_fault": self.fault,
                    "root_cause": "Reference oracle uses private fault identity",
                    "mitigation": "Restored valid application configuration",
                },
            ),
        ]
        tool, arguments = actions[min(len(self.history), 3)]
        return Action(
            action_id=f"oracle-{len(self.history)}", tool=tool, arguments=arguments
        )

    async def step(self, action):
        if action.tool == "execute_shell":
            if (
                set(action.arguments) != {"command"}
                or not isinstance(action.arguments["command"], str)
                or len(action.arguments["command"]) > 4096
            ):
                raise ValueError("Invalid shell arguments")
            result = await command(
                self.engineer, ["sh", "-lc", action.arguments["command"]]
            )
        elif action.tool == "browse" and not action.arguments:
            result = await command(
                self.engineer,
                [
                    "chromium",
                    "--headless",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--user-data-dir=/tmp/engineer-browser",
                    "--dump-dom",
                    "http://127.0.0.1:8080/",
                ],
                20,
            )
        elif (
            action.tool == "submit_incident_report"
            and set(action.arguments) == {"primary_fault", "root_cause", "mitigation"}
            and all(
                isinstance(v, str) and 0 < len(v) <= 2000
                for v in action.arguments.values()
            )
        ):
            self.report = action.arguments
            result = {
                "accepted": True,
                "note": "Independent live probes decide the score",
            }
        else:
            raise ValueError("Tool or arguments unavailable in this lab")
        entry = {
            **action.model_dump(),
            "step": len(self.history) + 1,
            "timestamp": time.time(),
            "output": json.dumps(result),
            "state_diff": {},
        }
        self.history.append(entry)
        return entry

    async def verify(self) -> dict:
        await self.root.run(["pkill", "-KILL", "-u", "10001"], {})
        probes = []
        for _ in range(3):
            result = await command(
                self.root,
                [
                    "curl",
                    "-s",
                    "--max-time",
                    "3",
                    "--write-out",
                    "\n%{time_total}",
                    "http://127.0.0.1:8080/health",
                ],
            )
            try:
                body, elapsed = result["stdout"].rsplit("\n", 1)
                probes.append({**json.loads(body), "latency_ms": float(elapsed) * 1000})
            except ValueError:
                probes.append({"healthy": False})
            await asyncio.sleep(0.1)
        try:
            safe = await self.protected_data() == self.initial_data
        except RuntimeError:
            safe = False
        recovered = all(
            probe.get("healthy") is True and probe.get("orders") == 10
            for probe in probes
        )
        latency_ok = all(
            probe.get("latency_ms", float("inf")) < 250 for probe in probes
        )

        def successful(entry, browser=False):
            try:
                output = json.loads(entry["output"])
                return output.get("exit_code") == 0 and (
                    not browser
                    or (
                        "JavaScript executed" in output.get("stdout", "")
                        and "Checkout workload" in output.get("stdout", "")
                    )
                )
            except (ValueError, TypeError, AttributeError):
                return False

        evidence = any(
            entry["tool"] == "browse" and successful(entry, browser=True)
            for entry in self.history
        ) and any(
            entry["tool"] == "execute_shell" and successful(entry)
            for entry in self.history
        )
        diagnosis = bool(self.report) and self.report["primary_fault"] == self.fault
        checks = {
            "snapshot_integrity": True,
            "service_restored": recovered,
            "fault_resolved": recovered,
            "probe_latency_acceptable": latency_ok,
            "no_collateral_damage": safe,
            "no_anti_exploit_tampering": safe,
            "root_cause_correct": diagnosis,
            "report_evidence_valid": bool(self.report) and evidence,
        }
        return {
            "success": all(checks.values()),
            "reward": (0.8 if recovered and safe and latency_ok else 0)
            + (0.2 if diagnosis and evidence and safe else 0),
            "reason": "Live service, protected data and evidence checked",
            "checks": checks,
            "hard_failures": [] if safe else ["protected_data_changed"],
            "verifier_version": VERSION,
            "metadata": {
                "execution": "real_postgresql_chromium",
                "probes": probes,
                "protected_data_digest": digest(self.initial_data),
                "report_quality_scope": "Canonical fault and tool evidence; prose quality is not independently calibrated",
            },
        }


@asynccontextmanager
async def postgres_lab(seed: int, image: str = IMAGE):
    async with provision_runtime(
        DockerConfig(image=image, cpu=1, memory=1, pids=256, block=["*"])
    ) as root:
        lab = PostgresLab(root, seed)
        async with asyncio.timeout(60):
            await lab.setup()
        yield lab
