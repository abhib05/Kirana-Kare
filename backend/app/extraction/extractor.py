"""Turn -> ExtractedTurn. Two interchangeable backends behind one interface:

- `LLMExtractor`: Meta Model API (OpenAI-compatible) with JSON-schema structured output.
- `RuleExtractor`: deterministic keyword/number parser (rules.py). Used in CI, in the eval
  harness as a baseline, and for demos without an API key.

Both return output that has already passed Pydantic parsing and `check_business_rules`.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings, get_settings
from app.extraction.catalog_match import CatalogEntry
from app.extraction.rules import extract_rules
from app.extraction.schemas import (
    ExtractedTurn,
    ExtractionRejected,
    Turn,
    check_business_rules,
    parse_llm_output,
)

log = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
PROMPT_DIR = Path(__file__).parent / "prompts"
CATALOG_LIMIT = 60


class Extractor(Protocol):
    prompt_version: str

    def extract(self, turn: Turn, context: list[Turn],
                catalog: list[CatalogEntry]) -> ExtractedTurn: ...


@lru_cache
def load_prompt(version: str = PROMPT_VERSION) -> str:
    return (PROMPT_DIR / f"{version}.txt").read_text(encoding="utf-8")


def _catalog_text(catalog: list[CatalogEntry]) -> str:
    return "\n".join(f"- {p.name}: {', '.join(p.aliases)}" for p in catalog[:CATALOG_LIMIT])


def _context_text(turn: Turn, context: list[Turn]) -> str:
    lines = [f"[{t.speaker or '?'}] {t.text}" for t in context[-6:]]
    prev = "\n".join(lines) if lines else "(none)"
    return f"Previous turns:\n{prev}\n\nCurrent turn:\n[{turn.speaker or '?'}] {turn.text}"


class LLMClient(Protocol):
    """The slice of the OpenAI SDK we use; lets tests inject a fake."""

    @property
    def chat(self) -> Any: ...


class LLMExtractor:
    def __init__(self, client: LLMClient, model: str, prompt_version: str = PROMPT_VERSION):
        self.client = client
        self.model = model
        self.prompt_version = prompt_version

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMExtractor:
        from openai import OpenAI

        client = OpenAI(api_key=settings.model_api_key, base_url=settings.llm_base_url,
                        timeout=15.0, max_retries=1)
        return cls(client, settings.llm_model)

    def extract(self, turn: Turn, context: list[Turn],
                catalog: list[CatalogEntry]) -> ExtractedTurn:
        system = load_prompt(self.prompt_version).replace("{catalog}", _catalog_text(catalog))
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": _context_text(turn, context)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "ExtractedTurn",
                                    "schema": ExtractedTurn.model_json_schema()},
                },
            )
            content = resp.choices[0].message.content or ""
        except Exception as exc:  # network / API errors: reject, never guess
            raise ExtractionRejected(f"LLM call failed: {type(exc).__name__}") from exc
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ExtractionRejected("LLM returned non-JSON output", content) from exc
        result = parse_llm_output(data)
        check_business_rules(result)
        return result


class RuleExtractor:
    prompt_version = "rules-v1"

    def extract(self, turn: Turn, context: list[Turn],
                catalog: list[CatalogEntry]) -> ExtractedTurn:
        result = extract_rules(turn.text, catalog)
        check_business_rules(result)
        return result


def get_extractor(settings: Settings | None = None) -> Extractor:
    s = settings or get_settings()
    if s.extractor_backend == "llm" and s.model_api_key:
        return LLMExtractor.from_settings(s)
    if s.extractor_backend == "llm":
        log.warning("EXTRACTOR_BACKEND=llm but MODEL_API_KEY is empty; using rule extractor")
    return RuleExtractor()
