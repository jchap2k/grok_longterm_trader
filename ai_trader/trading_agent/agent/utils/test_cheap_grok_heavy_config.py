import asyncio
import inspect
import json
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.utils.cheap_grok_heavy import (
    CheapGrokHeavy,
    format_cli_run_header,
    load_agent_specs_from_file,
    _chat_completion_response_from_xai_sdk_response,
    parse_context_file_args,
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
        Path(__file__).resolve().parents[1]
        / "configs"
        / "longterm_trading_agent_specs.json"
    )

    specs = load_agent_specs_from_file(str(config_path), preset_name="decision_4")

    assert [spec["name"] for spec in specs] == [
        "FundamentalAnalyst",
        "MacroRiskAnalyst",
        "ThesisCritic",
        "DecisionIntegrator",
    ]
    assert "research_principles" in specs[0]["input_sections"]


def test_longterm_agent_config_exposes_decision_presets():
    config_path = (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "longterm_trading_agent_specs.json"
    )

    decision_4 = load_agent_specs_from_file(str(config_path), preset_name="decision_4")
    decision_6 = load_agent_specs_from_file(str(config_path), preset_name="decision_6")

    assert [spec["name"] for spec in decision_4] == [
        "FundamentalAnalyst",
        "MacroRiskAnalyst",
        "ThesisCritic",
        "DecisionIntegrator",
    ]
    assert [spec["name"] for spec in decision_6] == [
        "FundamentalAnalyst",
        "MacroRiskAnalyst",
        "ValuationEdgeAnalyst",
        "PortfolioManager",
        "ThesisCritic",
        "DecisionIntegrator",
    ]


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


def test_prepare_agent_output_for_synthesis_preserves_json_executive_summary():
    client = CheapGrokHeavy(api_key="test-key", verbose=False)
    long_detail = "detail " * 500
    agent_text = json.dumps(
        {
            "executive_summary": "Buy only if margin of safety improves.",
            "detailed_analysis": long_detail,
        }
    )

    prepared = client._prepare_agent_output_for_synthesis(agent_text, max_chars=600)

    assert "EXECUTIVE SUMMARY:" in prepared
    assert "Buy only if margin of safety improves." in prepared
    assert "DETAILED OUTPUT" in prepared
    assert len(prepared) < len(agent_text)


def test_prepare_agent_output_for_synthesis_truncates_long_unstructured_output():
    client = CheapGrokHeavy(api_key="test-key", verbose=False)
    agent_text = "start-" + ("middle-" * 500) + "end"

    prepared = client._prepare_agent_output_for_synthesis(agent_text, max_chars=300)

    assert prepared.startswith("start-")
    assert prepared.endswith("end")
    assert "[truncated" in prepared
    assert len(prepared) < len(agent_text)


def test_merge_context_files_adds_named_file_sections_without_mutating_input(tmp_path):
    client = CheapGrokHeavy(api_key="test-key", verbose=False)
    brief_path = tmp_path / "strategy_brief.md"
    brief_path.write_text("Respect benchmark discipline.", encoding="utf-8")
    base_context = {"research_packet": "NVDA packet"}

    merged = client._merge_context_files(
        base_context,
        {"strategy_brief": brief_path},
    )

    assert merged["research_packet"] == "NVDA packet"
    assert merged["strategy_brief"] == "Respect benchmark discipline."
    assert "strategy_brief" not in base_context


def test_parse_context_file_args_returns_named_paths(tmp_path):
    brief_path = tmp_path / "strategy_brief.md"
    safety_path = tmp_path / "safety.md"

    parsed = parse_context_file_args([
        f"strategy_brief={brief_path}",
        f"safety={safety_path}",
    ])

    assert parsed == {
        "strategy_brief": str(brief_path),
        "safety": str(safety_path),
    }


def test_parse_context_file_args_rejects_missing_name_separator(tmp_path):
    brief_path = tmp_path / "strategy_brief.md"

    try:
        parse_context_file_args([str(brief_path)])
    except ValueError as exc:
        assert "name=path" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_format_cli_run_header_uses_configured_agent_count():
    client = CheapGrokHeavy(
        api_key="test-key",
        verbose=False,
        agent_specs=[
            {"name": "One", "temperature": 0.1},
            {"name": "Two", "temperature": 0.2},
            {"name": "Three", "temperature": 0.3},
        ],
        max_concurrent=2,
    )

    header = format_cli_run_header(client)

    assert "3 agents" in header
    assert "max_concurrent=2" in header
    assert "grok-4.3" in header
    assert "api_backend=xai_sdk" in header


