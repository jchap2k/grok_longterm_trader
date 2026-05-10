"""
cheap_grok_heavy.py
====================
Self-Mixture-of-Agents (Self-MoA) ensemble using the native xAI SDK by default.
Runs N parallel grok-4.3 calls with temperature
variation, then synthesizes via a master call.

SCOPE: Standalone batch tool. Does NOT write to learning.db, trade_journal,
decision_journal, or any live-trading subsystem. Output is plain text only.

Inspired by "Poor Man's Grok Heavy" (Reddit 2026-03) and validated by Grok:
  - Non-uniform temperature spread creates both rigorous and creative responses
  - Master synthesizer (in 'heavy mode') combines best of all agents
  - Cost-sensitive replacement for native multi-agent calls; default width is
    now 4 agents after the May 2026 Grok 4.1 fast deprecation.

Temperature spreads (Grok-validated, asymmetric density):
    8-agent:  [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.5]
    16-agent: [0.0, 0.05, 0.1, 0.2, ..., 1.1, 1.3, 1.4, 1.5]
    0.0 = fully deterministic/rigorous; 1.5 = maximally creative
    Non-uniform: denser at low end (rigorous), sparser at high end (creative)
    Both validated for Self-MoA diversity (avoids mid-range clustering).

Usage (standalone):
    from agent.utils.cheap_grok_heavy import CheapGrokHeavy

    client = CheapGrokHeavy(api_key=XAI_API_KEY, agent_count=4)
    result = client.call(prompt)             # synchronous
    result = await client.call_async(prompt) # async context

Usage (CLI self-test):
    python -m agent.utils.cheap_grok_heavy --test

Design notes:
  - Uses xai_sdk by default so provider-reported costs/tool usage can be captured
  - xai_sdk now exposes temperature; OpenAI-compatible REST remains available as fallback
  - agent_count=4 default: cost-controlled council for pricier Grok 4.3 calls
  - max_concurrent=4 default: asyncio.Semaphore caps burst to prevent 429s
  - API client adapter created once in __init__ and reused (not recreated per call)
  - Synthesis at temp=0.1 (focused/consistent), max_tokens = agent_max_tokens * 2
  - Agents at max_tokens=2048 (configurable via agent_max_tokens param)
  - Failed agents are excluded from synthesis (warning logged)
  - Token costs logged at $1.25/$2.50 per 1M (grok-4.3, May 2026)
"""

import asyncio
import json
import os
import sys
import time
import pathlib
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XAI_BASE_URL      = "https://api.x.ai/v1"
DEFAULT_MODEL     = "grok-4.3"
DEFAULT_AGENTS    = 4            # cost-controlled default after 4.1 fast deprecation
AGENT_TIMEOUT_S   = 180          # per-agent hard timeout
COST_INPUT_PER_M  = 1.25        # $ per 1M input tokens
COST_OUTPUT_PER_M = 2.50        # $ per 1M output tokens
XAI_COST_TICKS_PER_USD = 10_000_000_000
XAI_WEB_SEARCH_COST_PER_1K = 5.0

# Grok-validated 8-point spread: default for <= 8 agents.
# NOT evenly spaced - avoids mid-range clustering.
_TEMP_SPREAD_8 = [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.5]

# Grok-validated 16-point spread: for 9-16 agents.
# Asymmetric density: denser at rigorous low end (0.0-0.1),
# sparser at creative high end (1.1-1.5). Max=1.5 matches 8-agent ceiling
# - empirically confirmed: temps >1.5 reliably timeout at 180s budget.
_TEMP_SPREAD_16 = [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6,
                   0.7, 0.8, 0.9, 1.0, 1.1, 1.3, 1.4, 1.5]
# Note: Grok originally suggested 1.6 and 1.9 for the last two but empirical
# testing shows these reliably timeout (>180s API time) due to heavy reasoning.
# Capped at 1.4/1.5 which stay within budget and match the 8-agent ceiling.

_TEMP_SPREAD_FULL = _TEMP_SPREAD_8  # backward-compat alias


def resolve_agent_specs(payload, preset_name: str | None = None) -> list[dict]:
    """Resolve agent specs from a raw payload, optionally using a named preset."""
    if isinstance(payload, list):
        specs = payload
        presets = {}
    elif isinstance(payload, dict):
        specs = payload.get("agent_specs", [])
        presets = payload.get("presets", {}) or {}
    else:
        raise ValueError("Agent spec config must be a list or dictionary payload.")

    if not isinstance(specs, list):
        raise ValueError("Agent spec config must contain a list of agent_specs.")

    if not preset_name:
        return specs

    role_names = presets.get(preset_name)
    if not role_names:
        raise ValueError(f"Unknown agent preset: {preset_name}")

    spec_map = {spec.get("name"): spec for spec in specs}
    resolved = []
    for role_name in role_names:
        spec = spec_map.get(role_name)
        if spec is None:
            raise ValueError(f"Preset '{preset_name}' references unknown role '{role_name}'")
        resolved.append(spec)
    return resolved


def load_agent_specs_from_file(path: str, preset_name: str | None = None) -> list[dict]:
    """Load a list of agent specs from a JSON config file."""
    payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return resolve_agent_specs(payload, preset_name=preset_name)


