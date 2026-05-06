"""
cheap_grok_heavy.py
====================
Self-Mixture-of-Agents (Self-MoA) ensemble using the xAI OpenAI-compatible
REST API. Runs N parallel grok-4.3 calls with temperature
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
  - Uses openai.AsyncOpenAI with base_url="https://api.x.ai/v1" (same as grok_client.py)
  - xai_sdk does NOT expose temperature - OpenAI-compatible REST API does (0.0 to 2.0)
  - agent_count=4 default: cost-controlled council for pricier Grok 4.3 calls
  - max_concurrent=4 default: asyncio.Semaphore caps burst to prevent 429s
  - AsyncOpenAI client created once in __init__ and reused (not recreated per call)
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XAI_BASE_URL      = "https://api.x.ai/v1"
DEFAULT_MODEL     = "grok-4.3"
DEFAULT_AGENTS    = 4            # cost-controlled default after 4.1 fast deprecation
AGENT_TIMEOUT_S   = 180          # per-agent hard timeout
COST_INPUT_PER_M  = 1.25        # $ per 1M input tokens
COST_OUTPUT_PER_M = 2.50        # $ per 1M output tokens

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

    Uses the xAI OpenAI-compatible REST API (same pattern as grok_client.py).
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
    ):
        import openai

        self.api_key          = api_key or os.environ.get("XAI_API_KEY", "")
        self.model            = model
        self.agent_max_tokens = agent_max_tokens
        self.verbose          = verbose

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

        # Single shared client - AsyncOpenAI does not connect at construction,
        # so this is safe to create in __init__ and reuse across all calls.
        self._client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=XAI_BASE_URL,
        )

        # Semaphore initialized lazily in the first async context to ensure
        # it binds to the correct event loop (Python < 3.10 compatibility).
        self._semaphore: asyncio.Semaphore | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
            "output_tokens": 0,
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
                messages = []
                if spec.get("system_prompt"):
                    messages.append({"role": "system", "content": spec["system_prompt"]})
                messages.append({"role": "user", "content": prompt})
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self.model,
                        temperature=spec["temperature"],
                        max_tokens=self.agent_max_tokens,
                        messages=messages,
                    ),
                    timeout=AGENT_TIMEOUT_S,
                )
                result["text"]          = response.choices[0].message.content or ""
                result["input_tokens"]  = response.usage.prompt_tokens
                result["output_tokens"] = response.usage.completion_tokens
                result["elapsed_s"]     = round(time.time() - t0, 1)
                self._log(
                    f"[CheapGrokHeavy] Agent {agent_idx + 1} done "
                    f"({result['elapsed_s']}s, "
                    f"{result['output_tokens']} output tokens)"
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

    async def _run_agents(self, prompt: str) -> list:
        """Launch all agents concurrently (gated by semaphore). Returns list of result dicts."""
        tasks = [
            self._call_agent(prompt, spec, idx)
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

    async def _synthesize(self, prompt: str, agent_results: list) -> str:
        """
        Master call in 'heavy mode': given all agent outputs, synthesize the best answer.

        The master is instructed to:
        1. Identify the STRONGEST answers by reasoning quality (not just consensus)
        2. Combine the best parts from all agents
        3. Resolve contradictions using its own judgment
        4. Produce ONE final polished answer
        """
        good = [r for r in agent_results if r["error"] is None and r["text"]]
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
            agent_blocks.append(
                f"--- AGENT {r['idx'] + 1}: {r['name']} (temp={r['temperature']:.2f}, "
                f"{label}) ---\n{r['text']}"
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
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self.model,
                        temperature=0.1,                       # focused/consistent synthesis output
                        max_tokens=self.agent_max_tokens * 2,  # synthesis = 2x agent limit
                        messages=[{"role": "user", "content": synthesis_prompt}],
                    ),
                    timeout=AGENT_TIMEOUT_S,
                )
            elapsed = round(time.time() - t0, 1)
            text    = response.choices[0].message.content or ""
            self._log(
                f"[CheapGrokHeavy] Synthesis done ({elapsed}s, "
                f"{response.usage.completion_tokens} tokens)"
            )
            return text
        except Exception as exc:
            self._log(f"[CheapGrokHeavy] Synthesis ERROR: {exc} - returning best agent")
            # Fallback: return the longest successful agent response
            return max(good, key=lambda r: len(r["text"] or "")).get("text", "")

    # ------------------------------------------------------------------
    # Cost summary
    # ------------------------------------------------------------------

    def _print_cost(self, agent_results: list, synthesis_tokens: int = 0) -> None:
        if not self.verbose:
            return
        total_in  = sum(r["input_tokens"]  for r in agent_results)
        total_out = sum(r["output_tokens"] for r in agent_results)
        total_in  += synthesis_tokens  # rough estimate for synthesis input
        cost = (
            total_in  / 1_000_000 * COST_INPUT_PER_M +
            total_out / 1_000_000 * COST_OUTPUT_PER_M
        )
        failed = sum(1 for r in agent_results if r["error"])
        print(
            f"\n[CheapGrokHeavy] Token usage: "
            f"{total_in:,} input / {total_out:,} output | "
            f"~${cost:.4f} | "
            f"{self.agent_count - failed}/{self.agent_count} agents succeeded",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def call_async(self, prompt: str) -> str:
        """
        Async entry point.  Run ensemble and return synthesized result.

        Args:
            prompt: the full prompt to send to all agents

        Returns:
            Synthesized text from master call.
        """
        t_start       = time.time()
        agent_results = await self._run_agents(prompt)
        synthesis     = await self._synthesize(prompt, agent_results)
        elapsed_total = round(time.time() - t_start, 1)

        # Estimate synthesis input tokens (rough: len(agent texts) / 4)
        synth_in_est = sum(len(r["text"] or "") for r in agent_results) // 4

        self._print_cost(agent_results, synth_in_est)
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
    ) -> str:
        """Run the ensemble using per-agent context selection from configured specs."""
        agent_prompts = [
            self._build_agent_user_prompt(task_prompt, spec, context_sections)
            for spec in self.agent_specs
        ]
        t_start = time.time()
        tasks = [
            self._call_agent(prompt, spec, idx)
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
        synthesis = await self._synthesize(synthesis_prompt, agent_results)
        elapsed_total = round(time.time() - t_start, 1)
        synth_in_est = sum(len(r["text"] or "") for r in agent_results) // 4
        self._print_cost(agent_results, synth_in_est)
        self._log(
            f"[CheapGrokHeavy] Total elapsed: {elapsed_total}s "
            f"({self.agent_count} agents + synthesis)"
        )
        return synthesis

    def call(self, prompt: str) -> str:
        """
        Synchronous entry point.  Wraps call_async with asyncio.run().

        Use this from non-async scripts (e.g. run_hypothesis_generator.py).
        Use call_async() from within async contexts.
        """
        return asyncio.run(self.call_async(prompt))

    def call_with_context(
        self,
        task_prompt: str,
        context_sections: dict | None = None,
    ) -> str:
        """Synchronous wrapper for config-driven context-aware execution."""
        return asyncio.run(self.call_async_with_context(task_prompt, context_sections))


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
    verbose: bool = True,
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
    )
    return client.call(prompt)


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

    _max_c = args.max_concurrent or min(4, args.agents)
    print(f"CheapGrokHeavy self-test: {args.agents} agents, max_concurrent={_max_c}, model={args.model}")
    print(f"Temps: {_select_temps(args.agents)}")
    print(f"Prompt: {test_prompt[:100]}...\n")

    result = cheap_grok_heavy_call(
        test_prompt,
        api_key=_key,
        agent_count=args.agents,
        max_concurrent=args.max_concurrent,
        model=args.model,
    )

    if args.output:
        pathlib.Path(args.output).write_text(result, encoding="utf-8")
        print(f"\n[self-test] Result written to {args.output}")
    else:
        print("\n=== RESULT ===")
        print(result)
