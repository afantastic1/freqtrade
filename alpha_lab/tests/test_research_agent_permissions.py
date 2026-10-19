from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / ".opencode" / "agents" / "alpha-researcher.md"


def test_research_agent_has_no_direct_panel_or_raw_market_file_read_rule():
    document = AGENT.read_text(encoding="utf-8")
    permissions = document.split("---", 2)[1]

    assert "permissions:" in permissions
    assert 'action: "*"' in permissions
    assert "resource: \"*\"" in permissions
    assert "effect: deny" in permissions
    assert "user_data/research/alpha_lab/panels/**" not in permissions
    assert "user_data/data/**" not in permissions
    assert "action: grep" not in permissions
    assert "action: shell" in permissions
    assert "user_data/research/alpha_lab/candidates/current/request.json" in permissions
    assert "user_data/research/alpha_lab/candidates/current/config.json" in permissions
    assert permissions.count("- action: edit") == 2
    assert "- action: edit\n    resource: user_data/research/alpha_lab/candidates/**" not in permissions
    assert "action: subagent" not in permissions  # catch-all deny covers it
    assert "V2 grep matches a regex" in permissions
    assert "user_data/research/alpha_lab/runs/**" not in permissions


def test_agent_instructions_require_gateway_for_panel_evidence():
    document = AGENT.read_text(encoding="utf-8")
    assert "Do not read panel files directly" in document
    assert "research_agent_cli" in document
