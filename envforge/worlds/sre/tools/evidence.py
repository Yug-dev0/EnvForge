"""Public diagnostic evidence eligibility shared by agent policy and verifier."""

DIAGNOSTIC_TOOLS = {"read_logs", "inspect_deploy", "query_metrics"}
MUTATION_TOOLS = {"edit_config", "restart_service", "rollback"}


def diagnostic_action_ids(history: list[dict]) -> list[str]:
    return [
        action["action_id"]
        for action in history
        if action["tool"] in DIAGNOSTIC_TOOLS
        and not action["output"].startswith('{"error"')
        and action["output"] != "No logs found matching criteria."
    ]
