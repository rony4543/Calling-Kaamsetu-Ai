"""
Agent base class.

Every agent shares two things: a Jinja2 environment pointed at app/prompts, and
a handle to the OpenAI client. Agents render a prompt template, call the LLM,
and return either text or structured JSON. They never touch Supabase directly —
they go through the memory/ layer and repositories.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.integrations.openai_client import get_openai_client

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    autoescape=False,        # we render prompts, not HTML
    trim_blocks=True,
    lstrip_blocks=True,
)


class BaseAgent:
    """Common helpers for all agents."""

    name: str = "base"

    @property
    def llm(self):
        return get_openai_client()

    def render(self, template: str, **ctx) -> str:
        """Render a Jinja2 prompt template from app/prompts (e.g. 'matchmaker.j2')."""
        return _env.get_template(template).render(**ctx)
