from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_integration_contract_exists() -> None:
    contract = (ROOT / "configs" / "runtime_integration.yaml").read_text(encoding="utf-8")
    assert "hermes_retek_is_host_side_governance: true" in contract
    assert "do_not_replace_hermes_core_by_default: true" in contract
    assert "blind_git_pull_on_server_forbidden: true" in contract
    assert "host_side_governance:" in contract
    assert "hermes_core:" in contract


def test_agents_runtime_boundary_matches_contract() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "RUNTIME BOUNDARY" in agents
    assert "Host -> Hermes_Retek scripts/configs" in agents
    assert "не меняй `hermes-core`" in agents
    assert "blind `git pull`" in agents


def test_readme_links_runtime_integration_doc() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Runtime Integration Note" in readme
    assert "docs/17_hermes_runtime_integration.md" in readme