def test_build_agent_messages_prepends_shared_system_context_for_cacheable_prefix():
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
    shared = "CORE RULES: obey protected holdings and benchmark discipline."

    first_messages = client._build_agent_messages(
        "Review AAPL.",
        client.agent_specs[0],
        shared_system_context=shared,
    )
    second_messages = client._build_agent_messages(
        "Review MSFT.",
        client.agent_specs[1],
        shared_system_context=shared,
    )

    assert first_messages[0]["role"] == "system"
    assert second_messages[0]["role"] == "system"
    assert first_messages[0]["content"].startswith(shared)
    assert second_messages[0]["content"].startswith(shared)
    assert "Analyze fundamentals." in first_messages[0]["content"]
    assert "Analyze macro risk." in second_messages[0]["content"]
    assert first_messages[1] == {"role": "user", "content": "Review AAPL."}


def test_cached_prompt_tokens_are_extracted_from_chat_completion_usage_details():
    client = CheapGrokHeavy(api_key="test-key", verbose=False)
    usage = SimpleNamespace(
        prompt_tokens=123,
        completion_tokens=45,
        prompt_tokens_details=SimpleNamespace(cached_tokens=67),
    )

    assert client._usage_cached_prompt_tokens(usage) == 67


def test_cache_conversation_id_builds_x_grok_conv_id_header():
    client = CheapGrokHeavy(api_key="test-key", verbose=False)

    assert client._cache_extra_headers(None) == {}
    assert client._cache_extra_headers("longterm-decision-abc123") == {
        "x-grok-conv-id": "longterm-decision-abc123"
    }


def test_call_agent_records_cached_tokens_with_fake_chat_completion_client():
    class FakeCompletions:
        def __init__(self):
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=SimpleNamespace(
                    prompt_tokens=123,
                    completion_tokens=45,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=67),
                ),
            )

    fake_completions = FakeCompletions()
    client = CheapGrokHeavy(
        api_key="test-key",
        verbose=False,
        agent_specs=[
            {
                "name": "FundamentalAnalyst",
                "temperature": 0.15,
                "system_prompt": "Analyze fundamentals.",
            }
        ],
    )
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake_completions)
    )

    result = asyncio.run(
        client._call_agent(
            "Review AAPL.",
            client.agent_specs[0],
            0,
            shared_system_context="CORE RULES: protect benchmark holdings.",
            cache_conversation_id="longterm-decision-test",
        )
    )

    assert result["input_tokens"] == 123
    assert result["cached_input_tokens"] == 67
    assert result["output_tokens"] == 45
    messages = fake_completions.kwargs["messages"]
    assert messages[0]["content"].startswith("CORE RULES")
    assert "Analyze fundamentals." in messages[0]["content"]
    assert fake_completions.kwargs["extra_headers"] == {
        "x-grok-conv-id": "longterm-decision-test"
    }


def test_xai_sdk_response_adapter_extracts_authoritative_cost_and_tool_usage():
    response = SimpleNamespace(
        content="ok",
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=2000,
            total_tokens=3000,
            cached_prompt_text_tokens=400,
            cost_in_usd_ticks=212_500_000,
        ),
        server_side_tool_usage={"web_search": 3},
    )

    adapted = _chat_completion_response_from_xai_sdk_response(response)

    assert adapted.choices[0].message.content == "ok"
    assert adapted.usage.prompt_tokens == 1000
    assert adapted.usage.completion_tokens == 2000
    assert adapted.usage.prompt_tokens_details.cached_tokens == 400
    assert adapted.usage.cost_usd == 0.02125
    assert adapted.usage.server_side_tool_usage == {"web_search": 3}
    assert adapted.usage.tool_invocation_count == 3
    assert adapted.usage.web_search_call_count == 3
    assert adapted.usage.web_search_cost_usd == 0.015


def test_installed_xai_sdk_chat_create_supports_temperature():
    from xai_sdk import Client

    client = Client(api_key="test-key")
    signature = inspect.signature(client.chat.create)

    assert "temperature" in signature.parameters


def test_print_cost_prefers_xai_sdk_actual_cost_when_available(capsys):
    client = CheapGrokHeavy(api_key="test-key", verbose=True)

    client._print_cost(
        [
            {
                "input_tokens": 1000,
                "cached_input_tokens": 400,
                "output_tokens": 2000,
                "estimated_total_cost_usd": 0.02125,
                "tool_invocation_count": 3,
                "tool_cost_usd": 0.015,
                "error": None,
            }
        ]
    )

    output = capsys.readouterr().out
    assert "3 tools ($0.0150)" in output
    assert "actual $0.0213" in output


def test_print_cost_adds_tool_cost_to_token_estimate_when_actual_cost_missing(capsys):
    client = CheapGrokHeavy(api_key="test-key", verbose=True)

    client._print_cost(
        [
            {
                "input_tokens": 1000,
                "cached_input_tokens": 0,
                "output_tokens": 2000,
                "estimated_total_cost_usd": 0.0,
                "tool_invocation_count": 3,
                "tool_cost_usd": 0.015,
                "error": None,
            }
        ]
    )

    output = capsys.readouterr().out
    assert "3 tools ($0.0150)" in output
    assert "estimated $0.0212" in output


