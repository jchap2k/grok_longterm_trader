import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.utils.cheap_grok_heavy import (
    CheapGrokHeavy,
    load_agent_specs_from_file,
    resolve_agent_specs,
)


def test_load_agent_specs_from_file_round_trips_json(tmp_path):
    config_path = tmp_path / "agent_specs.json"
    config_path.write_text(
        json.dumps(
            {
                "agent_specs": [
                    {
                        "name": "FundamentalAnalyst",
                        "temperature": 0.15,
                        "system_prompt": "Analyze fundamentals.",
                        "input_sections": ["company_research_packet"],
                        "output_schema": {"thesis_strength": "string"},
                        "weight": 1.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    specs = load_agent_specs_from_file(str(config_path))

    assert len(specs) == 1
    assert specs[0]["name"] == "FundamentalAnalyst"
    assert specs[0]["temperature"] == 0.15
    assert specs[0]["input_sections"] == ["company_research_packet"]


def test_custom_agent_specs_override_temperature_spread_and_count():
    client = CheapGrokHeavy(
        api_key="test-key",
        verbose=False,
        agent_specs=[
            {
                "name": "FundamentalAnalyst",
                "temperature": 0.15,
                "system_prompt": "Analyze fundamentals.",
            },
            {
                "name": "MacroRiskAnalyst",
                "temperature": 0.2,
                "system_prompt": "Analyze macro risk.",
            },
        ],
    )

    assert client.agent_count == 2
    assert [spec["temperature"] for spec in client.agent_specs] == [0.15, 0.2]


def test_build_agent_prompt_uses_only_requested_sections_and_schema():
    client = CheapGrokHeavy(
        api_key="test-key",
        verbose=False,
        agent_specs=[
            {
                "name": "ThesisCritic",
                "temperature": 0.3,
                "system_prompt": "Challenge the thesis.",
                "input_sections": ["bull_thesis", "supporting_evidence"],
                "output_schema": {
                    "failure_modes": ["list of strings"],
                    "kill_criteria": ["list of strings"],
                },
                "weight": 1.2,
            }
        ],
    )

    prompt = client._build_agent_user_prompt(
        task_prompt="Review this long-term stock idea.",
        spec=client.agent_specs[0],
        context_sections={
            "bull_thesis": "The company can compound for years.",
            "supporting_evidence": "Revenue and margins are rising.",
            "portfolio_context": "Existing position in semis.",
        },
    )

    assert "The company can compound for years." in prompt
    assert "Revenue and margins are rising." in prompt
    assert "Existing position in semis." not in prompt
    assert "failure_modes" in prompt
    assert "kill_criteria" in prompt


def test_longterm_agent_config_file_loads_expected_four_roles():
    config_path = (
        Path(__file__).resolve().parents[2]
        / "longterm"
        / "configs"
        / "longterm_agent_specs_v1.json"
    )

    specs = load_agent_specs_from_file(str(config_path))

    assert [spec["name"] for spec in specs] == [
        "FundamentalAnalyst",
        "MacroRiskAnalyst",
        "ThesisCritic",
        "DecisionIntegrator",
    ]
    assert "research_principles" in specs[0]["input_sections"]


def test_preferred_synthesis_schema_uses_highest_weight_agent_schema():
    client = CheapGrokHeavy(
        api_key="test-key",
        verbose=False,
        agent_specs=[
            {
                "name": "FundamentalAnalyst",
                "temperature": 0.15,
                "system_prompt": "Analyze fundamentals.",
                "output_schema": {"thesis_strength": "string"},
                "weight": 1.0,
            },
            {
                "name": "DecisionIntegrator",
                "temperature": 0.1,
                "system_prompt": "Integrate decisions.",
                "output_schema": {"recommendation": "string", "confidence": "integer"},
                "weight": 1.3,
            },
        ],
    )

    schema = client._preferred_synthesis_schema()

    assert schema == {"recommendation": "string", "confidence": "integer"}


def test_resolve_agent_specs_uses_named_preset_not_top_n_truncation():
    payload = {
        "agent_specs": [
            {"name": "Researcher", "temperature": 0.2},
            {"name": "Analyst", "temperature": 0.25},
            {"name": "Planner", "temperature": 0.3},
            {"name": "Creator", "temperature": 0.5},
            {"name": "Critic", "temperature": 0.35},
            {"name": "MasterIntegrator", "temperature": 0.05},
        ],
        "presets": {
            "minimal_4": [
                "Researcher",
                "Analyst",
                "Critic",
                "MasterIntegrator",
            ]
        },
    }

    specs = resolve_agent_specs(payload, preset_name="minimal_4")

    assert [spec["name"] for spec in specs] == [
        "Researcher",
        "Analyst",
        "Critic",
        "MasterIntegrator",
    ]


def test_general_default_config_minimal_preset_keeps_critic_and_integrator():
    config_path = (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "default_agent_specs_general.json"
    )

    specs = load_agent_specs_from_file(str(config_path), preset_name="minimal_4")

    assert [spec["name"] for spec in specs] == [
        "Researcher",
        "Analyst",
        "Critic",
        "MasterIntegrator",
    ]


def test_planning_and_code_review_configs_load_useful_presets():
    config_dir = Path(__file__).resolve().parents[1] / "configs"

    planning_specs = load_agent_specs_from_file(
        str(config_dir / "planning_agent_specs.json"),
        preset_name="planning_6",
    )
    review_specs = load_agent_specs_from_file(
        str(config_dir / "code_review_agent_specs.json"),
        preset_name="embedded_review_6",
    )

    assert [spec["name"] for spec in planning_specs] == [
        "GoalClarifier",
        "SystemMapper",
        "PlanArchitect",
        "RiskCritic",
        "Verifier",
        "DecisionIntegrator",
    ]
    assert [spec["name"] for spec in review_specs] == [
        "CorrectnessReviewer",
        "SecurityReviewer",
        "PerformanceReviewer",
        "MaintainabilityReviewer",
        "TestCoverageReviewer",
        "ReviewIntegrator",
    ]
