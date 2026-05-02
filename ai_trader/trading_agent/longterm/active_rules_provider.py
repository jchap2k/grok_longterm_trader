"""Active long-term rules context for committee prompts."""

from __future__ import annotations

from pathlib import Path


DEFAULT_ACTIVE_RULES_PATH = Path(__file__).resolve().parents[3] / "rules" / "active_rules.txt"


class ActiveRulesProvider:
    """Load the current long-term trading rules for LLM decision context."""

    def __init__(self, rules_path: str | Path | None = None):
        self.rules_path = Path(rules_path) if rules_path else DEFAULT_ACTIVE_RULES_PATH

    def load(self) -> str:
        if not self.rules_path.exists():
            return f"Active rules file not found: {self.rules_path}"
        text = self.rules_path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return f"Active rules file is empty: {self.rules_path}"
        return text


__all__ = ["ActiveRulesProvider", "DEFAULT_ACTIVE_RULES_PATH"]