def parse_context_file_args(values: list[str] | None) -> dict[str, str]:
    """Parse repeated CLI context-file args in name=path format."""
    parsed: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError("Context files must use name=path format.")
        name, path = raw.split("=", 1)
        name = name.strip()
        path = path.strip()
        if not name or not path:
            raise ValueError("Context files must use name=path format.")
        parsed[name] = path
    return parsed


def format_cli_run_header(client: "CheapGrokHeavy") -> str:
    """Return the CLI run summary after specs/presets have been resolved."""
    return (
        f"CheapGrokHeavy self-test: {client.agent_count} agents, "
        f"max_concurrent={client.max_concurrent}, model={client.model}, "
        f"api_backend={client.api_backend}"
    )


def _attr_or_key(value, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _xai_cost_usd_from_response(response) -> float:
    """Extract authoritative xAI SDK response cost when the SDK exposes it."""
    direct = _attr_or_key(response, "cost_usd")
    if direct not in (None, ""):
        return round(_safe_float(direct), 6)
    usage = _attr_or_key(response, "usage")
    ticks = _attr_or_key(usage, "cost_in_usd_ticks")
    if ticks not in (None, ""):
        return round(_safe_float(ticks) / XAI_COST_TICKS_PER_USD, 6)
    return 0.0


def _xai_server_side_tool_counts(response) -> dict[str, int]:
    """Normalize xAI SDK server-side tool usage into dashboard-friendly counts."""
    raw = _attr_or_key(response, "server_side_tool_usage")
    if callable(raw):
        raw = raw()
    if raw in (None, ""):
        usage = _attr_or_key(response, "usage")
        raw = _attr_or_key(usage, "server_side_tool_usage")
        if callable(raw):
            raw = raw()
    counts: dict[str, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            count = _safe_int(value)
            if count:
                counts[str(key)] = count
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, str):
                counts[item] = counts.get(item, 0) + 1
            elif isinstance(item, dict):
                name = str(item.get("tool") or item.get("name") or item.get("type") or "").strip()
                count = _safe_int(item.get("count") or item.get("calls") or 1)
                if name and count:
                    counts[name] = counts.get(name, 0) + count
            else:
                name = str(_attr_or_key(item, "tool") or _attr_or_key(item, "name") or _attr_or_key(item, "type") or "").strip()
                count = _safe_int(_attr_or_key(item, "count") or _attr_or_key(item, "calls") or 1)
                if name and count:
                    counts[name] = counts.get(name, 0) + count
    usage = _attr_or_key(response, "usage")
    generic_count = _safe_int(_attr_or_key(usage, "server_side_tools_used"))
    if generic_count and not counts:
        counts["unknown"] = generic_count
    return counts


def _chat_completion_response_from_xai_sdk_response(response):
    """Adapt a native xAI SDK response to the small chat-completion shape CGH expects."""
    usage = _attr_or_key(response, "usage")
    prompt_tokens = _safe_int(
        _attr_or_key(usage, "prompt_tokens")
        or _attr_or_key(usage, "prompt_text_tokens")
    )
    completion_tokens = _safe_int(_attr_or_key(usage, "completion_tokens"))
    cached_tokens = _safe_int(_attr_or_key(usage, "cached_prompt_text_tokens"))
    tool_counts = _xai_server_side_tool_counts(response)
    web_search_calls = _safe_int(tool_counts.get("web_search"))
    web_search_cost = round(web_search_calls / 1_000 * XAI_WEB_SEARCH_COST_PER_1K, 6)
    cost_usd = _xai_cost_usd_from_response(response)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=str(_attr_or_key(response, "content") or ""))
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=_safe_int(_attr_or_key(usage, "total_tokens")) or prompt_tokens + completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
            cost_usd=cost_usd,
            server_side_tool_usage=tool_counts,
            tool_invocation_count=sum(tool_counts.values()),
            web_search_call_count=web_search_calls,
            web_search_cost_usd=web_search_cost,
        ),
    )


class _XaiSdkChatCompletions:
    """Small async adapter exposing the chat.completions.create shape used internally."""

    def __init__(self, api_key: str):
        from xai_sdk import Client

        self._client = Client(api_key=api_key)

    async def create(self, **kwargs):
        return await asyncio.to_thread(self._create_sync, **kwargs)

    def _create_sync(self, **kwargs):
        from xai_sdk.chat import assistant, developer, system, user

        role_builders = {
            "assistant": assistant,
            "developer": developer,
            "system": system,
            "user": user,
        }
        messages = []
        for message in kwargs.get("messages") or []:
            role = str(message.get("role") or "user")
            content = message.get("content") or ""
            if not isinstance(content, str):
                content = json.dumps(content, sort_keys=True)
            messages.append(role_builders.get(role, user)(content))

        extra_headers = kwargs.get("extra_headers") or {}
        conversation_id = extra_headers.get("x-grok-conv-id") if isinstance(extra_headers, dict) else None
        chat = self._client.chat.create(
            model=kwargs["model"],
            messages=messages,
            max_tokens=kwargs.get("max_tokens"),
            temperature=kwargs.get("temperature"),
            conversation_id=conversation_id,
        )
        return _chat_completion_response_from_xai_sdk_response(chat.sample())


class _XaiSdkClientAdapter:
    """Expose chat.completions.create using native xAI SDK underneath."""

    def __init__(self, api_key: str):
        self.chat = SimpleNamespace(completions=_XaiSdkChatCompletions(api_key))


