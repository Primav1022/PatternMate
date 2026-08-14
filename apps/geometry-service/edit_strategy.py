"""LLM (or rule fallback) judges which pieces/edges a remix option should touch.

Not about aesthetics — about connection / edit scope:
  sleeve_only       → 只换袖片（泡袖、正肩…）
  body_and_sleeve   → 前+后+袖一起换（插肩、蝙蝠）
  body_integrated   → 换衣身、去掉独立袖（飞袖）
  neck_edge_only    → 只改前后片领口线（+可选领条）
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Any

from composition_engine import BACK_ROLES, COLLAR_ROLES, FRONT_ROLES, PURE_SLEEVE_ROLES
from simple_compose import BODY_ROLES, SLEEVE_STRATEGY, _option_slug, _sleeve_plan

ALLOWED_MODES = {"sleeve_only", "body_and_sleeve", "body_integrated", "neck_edge_only", "cuff_only"}

JUDGE_PROMPT = """你是服装纸样 remix 策略助手。根据选项判断「该改哪些片/线」，不是好看与否。

可选 mode（只能选一个）：
- sleeve_only：只换独立袖片。适用于泡袖 puff、正肩 set-in、bell 等，衣身袖窿结构不变。
- body_and_sleeve：前片+后片+袖片一起换。适用于插肩 raglan、蝙蝠袖 batwing（肩袖连身结构变了）。
- body_integrated：主要换前后衣身，并去掉独立袖片。适用于飞袖 flutter（没有单独袖片）。
- neck_edge_only：只改前后片上的领口线（可顺带领条 binding），禁止整片换衣身。适用于领口 neckline。
- cuff_only：只换袖口附件。

只返回 JSON：
{
  "mode": "sleeve_only|body_and_sleeve|body_integrated|neck_edge_only|cuff_only",
  "drop_host_sleeves": true/false,
  "reason": "一句中文，说明连接方式"
}
"""


def _model_config(overrides: dict[str, Any] | None = None) -> dict[str, str]:
    overrides = overrides or {}
    return {
        "base_url": str(overrides.get("model_base_url") or os.getenv("MODEL_BASE_URL", "")).strip().rstrip("/"),
        "model_name": str(overrides.get("model_name") or os.getenv("MODEL_NAME", "")).strip(),
        "api_key": str(overrides.get("model_api_key") or os.getenv("MODEL_API_KEY", "")).strip(),
    }


def model_ready(overrides: dict[str, Any] | None = None) -> bool:
    cfg = _model_config(overrides)
    return bool(cfg["base_url"] and cfg["model_name"] and cfg["api_key"] and not cfg["api_key"].startswith("fill-your-"))


def _roles_for_mode(mode: str) -> set[str]:
    if mode == "sleeve_only":
        return set(PURE_SLEEVE_ROLES)
    if mode == "body_and_sleeve":
        return set(BODY_ROLES | PURE_SLEEVE_ROLES)
    if mode == "body_integrated":
        return set(BODY_ROLES)
    if mode == "neck_edge_only":
        return set(COLLAR_ROLES)  # attachments only; body edges reshaped separately
    if mode == "cuff_only":
        return {"cuff", "cuff_left", "cuff_right"}
    return set(PURE_SLEEVE_ROLES)


def rule_strategy(group: str, option_id: str | None) -> dict[str, Any]:
    """Deterministic fallback — same table as simple_compose."""
    if group == "neckline":
        return {
            "mode": "neck_edge_only",
            "roles": sorted(_roles_for_mode("neck_edge_only")),
            "drop_host_sleeves": False,
            "slug": _option_slug(option_id),
            "source": "rule",
            "reason": "领口只改前后片领口线，不整片换衣身",
        }
    if group == "cuff":
        return {
            "mode": "cuff_only",
            "roles": sorted(_roles_for_mode("cuff_only")),
            "drop_host_sleeves": False,
            "slug": _option_slug(option_id),
            "source": "rule",
            "reason": "只换袖口附件",
        }
    plan = _sleeve_plan(option_id)
    return {
        "mode": plan["mode"],
        "roles": sorted(plan["roles"]),
        "drop_host_sleeves": bool(plan.get("drop_host_sleeves")),
        "slug": plan.get("slug"),
        "source": "rule",
        "reason": {
            "sleeve_only": "袖片连接方式，衣身袖窿结构不变",
            "body_and_sleeve": "插肩/连身肩袖，须换前后衣身+袖",
            "body_integrated": "飞袖无独立袖片，换衣身并去掉 host 袖",
        }.get(plan["mode"], plan["mode"]),
    }


def _normalize_plan(raw: dict[str, Any], *, group: str, option_id: str | None, source: str) -> dict[str, Any]:
    mode = str(raw.get("mode") or "").strip()
    if mode not in ALLOWED_MODES:
        return rule_strategy(group, option_id)
    drop = bool(raw.get("drop_host_sleeves"))
    if mode == "body_integrated":
        drop = True
    elif mode in {"sleeve_only", "body_and_sleeve", "neck_edge_only", "cuff_only"}:
        drop = False
    return {
        "mode": mode,
        "roles": sorted(_roles_for_mode(mode)),
        "drop_host_sleeves": drop,
        "slug": _option_slug(option_id),
        "source": source,
        "reason": str(raw.get("reason") or "").strip() or None,
    }


def judge_edit_strategy(
    *,
    group: str,
    option_id: str | None,
    family: str = "tshirt",
    host_base_option_id: str | None = None,
    model_overrides: dict[str, Any] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Return strategy plan. Tries LLM when use_llm; always has rule fallback."""
    fallback = rule_strategy(group, option_id)
    if not use_llm or not model_ready(model_overrides):
        return {**fallback, "llm_ok": False, "llm_error": None if not use_llm else "model_not_configured"}

    cfg = _model_config(model_overrides)
    payload_obj = {
        "family": family,
        "group": group,
        "option_id": option_id,
        "option_slug": _option_slug(option_id),
        "host_base_option_id": host_base_option_id,
        "known_sleeve_slugs": sorted(SLEEVE_STRATEGY.keys()),
    }
    body = json.dumps(
        {
            "model": cfg["model_name"],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": JUDGE_PROMPT},
                {"role": "user", "content": json.dumps(payload_obj, ensure_ascii=False)},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    ssl_verify = os.getenv("MODEL_SSL_VERIFY", "true").lower() not in {"0", "false", "no"}
    ctx = ssl.create_default_context()
    if not ssl_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "60")), context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = str(data["choices"][0]["message"]["content"]).strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        parsed = json.loads(content)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {**fallback, "llm_ok": False, "llm_error": str(exc)[:300]}

    plan = _normalize_plan(parsed, group=group, option_id=option_id, source="llm")
    plan["llm_ok"] = True
    plan["llm_error"] = None
    plan["llm_raw"] = parsed
    plan["rule_fallback"] = fallback
    return plan