def test_call_agent_omits_cache_header_by_default_with_fake_chat_completion_client():
    class FakeCompletions:
        def __init__(self):
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
            )

    fake_completions = FakeCompletions()
    client = CheapGrokHeavy(
        api_key="test-key",
        verbose=False,
        agent_specs=[{"name": "FundamentalAnalyst", "temperature": 0.15}],
    )
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake_completions)
    )

    result = asyncio.run(client._call_agent("Review AAPL.", client.agent_specs[0], 0))

    assert result["error"] is None
    assert fake_completions.kwargs["extra_headers"] == {}


def test_synthesis_logs_cached_prompt_tokens_with_fake_chat_completion_client(capsys):
    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="synthesized"))],
                usage=SimpleNamespace(
                    prompt_tokens=200,
                    completion_tokens=50,
                    cost_usd=0.0075,
                    tool_invocation_count=1,
                    web_search_call_count=1,
                    web_search_cost_usd=0.005,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=19),
                ),
            )

    client = CheapGrokHeavy(
        api_key="test-key",
        verbose=True,
        agent_specs=[
            {"name": "One", "temperature": 0.1},
            {"name": "Two", "temperature": 0.2},
        ],
    )
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )

    result = asyncio.run(
        client._synthesize(
            "Question",
            [
                {"idx": 0, "name": "One", "temperature": 0.1, "text": "answer one", "error": None},
                {"idx": 1, "name": "Two", "temperature": 0.2, "text": "answer two", "error": None},
            ],
        )
    )

    output = capsys.readouterr().out
    assert result == "synthesized"
    assert "19 cached input tokens" in output
    assert client._last_synthesis_usage["estimated_total_cost_usd"] == 0.0075
    assert client._last_synthesis_usage["tool_invocation_count"] == 1


def test_call_async_with_context_applies_shared_prefix_to_agents_and_synthesis():
    class FakeCompletions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            content = "final decision" if len(self.calls) == 3 else f"agent {len(self.calls)}"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(
                    prompt_tokens=20,
                    completion_tokens=5,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=3),
                ),
            )

    fake_completions = FakeCompletions()
    client = CheapGrokHeavy(
        api_key="test-key",
        verbose=False,
        agent_specs=[
            {
                "name": "FundamentalAnalyst",
                "temperature": 0.15,
                "system_prompt": "Analyze fundamentals.",
                "input_sections": ["company_research_packet"],
            },
            {
                "name": "RiskAnalyst",
                "temperature": 0.2,
                "system_prompt": "Analyze risk.",
                "input_sections": ["risk_flags"],
            },
        ],
    )
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake_completions)
    )

    result = asyncio.run(
        client.call_async_with_context(
            "Review this stock.",
            {
                "company_research_packet": "AAPL packet",
                "risk_flags": "valuation risk",
            },
            shared_system_context="CORE RULES: protected holdings stay protected.",
            cache_conversation_id="longterm-decision-test",
        )
    )

    assert result == "final decision"
    assert len(fake_completions.calls) == 3
    for call in fake_completions.calls:
        assert call["extra_headers"] == {"x-grok-conv-id": "longterm-decision-test"}
        messages = call["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"].startswith("CORE RULES")
    assert "Analyze fundamentals." in fake_completions.calls[0]["messages"][0]["content"]
    assert "Analyze risk." in fake_completions.calls[1]["messages"][0]["content"]
    assert "AAPL packet" in fake_completions.calls[0]["messages"][1]["content"]
    assert "valuation risk" in fake_completions.calls[1]["messages"][1]["content"]


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


def test_high_weight_integrator_configs_request_executive_summary():
    config_dir = Path(__file__).resolve().parents[1] / "configs"
    config_paths = [
        config_dir / "longterm_trading_agent_specs.json",
        config_dir / "planning_agent_specs.json",
        config_dir / "default_agent_specs_general.json",
    ]

    for config_path in config_paths:
        specs = load_agent_specs_from_file(str(config_path))
        integrators = [
            spec for spec in specs
            if spec["name"] in {"DecisionIntegrator", "MasterIntegrator"}
        ]
        assert integrators, config_path
        for spec in integrators:
            assert "executive_summary" in spec["output_schema"], spec["name"]


def test_longterm_strategy_brief_exists_and_captures_core_guardrails():
    brief_path = (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "strategy_brief_longterm.md"
    )

    brief = brief_path.read_text(encoding="utf-8")

    for required in [
        "order_submission_enabled=false",
        "protected holdings",
        "FXAIX",
        "FRED",
        "Motley Fool",
        "no-submit scheduler",
    ]:
        assert required in brief