def _normalize_agent_spec(spec: dict, fallback_temperature: float | None = None) -> dict:
    """Normalize one external agent spec into the runtime format."""
    if not isinstance(spec, dict):
        raise ValueError("Each agent spec must be a dictionary.")

    temperature = spec.get("temperature", fallback_temperature)
    if temperature is None:
        temperature = 0.5

    return {
        "name": spec.get("name", "Agent"),
        "temperature": float(temperature),
        "system_prompt": spec.get("system_prompt", "").strip(),
        "input_sections": list(spec.get("input_sections", []) or []),
        "output_schema": spec.get("output_schema", {}) or {},
        "weight": float(spec.get("weight", 1.0)),
    }


def _select_temps(n: int) -> list:
    """
    Select n temperatures using the Grok-validated asymmetric spreads.

    For n <= 8:  subsample _TEMP_SPREAD_8  (8-point, max=1.5)
    For 8 < n <= 16: subsample _TEMP_SPREAD_16 (16-point, max=1.5)
    For n > 16:  greedy midpoint insertion from _TEMP_SPREAD_16

    Non-uniform density (denser at rigorous low end, sparser at creative
    high end) maximizes agent diversity in Self-MoA - avoids mid-range
    clustering that uniform spacing produces.
    """
    if n == 1:
        return [0.5]

    # Choose base spread: 16-point for n > 8, else 8-point
    base = _TEMP_SPREAD_16 if n > 8 else _TEMP_SPREAD_8

    if n >= len(base):
        # Greedy midpoint insertion: preserve all anchors, fill gaps until n reached
        pts = list(base)
        while len(pts) < n:
            gaps = [(pts[i + 1] - pts[i], i) for i in range(len(pts) - 1)]
            _, best_i = max(gaps)
            mid = round((pts[best_i] + pts[best_i + 1]) / 2, 2)
            pts.insert(best_i + 1, mid)
        return pts

    # Subsample: evenly spaced indices into base spread
    indices = [round(i * (len(base) - 1) / (n - 1)) for i in range(n)]
    seen = set()
    result = []
    for idx in indices:
        if idx not in seen:
            seen.add(idx)
            result.append(base[idx])
    # Fill any gaps if dedup reduced length
    for idx in range(len(base)):
        if len(result) >= n:
            break
        if idx not in seen:
            seen.add(idx)
            result.append(base[idx])
    return sorted(result[:n])


# ---------------------------------------------------------------------------
# CheapGrokHeavy
# ---------------------------------------------------------------------------

