from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_roles  # noqa: E402


def test_runtime_contract_names_single_writer_roles() -> None:
    contract = agent_roles.role_runtime_contract()

    assert contract["version"] == 1
    assert contract["single_writer_roles"] == ["architect", "devops_operator"]
    assert "security_vault_agent" in contract["tool_gateway_roles"]
    assert contract["parallel_roles"]["tester"] == 3
    assert contract["parallel_roles"]["bot1_developer"] == 1


def test_phase_filter_keeps_execution_roles_single_writer_or_isolated() -> None:
    execution_roles = agent_roles.roles_for_phase("execution")
    by_name = {role["name"]: role for role in execution_roles}

    assert set(by_name) == {"bot1_developer", "devops_operator"}
    assert by_name["bot1_developer"]["writes_shared_state"] is False
    assert by_name["devops_operator"]["writes_shared_state"] is True
    assert all(role["max_parallel"] == 1 for role in execution_roles)


def test_unknown_role_raises_key_error() -> None:
    try:
        agent_roles.role_by_name("../bad")
    except KeyError as exc:
        assert "unknown role" in str(exc)
    else:
        raise AssertionError("unknown role should fail closed")


def test_cli_outputs_json_for_phase() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "agent_roles.py"), "--phase", "verification"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [role["name"] for role in payload] == ["tester", "bot2_reviewer"]
