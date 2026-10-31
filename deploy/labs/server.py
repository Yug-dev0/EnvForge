"""Disposable checkout workload. This file is read-only to the evaluated agent."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg2


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            config = json.loads(Path("/app/config.json").read_text())
            with (
                psycopg2.connect(
                    host="127.0.0.1",
                    port=config["port"],
                    user="checkout",
                    password=config["password"],
                    dbname="checkout",
                    connect_timeout=1,
                ) as conn,
                conn.cursor() as cursor,
            ):
                cursor.execute(
                    "SET statement_timeout = %s", (int(config["timeout_ms"]),)
                )
                cursor.execute("SELECT pg_sleep(0.01)")
                # The table selector is a constrained application setting.
                table = config["orders_table"]
                if table not in {"orders", "orders_archive"}:
                    raise ValueError("Invalid orders table setting")
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
            result, status = {"healthy": count == 10, "orders": count}, 200
        except (psycopg2.Error, ValueError, KeyError, OSError, TypeError) as error:
            # Only synthetic workload configuration is present in this container.
            result, status = (
                {"healthy": False, "error": str(error).splitlines()[0][:300]},
                503,
            )
        data = json.dumps(result).encode()
        if self.path == "/":
            data = (
                "<html><body><h1>Checkout workload</h1><pre>"
                + json.dumps(result).replace("<", "&lt;")
                + '</pre><p id="browser-proof"></p><script>document.getElementById("browser-proof").textContent="JavaScript executed"</script></body></html>'
            ).encode()
        self.send_response(status)
        self.send_header(
            "Content-Type", "text/html" if self.path == "/" else "application/json"
        )
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