class CheapGrokHeavy:
    """
    Ensemble wrapper: N parallel calls + master synthesis.

    Uses the native xAI SDK by default so provider-reported costs and server-side
    tool usage can be captured. The OpenAI-compatible xAI endpoint remains
    available only as an explicit fallback via api_backend="openai_compat".
    Temperature variation creates agent diversity without needing different models.
    An asyncio.Semaphore caps concurrent requests to prevent API rate limiting.

    Args:
        api_key:          xAI API key (falls back to XAI_API_KEY env var)
        agent_count:      number of parallel agents (default 4, cost-controlled)
        model:            model name for all calls (default grok-4.3)
        agent_max_tokens: max output tokens per agent (default 2048); synthesis gets 2x
        max_concurrent:   max simultaneous API calls via semaphore (default min(4, agent_count))
                          Lower this on shared accounts or if hitting 429s.
        verbose:          print per-agent progress (default True)
    """

    def __init__(
        self,
        api_key: str | None = None,
        agent_count: int = DEFAULT_AGENTS,
        agent_specs: list[dict] | None = None,
        agent_specs_path: str | None = None,
        agent_preset: str | None = None,
        model: str = DEFAULT_MODEL,
        agent_max_tokens: int = 2048,
        max_concurrent: int | None = None,
        verbose: bool = True,
        api_backend: str = "xai_sdk",
    ):
        self.api_key          = api_key or os.environ.get("XAI_API_KEY", "")
        self.model            = model
        self.agent_max_tokens = agent_max_tokens
        self.verbose          = verbose
        self.api_backend      = api_backend
        self._last_synthesis_usage: dict | None = None
        self.last_usage: dict | None = None

        if agent_specs is not None and agent_specs_path is not None:
            raise ValueError("Pass either agent_specs or agent_specs_path, not both.")
        if agent_preset is not None and agent_specs_path is None:
            raise ValueError("agent_preset requires agent_specs_path.")

        loaded_specs = agent_specs
        if agent_specs_path:
            loaded_specs = load_agent_specs_from_file(agent_specs_path, preset_name=agent_preset)

        if loaded_specs:
            self.agent_specs = [
                _normalize_agent_spec(spec)
                for spec in loaded_specs
            ]
        else:
            self.agent_specs = [
                _normalize_agent_spec(
                    {
                        "name": f"Agent {idx + 1}",
                        "temperature": temp,
                        "system_prompt": "",
                    },
                    fallback_temperature=temp,
                )
                for idx, temp in enumerate(_select_temps(max(1, agent_count)))
            ]

        self.agent_count = len(self.agent_specs)
        self.max_concurrent = max_concurrent or min(4, self.agent_count)

        if not self.api_key:
            raise ValueError(
                "xAI API key required. Pass api_key= or set XAI_API_KEY env var."
            )

        self._client = self._build_client_adapter(api_backend)

        # Semaphore initialized lazily in the first async context to ensure
        # it binds to the correct event loop (Python < 3.10 compatibility).
        self._semaphore: asyncio.Semaphore | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_client_adapter(self, api_backend: str):
        backend = str(api_backend or "xai_sdk").strip().lower()
        if backend == "xai_sdk":
            return _XaiSdkClientAdapter(self.api_key)
        if backend in {"openai", "openai_compat", "openai-compatible"}:
            import openai

            return openai.AsyncOpenAI(
                api_key=self.api_key,
                base_url=XAI_BASE_URL,
            )
        raise ValueError("api_backend must be 'xai_sdk' or 'openai_compat'.")

    def _temperatures(self) -> list:
        """Select temperature spread for the configured agent count."""
        return [spec["temperature"] for spec in self.agent_specs]

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def _preferred_synthesis_schema(self) -> dict:
        """Return the output schema from the highest-weight configured agent."""
        ranked = sorted(
            self.agent_specs,
            key=lambda spec: (spec.get("weight", 1.0), bool(spec.get("output_schema"))),
            reverse=True,
        )
        for spec in ranked:
            schema = spec.get("output_schema") or {}
            if schema:
                return schema
        return {}

    def _prepare_agent_output_for_synthesis(self, text: str, max_chars: int = 1600) -> str:
        """Keep the agent's decision summary while trimming verbose detail."""
        if len(text) <= max_chars:
            return text

        summary = ""
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                candidate = payload.get("executive_summary")
                if isinstance(candidate, str):
                    summary = candidate.strip()
        except json.JSONDecodeError:
            summary = ""

        head_chars = max(1, max_chars // 2)
        tail_chars = max(1, max_chars // 3)
        detail = (
            f"{text[:head_chars]}\n\n"
            f"... [truncated {max(0, len(text) - head_chars - tail_chars)} chars for synthesis] ...\n\n"
            f"{text[-tail_chars:]}"
        )

        if summary:
            return f"EXECUTIVE SUMMARY:\n{summary[:650]}\n\nDETAILED OUTPUT (truncated):\n{detail}"
        return detail

    def _merge_context_files(
        self,
        context_sections: dict | None,
        context_files: dict[str, str | pathlib.Path] | None,
    ) -> dict:
        """Read named context files and merge them into context sections."""
        merged = dict(context_sections or {})
        for key, path in (context_files or {}).items():
            merged[key] = pathlib.Path(path).read_text(encoding="utf-8")
        return merged

    @staticmethod
    def _usage_cached_prompt_tokens(usage) -> int:
        """Extract provider-reported cached prompt tokens when available."""
        details = getattr(usage, "prompt_tokens_details", None)
        if details is None and isinstance(usage, dict):
            details = usage.get("prompt_tokens_details")
        if details is None:
            return 0
        if isinstance(details, dict):
            value = details.get("cached_tokens", 0)
        else:
            value = getattr(details, "cached_tokens", 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _cache_extra_headers(cache_conversation_id: str | None) -> dict[str, str]:
        """Build optional xAI cache-routing headers."""
        if not cache_conversation_id:
            return {}
        return {"x-grok-conv-id": str(cache_conversation_id)}

    def _build_agent_messages(
        self,
        prompt: str,
        spec: dict,
        *,
        shared_system_context: str | None = None,
    ) -> list[dict[str, str]]:
        """Build chat messages, keeping any shared system context as a common prefix."""
        messages: list[dict[str, str]] = []
        system_parts = []
        if shared_system_context:
            system_parts.append(shared_system_context.strip())
        if spec.get("system_prompt"):
            system_parts.append(spec["system_prompt"].strip())
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily create semaphore in the running event loop."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    # ------------------------------------------------------------------
    # Single agent call (async)
    # ------------------------------------------------------------------

    async def _call_agent(
        self,
        prompt: str,
        spec: dict,
        agent_idx: int,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> dict:
        """
        Call one agent behind the concurrency semaphore.
        Returns dict with keys: idx, temperature, text,
        input_tokens, output_tokens, elapsed_s, error.
        """
        result = {
            "idx":           agent_idx,
            "name":          spec["name"],
            "temperature":   spec["temperature"],
            "text":          None,
            "input_tokens":  0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "estimated_total_cost_usd": 0.0,
            "tool_invocation_count": 0,
            "web_search_call_count": 0,
            "tool_cost_usd": 0.0,
            "elapsed_s":     0.0,
            "error":         None,
        }
        t0 = time.time()
        async with self._get_semaphore():
            try:
                self._log(
                    f"[CheapGrokHeavy] Agent {agent_idx + 1}/{self.agent_count} "
                    f"starting ({spec['name']}, temp={spec['temperature']:.2f})..."
                )
                messages = self._build_agent_messages(
                    prompt,
                    spec,
                    shared_system_context=shared_system_context,
                )
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self.model,
                        temperature=spec["temperature"],
                        max_tokens=self.agent_max_tokens,
                        messages=messages,
                        extra_headers=self._cache_extra_headers(cache_conversation_id),
                    ),
                    timeout=AGENT_TIMEOUT_S,
                )
                result["text"]          = response.choices[0].message.content or ""
                result["input_tokens"]  = response.usage.prompt_tokens
                result["cached_input_tokens"] = self._usage_cached_prompt_tokens(response.usage)
                result["output_tokens"] = response.usage.completion_tokens
                result["estimated_total_cost_usd"] = _safe_float(
                    _attr_or_key(response.usage, "cost_usd")
                )
                result["tool_invocation_count"] = _safe_int(
                    _attr_or_key(response.usage, "tool_invocation_count")
                )
                result["web_search_call_count"] = _safe_int(
                    _attr_or_key(response.usage, "web_search_call_count")
                )
                result["tool_cost_usd"] = _safe_float(
                    _attr_or_key(response.usage, "web_search_cost_usd")
                )
                result["elapsed_s"]     = round(time.time() - t0, 1)
                self._log(
                    f"[CheapGrokHeavy] Agent {agent_idx + 1} done "
                    f"({result['elapsed_s']}s, "
                    f"{result['output_tokens']} output tokens, "
                    f"{result['cached_input_tokens']} cached input tokens)"
                )
            except asyncio.TimeoutError:
                result["error"] = f"Timeout after {AGENT_TIMEOUT_S}s"
                result["elapsed_s"] = round(time.time() - t0, 1)
                self._log(
                    f"[CheapGrokHeavy] Agent {agent_idx + 1} TIMEOUT "
                    f"after {result['elapsed_s']}s"
                )
            except Exception as exc:
                result["error"] = str(exc)
                result["elapsed_s"] = round(time.time() - t0, 1)
                self._log(
                    f"[CheapGrokHeavy] Agent {agent_idx + 1} ERROR: {exc}"
                )
        return result

    # ------------------------------------------------------------------
    # Parallel ensemble
    # ------------------------------------------------------------------

    async def _run_agents(
        self,
        prompt: str,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> list:
        """Launch all agents concurrently (gated by semaphore). Returns list of result dicts."""
        tasks = [
            self._call_agent(
                prompt,
                spec,
                idx,
                shared_system_context=shared_system_context,
                cache_conversation_id=cache_conversation_id,
            )
            for idx, spec in enumerate(self.agent_specs)
        ]
        self._log(
            f"\n[CheapGrokHeavy] Launching {self.agent_count} agents "
            f"(model={self.model}, max_concurrent={self.max_concurrent}, "
            f"temps={[f'{t:.2f}' for t in self._temperatures()]})..."
        )
        t0      = time.time()
        results = await asyncio.gather(*tasks)
        elapsed = round(time.time() - t0, 1)
        success = sum(1 for r in results if r["error"] is None)
        self._log(
            f"[CheapGrokHeavy] All agents complete: "
            f"{success}/{self.agent_count} succeeded in {elapsed}s"
        )
        return list(results)

    # ------------------------------------------------------------------
    # Master synthesis
    # ------------------------------------------------------------------

    async def _synthesize(
        self,
        prompt: str,
        agent_results: list,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> str:
        """
        Master call in 'heavy mode': given all agent outputs, synthesize the best answer.

        The master is instructed to:
        1. Identify the STRONGEST answers by reasoning quality (not just consensus)
        2. Combine the best parts from all agents
        3. Resolve contradictions using its own judgment
        4. Produce ONE final polished answer
        """
        good = [r for r in agent_results if r["error"] is None and r["text"]]
        self._last_synthesis_usage = None
        if not good:
            return "[CheapGrokHeavy] All agents failed - no synthesis possible."

        if len(good) == 1:
            self._log("[CheapGrokHeavy] Only 1 agent succeeded - returning directly.")
            return good[0]["text"]

        agent_blocks = []
        for r in good:
            if r["temperature"] <= 0.15:
                label = "fully deterministic"
            elif r["temperature"] <= 0.40:
                label = "rigorous/low-variance"
            elif r["temperature"] <= 0.75:
                label = "balanced"
            elif r["temperature"] <= 1.05:
                label = "creative"
            else:
                label = "highly exploratory"
            prepared_text = self._prepare_agent_output_for_synthesis(r["text"])
            agent_blocks.append(
                f"--- AGENT {r['idx'] + 1}: {r['name']} (temp={r['temperature']:.2f}, "
                f"{label}) ---\n{prepared_text}"
            )

        synthesis_prompt = (
            "You are Grok in 'heavy mode'. Below are {n} independent answers to "
            "the same question, generated by yourself at different temperature "
            "settings. Lower temperatures (0.0) are fully deterministic; higher "
            "temperatures (1.5) are maximally creative and exploratory.\n\n"
            "ORIGINAL QUESTION:\n{original_q}\n\n"
            "YOUR {n} ANSWERS:\n\n{agents}\n\n"
            "YOUR TASK (heavy mode synthesis):\n"
            "1. Identify the STRONGEST answers - judge by reasoning quality and "
            "specificity, not just by how often an idea appeared.\n"
            "2. Combine the best parts from all agents - high-temp creative "
            "insights are worth including if they are valid, even if unique.\n"
            "3. Resolve any contradictions using your own best judgment.\n"
            "4. Produce ONE final polished answer that is better than any "
            "individual response.\n"
            "5. Do NOT mention agents, temperatures, or the synthesis process "
            "in your output - just give the final answer directly.\n\n"
            "IMPORTANT: Match the format requested in the original question "
            "(hypotheses, ranked lists, analysis, etc.)."
        ).format(
            n=len(good),
            original_q=prompt[:3000] + ("..." if len(prompt) > 3000 else ""),
            agents="\n\n".join(agent_blocks),
        )

        preferred_schema = self._preferred_synthesis_schema()
        if preferred_schema:
            synthesis_prompt += (
                "\n\nReturn strict JSON matching this schema:\n" +
                json.dumps(preferred_schema, indent=2, sort_keys=True)
            )

        self._log(
            f"\n[CheapGrokHeavy] Running master synthesis "
            f"({len(good)} agent responses, temp=0.1)..."
        )
        t0 = time.time()
        try:
            # Synthesis also goes through the semaphore for consistent rate control
            async with self._get_semaphore():
                messages = self._build_agent_messages(
                    synthesis_prompt,
                    {"name": "Synthesis", "system_prompt": ""},
                    shared_system_context=shared_system_context,
                )
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self.model,
                        temperature=0.1,                       # focused/consistent synthesis output
                        max_tokens=self.agent_max_tokens * 2,  # synthesis = 2x agent limit
                        messages=messages,
                        extra_headers=self._cache_extra_headers(cache_conversation_id),
                    ),
                    timeout=AGENT_TIMEOUT_S,
                )
            elapsed = round(time.time() - t0, 1)
            text    = response.choices[0].message.content or ""
            cached_input_tokens = self._usage_cached_prompt_tokens(response.usage)
            self._last_synthesis_usage = {
                "idx": "synthesis",
                "name": "Synthesis",
                "temperature": 0.1,
                "input_tokens": _safe_int(_attr_or_key(response.usage, "prompt_tokens")),
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": _safe_int(_attr_or_key(response.usage, "completion_tokens")),
                "estimated_total_cost_usd": _safe_float(_attr_or_key(response.usage, "cost_usd")),
                "tool_invocation_count": _safe_int(_attr_or_key(response.usage, "tool_invocation_count")),
                "web_search_call_count": _safe_int(_attr_or_key(response.usage, "web_search_call_count")),
                "tool_cost_usd": _safe_float(_attr_or_key(response.usage, "web_search_cost_usd")),
                "error": None,
            }
            self._log(
                f"[CheapGrokHeavy] Synthesis done ({elapsed}s, "
                f"{response.usage.completion_tokens} tokens, "
                f"{cached_input_tokens} cached input tokens)"
            )
            return text
        except Exception as exc:
            self._log(f"[CheapGrokHeavy] Synthesis ERROR: {exc} - returning best agent")
            # Fallback: return the longest successful agent response
            return max(good, key=lambda r: len(r["text"] or "")).get("text", "")

    # ------------------------------------------------------------------
    # Cost summary
    # ------------------------------------------------------------------

    def _usage_summary(
        self,
        agent_results: list,
        synthesis_tokens: int = 0,
        elapsed_s: float = 0.0,
    ) -> dict:
        total_in  = sum(r["input_tokens"]  for r in agent_results)
        cached_in = sum(r.get("cached_input_tokens", 0) for r in agent_results)
        total_out = sum(r["output_tokens"] for r in agent_results)
        provider_cost = sum(float(r.get("estimated_total_cost_usd") or 0.0) for r in agent_results)
        tool_invocations = sum(int(r.get("tool_invocation_count") or 0) for r in agent_results)
        web_search_calls = sum(int(r.get("web_search_call_count") or 0) for r in agent_results)
        tool_cost = sum(float(r.get("tool_cost_usd") or 0.0) for r in agent_results)
        total_in  += synthesis_tokens  # rough estimate for synthesis input
        estimated_token_cost = (
            total_in  / 1_000_000 * COST_INPUT_PER_M +
            total_out / 1_000_000 * COST_OUTPUT_PER_M
        )
        estimated_total_cost = estimated_token_cost + tool_cost
        grand_total_cost = max(provider_cost, estimated_total_cost)
        agent_rows = [r for r in agent_results if isinstance(r.get("idx"), int)]
        failed = sum(1 for r in agent_rows if r.get("error"))
        return {
            "model": self.model,
            "api_backend": self.api_backend,
            "agent_count": self.agent_count,
            "request_count": len(agent_results),
            "success_count": len(agent_rows) - failed,
            "failed_count": failed,
            "elapsed_s": elapsed_s,
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_cached_input_tokens": cached_in,
            "total_tool_invocation_count": tool_invocations,
            "total_web_search_call_count": web_search_calls,
            "total_tool_cost_usd": round(tool_cost, 6),
            "provider_reported_cost_usd": round(provider_cost, 6),
            "estimated_token_cost_usd": round(estimated_token_cost, 6),
            "estimated_total_cost_usd": round(estimated_total_cost, 6),
            "grand_total_cost_usd": round(grand_total_cost, 6),
            "cost_basis": "actual" if provider_cost >= estimated_total_cost and provider_cost else "estimated",
        }

    def _print_cost(
        self,
        agent_results: list,
        synthesis_tokens: int = 0,
        elapsed_s: float = 0.0,
    ) -> dict:
        summary = self._usage_summary(agent_results, synthesis_tokens, elapsed_s=elapsed_s)
        if not self.verbose:
            return summary
        print(
            f"\n[CheapGrokHeavy] Token usage: "
            f"{summary['total_input_tokens']:,} input / {summary['total_output_tokens']:,} output | "
            f"{summary['total_cached_input_tokens']:,} cached input | "
            f"{summary['total_tool_invocation_count']:,} tools (${summary['total_tool_cost_usd']:.4f}) | "
            f"{summary['cost_basis']} ${summary['grand_total_cost_usd']:.4f} | "
            f"{summary['success_count']}/{summary['agent_count']} agents succeeded",
            flush=True,
        )
        return summary

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def call_async(
        self,
        prompt: str,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> str:
        """
        Async entry point.  Run ensemble and return synthesized result.

        Args:
            prompt: the full prompt to send to all agents

        Returns:
            Synthesized text from master call.
        """
        t_start       = time.time()
        agent_results = await self._run_agents(
            prompt,
            shared_system_context=shared_system_context,
            cache_conversation_id=cache_conversation_id,
        )
        synthesis     = await self._synthesize(
            prompt,
            agent_results,
            shared_system_context=shared_system_context,
            cache_conversation_id=cache_conversation_id,
        )
        elapsed_total = round(time.time() - t_start, 1)

        # Estimate synthesis input tokens (rough: len(agent texts) / 4)
        synth_in_est = sum(len(r["text"] or "") for r in agent_results) // 4

        usage_rows = list(agent_results)
        if self._last_synthesis_usage:
            usage_rows.append(self._last_synthesis_usage)
            synth_in_est = 0
        self.last_usage = self._print_cost(usage_rows, synth_in_est, elapsed_s=elapsed_total)
        self._log(
            f"[CheapGrokHeavy] Total elapsed: {elapsed_total}s "
            f"({self.agent_count} agents + synthesis)"
        )
        return synthesis

    def _build_agent_user_prompt(
        self,
        task_prompt: str,
        spec: dict,
        context_sections: dict | None = None,
    ) -> str:
        """Build a user prompt for one configured agent."""
        sections = []
        context_sections = context_sections or {}
        input_keys = spec.get("input_sections", [])

        if input_keys:
            for key in input_keys:
                if key in context_sections:
                    sections.append(f"[{key}]\n{context_sections[key]}")
        else:
            for key, value in context_sections.items():
                sections.append(f"[{key}]\n{value}")

        prompt_parts = [task_prompt.strip()]
        if sections:
            prompt_parts.append("Relevant context sections:\n\n" + "\n\n".join(sections))

        schema = spec.get("output_schema") or {}
        if schema:
            prompt_parts.append(
                "Return strict JSON matching this schema:\n" +
                json.dumps(schema, indent=2, sort_keys=True)
            )

        return "\n\n".join(part for part in prompt_parts if part)

    async def call_async_with_context(
        self,
        task_prompt: str,
        context_sections: dict | None = None,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> str:
        """Run the ensemble using per-agent context selection from configured specs."""
        agent_prompts = [
            self._build_agent_user_prompt(task_prompt, spec, context_sections)
            for spec in self.agent_specs
        ]
        t_start = time.time()
        tasks = [
            self._call_agent(
                prompt,
                spec,
                idx,
                shared_system_context=shared_system_context,
                cache_conversation_id=cache_conversation_id,
            )
            for idx, (prompt, spec) in enumerate(zip(agent_prompts, self.agent_specs))
        ]
        self._log(
            f"\n[CheapGrokHeavy] Launching {self.agent_count} configured agents "
            f"(model={self.model}, max_concurrent={self.max_concurrent})..."
        )
        agent_results = await asyncio.gather(*tasks)
        synthesis_prompt = self._build_agent_user_prompt(
            task_prompt,
            {
                "name": "Synthesis",
                "output_schema": {},
            },
            context_sections,
        )
        synthesis = await self._synthesize(
            synthesis_prompt,
            agent_results,
            shared_system_context=shared_system_context,
            cache_conversation_id=cache_conversation_id,
        )
        elapsed_total = round(time.time() - t_start, 1)
        synth_in_est = sum(len(r["text"] or "") for r in agent_results) // 4
        usage_rows = list(agent_results)
        if self._last_synthesis_usage:
            usage_rows.append(self._last_synthesis_usage)
            synth_in_est = 0
        self.last_usage = self._print_cost(usage_rows, synth_in_est, elapsed_s=elapsed_total)
        self._log(
            f"[CheapGrokHeavy] Total elapsed: {elapsed_total}s "
            f"({self.agent_count} agents + synthesis)"
        )
        return synthesis

    async def call_async_with_files(
        self,
        task_prompt: str,
        context_sections: dict | None = None,
        context_files: dict[str, str | pathlib.Path] | None = None,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> str:
        """Run the ensemble after loading named files into context sections."""
        merged_context = self._merge_context_files(context_sections, context_files)
        return await self.call_async_with_context(
            task_prompt,
            merged_context,
            shared_system_context=shared_system_context,
            cache_conversation_id=cache_conversation_id,
        )

    def call(
        self,
        prompt: str,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> str:
        """
        Synchronous entry point.  Wraps call_async with asyncio.run().

        Use this from non-async scripts (e.g. run_hypothesis_generator.py).
        Use call_async() from within async contexts.
        """
        return asyncio.run(
            self.call_async(
                prompt,
                shared_system_context=shared_system_context,
                cache_conversation_id=cache_conversation_id,
            )
        )

    def call_with_context(
        self,
        task_prompt: str,
        context_sections: dict | None = None,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> str:
        """Synchronous wrapper for config-driven context-aware execution."""
        return asyncio.run(
            self.call_async_with_context(
                task_prompt,
                context_sections,
                shared_system_context=shared_system_context,
                cache_conversation_id=cache_conversation_id,
            )
        )

    def call_with_files(
        self,
        task_prompt: str,
        context_sections: dict | None = None,
        context_files: dict[str, str | pathlib.Path] | None = None,
        *,
        shared_system_context: str | None = None,
        cache_conversation_id: str | None = None,
    ) -> str:
        """Synchronous wrapper for file-backed context-aware execution."""
        return asyncio.run(
            self.call_async_with_files(
                task_prompt,
                context_sections,
                context_files,
                shared_system_context=shared_system_context,
                cache_conversation_id=cache_conversation_id,
            )
        )


# ---------------------------------------------------------------------------
# Module-level helper (drop-in replacement for single Grok call)
# ---------------------------------------------------------------------------

def cheap_grok_heavy_call(
    prompt: str,
    api_key: str | None = None,
    agent_count: int = DEFAULT_AGENTS,
    agent_specs: list[dict] | None = None,
    agent_specs_path: str | None = None,
    agent_preset: str | None = None,
    model: str = DEFAULT_MODEL,
    agent_max_tokens: int = 2048,
    max_concurrent: int | None = None,
    shared_system_context: str | None = None,
    cache_conversation_id: str | None = None,
    verbose: bool = True,
    api_backend: str = "xai_sdk",
) -> str:
    """
    Convenience function.  One-liner replacement for a single Grok call.

    Example:
        from agent.utils.cheap_grok_heavy import cheap_grok_heavy_call
        result = cheap_grok_heavy_call(prompt, agent_count=4, agent_max_tokens=4096)
        result = cheap_grok_heavy_call(prompt, max_concurrent=4)  # conservative rate limiting
    """
    client = CheapGrokHeavy(
        api_key=api_key,
        agent_count=agent_count,
        agent_specs=agent_specs,
        agent_specs_path=agent_specs_path,
        agent_preset=agent_preset,
        model=model,
        agent_max_tokens=agent_max_tokens,
        max_concurrent=max_concurrent,
        verbose=verbose,
        api_backend=api_backend,
    )
    return client.call(
        prompt,
        shared_system_context=shared_system_context,
        cache_conversation_id=cache_conversation_id,
    )


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CheapGrokHeavy self-test")
    parser.add_argument("--agents",          type=int, default=DEFAULT_AGENTS,
                        help=f"number of agents (default {DEFAULT_AGENTS})")
    parser.add_argument("--max-concurrent",  type=int, default=None,
                        help="max concurrent API calls (default min(6, agents))")
    parser.add_argument("--model",           default=DEFAULT_MODEL,
                        help="model name")
    parser.add_argument("--prompt",          default=None,
                        help="custom prompt (default: short test prompt)")
    parser.add_argument("--output",          default=None,
                        help="write result to file instead of stdout")
    parser.add_argument("--agent-specs-path", default=None,
                        help="optional JSON agent spec config")
    parser.add_argument("--agent-preset", default=None,
                        help="named preset from --agent-specs-path")
    parser.add_argument("--context-file", action="append", default=[],
                        help="named context file as name=path; repeatable")
    parser.add_argument("--api-backend", choices=["xai_sdk", "openai_compat"], default="xai_sdk",
                        help="API backend (default: xai_sdk for provider-reported costs)")
    args = parser.parse_args()

    test_prompt = args.prompt or (
        "List 3 concrete ways a momentum swing trading strategy "
        "can reduce its false-positive entry rate in choppy/sideways markets. "
        "Be specific about indicators, thresholds, and mechanisms."
    )

    # Get API key
    try:
        _dir = pathlib.Path(__file__).parent.parent
        sys.path.insert(0, str(_dir.parent))
        from agent.utils.credentials_manager import get_credentials_manager
        _key = get_credentials_manager().get_xai_key()
    except Exception:
        _key = os.environ.get("XAI_API_KEY", "")

    if not _key:
        print("ERROR: XAI_API_KEY not found. Set env var or configure credentials_manager.")
        sys.exit(1)

    client = CheapGrokHeavy(
        api_key=_key,
        agent_count=args.agents,
        max_concurrent=args.max_concurrent,
        model=args.model,
        agent_specs_path=args.agent_specs_path,
        agent_preset=args.agent_preset,
        api_backend=args.api_backend,
    )
    print(format_cli_run_header(client))
    print(f"Temps: {[spec['temperature'] for spec in client.agent_specs]}")
    print(f"Prompt: {test_prompt[:100]}...\n")
    context_files = parse_context_file_args(args.context_file)
    if context_files:
        result = client.call_with_files(test_prompt, context_files=context_files)
    else:
        result = client.call(test_prompt)

    if args.output:
        pathlib.Path(args.output).write_text(result, encoding="utf-8")
        print(f"\n[self-test] Result written to {args.output}")
    else:
        print("\n=== RESULT ===")
        print(result)
