"""Per-skill model overrides — auto-upgrade the model when a skill is
activated, then restore the previous model on the next turn.

Driven entirely by config.yaml (no per-skill frontmatter)::

    skills:
      model_overrides:
        red-teaming: "opus"
        research-paper-writing: "opus --provider anthropic"

The value is exactly what a user would type after ``/model`` — a model alias
plus an optional ``--provider`` flag. Resolution reuses
``hermes_cli.model_switch`` (the same pipeline ``/model`` uses) and the live
in-place swap reuses ``agent.switch_model`` (``run_agent.py`` ->
``agent.agent_runtime_helpers.switch_model``), which persists across turns and
rolls back atomically on failure.

Single integration point: :func:`on_turn_start` is called at the very top of
``agent.conversation_loop.run_conversation`` (before ``build_turn_context``),
so it covers every gateway — CLI, gateway, tui_gateway, and webhooks all funnel
their turns through ``run_conversation``. A skill activation is detected from
the activation note that ``agent.skill_commands.build_skill_invocation_message``
embeds in the user message.

Behaviour:
  * Turn that activates a skill with an override -> switch to that model,
    snapshotting the *current* model so it can be restored.
  * Any later turn that does NOT activate an override-carrying skill ->
    restore the snapshot first, so normal turns run on the base model again.

Best-effort throughout: a resolution/credential failure logs a warning and the
turn proceeds on the current model. A skill is never blocked by an override.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Matches the activation note emitted by
# agent.skill_commands.build_skill_invocation_message():
#   [IMPORTANT: The user has invoked the "<skill>" skill, ...]
_SKILL_ACTIVATION_RE = re.compile(r'invoked the "([^"]+)" skill', re.IGNORECASE)

# Attribute on the agent that holds the snapshot to restore to (or None).
_RESTORE_ATTR = "_skill_model_restore"

NoticeFn = Optional[Callable[[str], None]]


# ---------------------------------------------------------------------------
# Config + detection
# ---------------------------------------------------------------------------

def _get_overrides() -> dict:
    """Return the ``skills.model_overrides`` map from config (best-effort)."""
    try:
        from agent.skill_preprocessing import load_skills_config
        cfg = load_skills_config() or {}
        overrides = cfg.get("model_overrides")
        return overrides if isinstance(overrides, dict) else {}
    except Exception:
        logger.debug("skill model_overrides load failed", exc_info=True)
        return {}


def detect_activated_skill(user_message: str) -> Optional[str]:
    """Return the skill name a turn activates, or None.

    Reads the activation marker embedded by
    ``build_skill_invocation_message``; works regardless of which gateway
    queued the message.
    """
    if not user_message:
        return None
    match = _SKILL_ACTIVATION_RE.search(user_message)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Snapshot + apply helpers
# ---------------------------------------------------------------------------

def _snapshot(agent: Any) -> dict:
    return {
        "model": getattr(agent, "model", "") or "",
        "provider": getattr(agent, "provider", "") or "",
        "api_key": getattr(agent, "api_key", "") or "",
        "base_url": getattr(agent, "base_url", "") or "",
        "api_mode": getattr(agent, "api_mode", "") or "",
    }


def _apply(agent: Any, model: str, provider: str, api_key: str,
           base_url: str, api_mode: str) -> None:
    agent.switch_model(
        new_model=model,
        new_provider=provider,
        api_key=api_key or "",
        base_url=base_url or "",
        api_mode=api_mode or "",
    )


def _resolve(agent: Any, override: str):
    """Resolve an override string into a ModelSwitchResult via the shared
    ``/model`` pipeline. Returns None on failure."""
    try:
        from hermes_cli.model_switch import parse_model_flags, switch_model
    except Exception:
        logger.debug("model_switch import failed", exc_info=True)
        return None

    model_input, explicit_provider, _is_global, _refresh = parse_model_flags(override)

    user_providers = None
    custom_providers = None
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        user_providers = cfg.get("providers")
        custom_providers = cfg.get("custom_providers")
    except Exception:
        pass

    try:
        return switch_model(
            model_input,
            current_provider=getattr(agent, "provider", "") or "",
            current_model=getattr(agent, "model", "") or "",
            current_base_url=getattr(agent, "base_url", "") or "",
            current_api_key=getattr(agent, "api_key", "") or "",
            explicit_provider=explicit_provider,
            user_providers=user_providers,
            custom_providers=custom_providers,
        )
    except Exception:
        logger.warning("skill model override resolve raised", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_model_for_turn(agent: Any, switch_string: str, label: str,
                         notice_fn: NoticeFn = None) -> bool:
    """Snapshot the current model (once) and switch to *switch_string*.

    Generic core shared by per-skill overrides and the request-level model
    router (``agent.model_router``). *switch_string* is a ``/model``-style
    string (alias + optional ``--provider``). The snapshot is taken only when
    no restore is already pending, so back-to-back override/router turns still
    restore to the true base. Returns True iff a switch was applied. Best-effort.
    """
    if not isinstance(switch_string, str) or not switch_string.strip():
        return False

    result = _resolve(agent, switch_string.strip())
    if result is None or not getattr(result, "success", False):
        msg = getattr(result, "error_message", "") if result is not None else "unresolved"
        logger.warning("model switch for %s ('%s') not applied: %s",
                       label, switch_string, msg)
        return False

    # Already on the requested model+provider — nothing to do.
    if (result.new_model == (getattr(agent, "model", "") or "")
            and result.target_provider == (getattr(agent, "provider", "") or "")):
        return False

    if not getattr(agent, _RESTORE_ATTR, None):
        try:
            setattr(agent, _RESTORE_ATTR, _snapshot(agent))
        except Exception:
            logger.debug("could not snapshot model for restore", exc_info=True)

    try:
        _apply(agent, result.new_model, result.target_provider,
               result.api_key, result.base_url, result.api_mode)
    except Exception:
        logger.warning("model switch apply failed for %s", label, exc_info=True)
        return False

    if notice_fn:
        provider_label = result.provider_label or result.target_provider
        try:
            notice_fn(f"  ⚡ {label} → {result.new_model} ({provider_label})")
        except Exception:
            pass
    return True


def apply_skill_model_override(agent: Any, skill_name: str,
                               notice_fn: NoticeFn = None) -> bool:
    """Switch to the model configured for *skill_name* under
    ``skills.model_overrides``, if any. Thin wrapper over
    :func:`apply_model_for_turn`."""
    override = _get_overrides().get(skill_name)
    if not isinstance(override, str) or not override.strip():
        return False
    return apply_model_for_turn(agent, override, f"skill '{skill_name}'",
                                notice_fn=notice_fn)


def restore_skill_model(agent: Any, notice_fn: NoticeFn = None) -> bool:
    """Restore the model snapshot taken by a prior override, if pending."""
    snap = getattr(agent, _RESTORE_ATTR, None)
    if not snap:
        return False
    try:
        _apply(agent, snap["model"], snap["provider"], snap["api_key"],
               snap["base_url"], snap["api_mode"])
        if notice_fn:
            try:
                notice_fn(f"  ↩ skill model override cleared — back to {snap['model']}")
            except Exception:
                pass
        return True
    except Exception:
        logger.warning("skill model restore failed", exc_info=True)
        return False
    finally:
        try:
            setattr(agent, _RESTORE_ATTR, None)
        except Exception:
            pass


def on_turn_start(agent: Any, user_message: str, notice_fn: NoticeFn = None) -> None:
    """Single per-turn model-selection hook for ``run_conversation``.

    Precedence per turn:
      1. Explicit per-skill override (``skills.model_overrides``) when the turn
         activates a configured skill.
      2. The request-level model router (``model_router.enabled``) — classifies
         the request and picks Hermes's own model tier.
      3. Otherwise restore the base model if a prior switch is pending.

    Fully exception-isolated — must never break a turn. Subagents (delegated
    children) keep the model the parent chose and are never re-routed.
    """
    try:
        # Subagents run on the model the parent assigned — don't re-route them.
        if getattr(agent, "_subagent_id", None):
            return

        # 1) Explicit per-skill override wins.
        skill = detect_activated_skill(user_message)
        if skill and skill in _get_overrides():
            apply_skill_model_override(agent, skill, notice_fn=notice_fn)
            return

        # 2) Request-level model router (config-gated).
        switch_string = None
        try:
            from agent.model_router import route_model_for_turn
            switch_string = route_model_for_turn(agent, user_message)
        except Exception:
            logger.debug("model router failed", exc_info=True)
        if switch_string:
            apply_model_for_turn(agent, switch_string, "router", notice_fn=notice_fn)
            return

        # 3) Nothing dictates a model — restore base if a prior switch is pending.
        restore_skill_model(agent, notice_fn=notice_fn)
    except Exception:
        logger.debug("on_turn_start model hook failed", exc_info=True)
