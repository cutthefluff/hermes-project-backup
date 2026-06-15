"""Request-level model self-selection — the "model router".

When ``model_router.enabled`` is set in config, Hermes classifies each incoming
user request with a cheap auxiliary LLM (by default a free OpenRouter model) and
picks which model to run *itself* on for that turn:

  * ROUTE   -> a light tier: Hermes stays the lightweight middleman and delegates
               the actual work to a CLI / subagent.
  * COMPLEX -> a strong tier (e.g. the Codex API): Hermes does the deep reasoning
               itself.

Config (config.yaml)::

    model_router:
      enabled: true
      classifier:
        provider: openrouter
        model: "openrouter/auto"   # free routing; or a "<model>:free" slug
        timeout: 12
      default_tier: light          # used if the classifier errors / abstains
      tiers:
        light: ""                  # "" = keep / restore the current (base) model
        complex: "gpt-5-codex --provider openai-codex"
        # middle: "deepseek-... --provider ..."   # future cheaper tier

Each tier value is a ``/model``-style string (alias + optional ``--provider``).
An empty value means "no switch for this tier" — stay on (or restore to) the
base model. The actual model swap + restore is performed by
``agent.skill_model_override.apply_model_for_turn`` so the router and per-skill
overrides share one restore slot and never fight each other.

The classifier sees only the user's message (not history), runs at temperature 0
with a tiny token budget, and fails safe to ``default_tier`` on any error.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SYS_PROMPT = (
    "You are a fast request router for an AI agent named Hermes. Hermes can either "
    "(a) act as a lightweight orchestrator that delegates the work to command-line "
    "coding agents / subagents, or (b) do deep reasoning and work itself on a more "
    "capable model.\n"
    "Classify the user's request:\n"
    "- Reply COMPLEX if it needs Hermes's own careful multi-step reasoning, analysis, "
    "planning, design, debugging, or writing that the user clearly wants Hermes itself "
    "to perform.\n"
    "- Reply ROUTE for everything else: simple questions, quick lookups, chit-chat, or "
    "tasks that should be handed to a CLI/tool with Hermes as the middleman.\n"
    "Answer with exactly one word: ROUTE or COMPLEX."
)


def _cfg() -> dict:
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        mr = cfg.get("model_router")
        return mr if isinstance(mr, dict) else {}
    except Exception:
        logger.debug("model_router config load failed", exc_info=True)
        return {}


def is_enabled() -> bool:
    return bool(_cfg().get("enabled", False))


def _classify(user_message: str, cfg: dict) -> str:
    """Return ``'light'`` or ``'complex'``; fall back to ``default_tier``."""
    default_tier = str(cfg.get("default_tier") or "light").strip().lower()
    if not user_message or not user_message.strip():
        return default_tier
    cls = cfg.get("classifier") or {}
    try:
        from agent.auxiliary_client import call_llm
        resp = call_llm(
            provider=str(cls.get("provider") or "openrouter"),
            model=str(cls.get("model") or "openrouter/auto"),
            messages=[
                {"role": "system", "content": _SYS_PROMPT},
                {"role": "user", "content": user_message[:4000]},
            ],
            temperature=0,
            max_tokens=4,
            timeout=float(cls.get("timeout") or 12),
        )
        text = (resp.choices[0].message.content or "").strip().upper()
    except Exception:
        logger.debug("model router classification failed; using default tier",
                     exc_info=True)
        return default_tier
    if "COMPLEX" in text:
        return "complex"
    if "ROUTE" in text:
        return "light"
    return default_tier


def route_model_for_turn(agent, user_message: str) -> Optional[str]:
    """Return a ``/model``-style switch string for this turn, or ``None``.

    ``None`` means the router directs no switch (disabled, or the chosen tier
    maps to an empty value); the caller then restores the base model if a prior
    switch is pending.
    """
    cfg = _cfg()
    if not cfg.get("enabled", False):
        return None
    tiers = cfg.get("tiers") or {}
    tier = _classify(user_message, cfg)
    switch_string = tiers.get(tier)
    if isinstance(switch_string, str) and switch_string.strip():
        return switch_string.strip()
    return None
