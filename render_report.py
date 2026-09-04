#!/usr/bin/env python3
"""Render a live-only report with a read-only detail drawer.

The renderer builds a public projection instead of embedding the input report
verbatim.  Source paths, sample identities, internal snapshots and raw
descriptions therefore never reach the browser.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Mapping

from report_schema import (
    TERMINAL_LIVE_STATES,
    VIDEO_SLOT_SCHEMA,
    WORKFLOW_DISPLAY_CONFIG,
    WORKFLOW_DISPLAY_SCHEMA,
    load_tool_profile,
    template_payload,
    validated_copy,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PUBLIC_TEXT_BLOCKLIST = (
    "口述",
    "口头",
    "音频",
    "字幕",
    "替代",
    "演示模式",
    "已听到",
    "可追溯",
    "同图",
    "仅凭",
    "倒推",
    "内部",
    "source_path",
    "offline_run_id",
    "scoring_report_summary",
    "mock_live_stream",
    "c475-nested-10-r1",
    "终态",
    "来源路径",
    "原始",
)
# Match filesystem-looking absolute paths without treating the slash in a
# normal visual phrase such as ``燃烧室/气门侧`` as provenance.  A Unix path
# must have at least one directory separator after its leading slash; the
# negative look-behind keeps a slash embedded in Chinese/word text literal.
_PATH_RE = re.compile(
    r"(?:\b[A-Za-z]:[\\/][^\s]+|(?<![\w\u4e00-\u9fff])/(?:[^/\s]+/)+[^/\s]*)"
)
_POSITIVE_CONCLUSION_MARKERS = (
    "符合标准",
    "符合要求",
    "达到要求",
    "达标",
    "合格",
    "成功完成",
    "已成功",
    "已完成核验",
    "通过",
    "正确完成",
    "满足要求",
    "动作正确",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入 JSON 顶层必须是对象")
    return value


def _data_uri(path_text: str) -> str:
    if not path_text or path_text.startswith("timestamp:"):
        return ""
    path = Path(path_text).expanduser()
    if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _clean_text(value: Any, fallback: str = "当前视频流中的相关画面。", limit: int = 360) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or _PATH_RE.search(text):
        return fallback
    if any(marker in text for marker in PUBLIC_TEXT_BLOCKLIST):
        return fallback
    return text


def _public_binding_explanation(value: Any, binding_state: str) -> str:
    """Keep progress copy neutral until a supplied result is complete.

    A live recogniser may reuse one explanation field for all lifecycle
    states.  Positive wording in a manual-review or in-progress update would
    look like a second, inferred score, so replace only that case with a
    factual status sentence.
    """
    cleaned = _clean_text(value, "", 260)
    if any(marker in cleaned for marker in _POSITIVE_CONCLUSION_MARKERS):
        if binding_state == "待人工确认":
            return "相关画面已绑定，仍有项目需要确认。"
        if not _is_terminal(binding_state):
            return "正在整理相关画面。"
    return cleaned or "当前视频流中的相关证据。"


def _public_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the visual fields needed by the viewer."""
    return {
        "evidence_id": str(evidence.get("evidence_id") or "evidence"),
        "kind": _clean_text(evidence.get("kind"), "image", 80),
        "phase": _clean_text(evidence.get("phase"), "", 40) or None,
        "round": _clean_text(evidence.get("round"), "", 80) or None,
        "order_index": evidence.get("order_index"),
        "timestamp": evidence.get("timestamp"),
        "timestamp_sec": evidence.get("timestamp_sec"),
        "confidence": evidence.get("confidence"),
        "caption": _clean_text(evidence.get("caption"), "当前视频流中的相关画面。", 220),
        "src": _data_uri(str(evidence.get("source_path") or "")),
    }


def _score_for_state(state: str) -> int | None:
    return {"证据已绑定": 1, "已完成评分": 1, "待人工确认": 0}.get(state)


def _is_visual_tool(tool_id: Any) -> bool:
    normalized = str(tool_id or "").strip().lower()
    if not normalized:
        return False
    # Use whole tool-name components.  Substring matching would mistake the
    # visual ``temporal_sequence_analyzer`` for an oral tool (``temporal``
    # contains the letters ``oral``).
    components = set(re.split(r"[^a-z0-9]+", normalized))
    return not components.intersection({"oral", "speech", "audio", "subtitle", "transcript"})


def _is_terminal(state: str) -> bool:
    return state in TERMINAL_LIVE_STATES


def _public_sections(
    form: Mapping[str, Any],
    *,
    include_criterion_ids: bool = False,
) -> list[dict[str, Any]]:
    criteria = [x for x in form.get("criteria", []) or [] if isinstance(x, Mapping)]
    counts: dict[str, int] = {}
    for criterion in criteria:
        counts[str(criterion.get("group") or "其他")] = counts.get(str(criterion.get("group") or "其他"), 0) + 1
    sections: list[dict[str, Any]] = []
    for section in form.get("sections", []) or []:
        if not isinstance(section, Mapping):
            continue
        ids = [str(x) for x in section.get("criterion_ids", []) or []]
        public_section = {
            "section_id": str(section.get("section_id") or "section"),
            "label": _clean_text(section.get("label"), "核验", 80),
            "criterion_count": len(ids),
        }
        if include_criterion_ids:
            public_section["criterion_ids"] = ids
        sections.append(public_section)
    if not sections:
        sections = [
            {
                "section_id": key,
                "label": _clean_text(key, "核验", 80),
                "criterion_count": value,
                **({"criterion_ids": [
                    str(criterion.get("criterion_id"))
                    for criterion in criteria
                    if str(criterion.get("group") or "其他") == key
                ]} if include_criterion_ids else {}),
            }
            for key, value in counts.items()
        ]
    return sections


def _public_criterion(criterion: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "criterion_id": str(criterion.get("criterion_id") or "criterion"),
        "group": _clean_text(criterion.get("group"), "核验", 80),
        "label": _clean_text(criterion.get("label"), "核验项", 120),
        "basis": _clean_text(criterion.get("basis"), "观察相关对象和动作画面。"),
        "evidence_slot_ids": [str(x) for x in criterion.get("evidence_slot_ids", []) or []],
        "required": bool(criterion.get("required", True)),
        "demo_gate": "required",
        "boundary": _clean_text(criterion.get("boundary"), "关注目标对象和动作过程。"),
    }


def _public_check(check: Mapping[str, Any]) -> dict[str, Any]:
    confidence = check.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    else:
        confidence = max(0.0, min(1.0, float(confidence)))
    status = str(check.get("status") or "pending")
    observation = _clean_text(check.get("observation"), "当前画面仍需确认。")
    reason = _clean_text(check.get("reason"), "")
    # A manual, negative or still-pending check may carry stale recogniser
    # copy from an earlier pass.  Keep the unresolved view factual instead of
    # leaking a positive conclusion before the live item is settled.
    if status in {"pending", "manual_review", "not_confirmed", "demo_disabled"}:
        if any(marker in observation for marker in _POSITIVE_CONCLUSION_MARKERS):
            observation = "当前画面仍需确认。"
        if any(marker in reason for marker in _POSITIVE_CONCLUSION_MARKERS):
            reason = "仍有核验项待确认。"
    return {
        "criterion_id": str(check.get("criterion_id") or "criterion"),
        "status": status,
        "confidence": confidence,
        "evidence_ids": [str(x) for x in check.get("evidence_ids", []) or []],
        "observation": observation,
        "reason": reason,
    }


def _public_detail(item: Mapping[str, Any], binding_state: str) -> dict[str, Any]:
    form = item.get("detail_form", {}) or {}
    if not isinstance(form, Mapping):
        form = {}
    criteria = [x for x in form.get("criteria", []) or [] if isinstance(x, Mapping)]
    evaluation = item.get("detail_evaluation", {}) or {}
    if not isinstance(evaluation, Mapping):
        evaluation = {}
    # The live binding owns the lifecycle.  A stale or forged detail state
    # must not unlock the drawer while the item is still being located or
    # analysed.
    if not _is_terminal(binding_state):
        evaluation_state = "analyzing" if binding_state == "证据生成中" else "locked"
    else:
        evaluation_state = str(evaluation.get("state") or "unavailable")
    # A terminal live result without per-criterion checks is explicitly
    # unavailable.  Do not fill the gap with a rubric conclusion or guessed
    # observations; the drawer will keep the concise dimension skeleton.
    if _is_terminal(binding_state) and (
        evaluation_state != "unlocked"
        or (evaluation_state == "unlocked" and criteria and not (evaluation.get("checks") or []))
    ):
        evaluation_state = "unavailable"
    sections = _public_sections(form)
    criterion_count = len(criteria)
    button = {
        "label": "展开详细表单",
        "enabled": True,
        "expanded": False,
    }
    if not _is_terminal(binding_state):
        return {
            "state": evaluation_state,
            "sections": sections,
            "criterion_count": criterion_count,
            "button": button,
        }

    if evaluation_state != "unlocked":
        public = {
            "schema": str(form.get("schema") or "realtime-detail-form/v1"),
            "state": evaluation_state,
            "sections": sections,
            "criterion_count": criterion_count,
            "button": button,
        }
        if binding_state == "待人工确认":
            public["unresolved_summary"] = _clean_text(
                evaluation.get("unresolved_summary"),
                "详细结果尚未提供。",
                260,
            )
        return public

    public = {
        "schema": str(form.get("schema") or "realtime-detail-form/v1"),
        "state": evaluation_state,
        "updated_at": _clean_text(evaluation.get("updated_at"), "", 80) or None,
        "summary": _clean_text(
            form.get("summary"),
            "本项目从对象、动作、时序和完成状态等维度进行核验。",
            180,
        ),
        "sections": _public_sections(form, include_criterion_ids=True),
        "criterion_count": criterion_count,
        "criteria": [_public_criterion(criterion) for criterion in criteria],
        "checks": [
            _public_check(check)
            for check in evaluation.get("checks", []) or []
            if isinstance(check, Mapping)
        ],
        "unresolved_summary": _clean_text(evaluation.get("unresolved_summary"), "", 260),
        "risk_boundaries": [
            _clean_text(criterion.get("boundary"), "关注目标对象和动作过程。")
            for criterion in criteria
            if criterion.get("boundary")
        ],
        "button": button,
    }
    if binding_state in {"证据已绑定", "已完成评分"}:
        summary = evaluation.get("high_level_evaluation")
        if summary is None:
            summary = evaluation.get("evaluation_summary")
        if summary:
            cleaned_summary = _clean_text(summary, "", 260)
            if cleaned_summary:
                public["high_level_evaluation"] = cleaned_summary
    return public


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return one item without provenance or raw scorer output."""
    binding = item.get("live_binding", {}) or {}
    if not isinstance(binding, Mapping):
        binding = {}
    binding_state = str(binding.get("state") or "待开始")
    # A recogniser may attach a frame directly to a slot.  Merge those
    # records into the item's public evidence set so every disclosed detail
    # reference remains clickable, while deduplicating by stable ID/path.
    evidence_records: list[Mapping[str, Any]] = []
    seen_evidence_keys: set[str] = set()
    for entry in list(binding.get("evidence", []) or []) + list(
        entry
        for slot in list(item.get("required_evidence_slots", []) or [])
        + list(item.get("enhanced_evidence_slots", []) or [])
        if isinstance(slot, Mapping)
        for entry in list(slot.get("evidence", []) or [])
    ):
        if not isinstance(entry, Mapping):
            continue
        key = str(entry.get("evidence_id") or entry.get("source_path") or "")
        if not key:
            key = f"record-{len(evidence_records)}"
        if key in seen_evidence_keys:
            continue
        seen_evidence_keys.add(key)
        evidence_records.append(entry)
    evidence = [_public_evidence(entry) for entry in evidence_records]
    evidence_ids = {entry["evidence_id"] for entry in evidence}
    required_slots = []
    for slot in item.get("required_evidence_slots", []) or []:
        if not isinstance(slot, Mapping):
            continue
        slot_evidence = [
            str(entry.get("evidence_id"))
            for entry in slot.get("evidence", []) or []
            if isinstance(entry, Mapping) and entry.get("evidence_id")
        ]
        required_slots.append({
            "slot_id": str(slot.get("slot_id") or "slot"),
            "label": str(slot.get("label") or slot.get("slot_id") or "证据"),
            "required": bool(slot.get("required", True)),
            "bound": str(slot.get("status") or "") == "bound" or bool(evidence_ids.intersection(slot_evidence)),
        })
    public_item: dict[str, Any] = {
        "item_number": item.get("item_number"),
        "item_id": item.get("item_id"),
        "display_name": item.get("display_name"),
        "required_slots": required_slots,
        "detail": _public_detail(item, binding_state),
        "binding": {
            "state": binding_state,
            "revision": binding.get("revision", 0),
            "changed_slot_ids": [str(x) for x in binding.get("changed_slot_ids", []) or []],
            "live_timestamp": binding.get("live_timestamp"),
            "live_start_sec": binding.get("live_start_sec"),
            "live_end_sec": binding.get("live_end_sec"),
            "time_range": {
                "start": binding.get("live_start_sec"),
                "end": binding.get("live_end_sec"),
            },
            "time_confidence": binding.get("time_confidence"),
            "evidence_explanation": _public_binding_explanation(binding.get("evidence_explanation"), binding_state),
            "evidence": evidence,
        },
    }
    if _is_terminal(binding_state):
        public_item["difficulty"] = item.get("difficulty")
        public_item["difficulty_label"] = item.get("difficulty_label")
        tools = []
        for tool in item.get("analysis_tools", []) or []:
            if not isinstance(tool, Mapping):
                continue
            tool_id = str(tool.get("tool_id") or "")
            if not _is_visual_tool(tool_id):
                continue
            tools.append({
                "tool_id": tool_id,
                "label": _clean_text(tool.get("label") or tool_id, "分析工具", 100),
            })
        public_item["analysis_tools"] = tools
        profile = item.get("analysis_profile", {}) or {}
        if not isinstance(profile, Mapping):
            profile = {}
        public_item["analysis_profile"] = {
            "distinct_tool_count": len(tools),
            "actual_analysis_task_count": profile.get("actual_analysis_task_count"),
            "complexity_features": [
                _clean_text(feature, "视觉链路", 80)
                for feature in profile.get("complexity_features", []) or []
            ],
        }
        public_item["score"] = _score_for_state(binding_state)
        public_item["score_max"] = 1
    if binding_state in {"证据已绑定", "已完成评分"}:
        # Do not invent a conclusion when a recogniser has not supplied one.
        # A terminal state controls score visibility, but it is not a
        # substitute for an evaluation sentence.
        target = item.get("realtime_target")
        if target:
            cleaned_target = _clean_text(target, "", 360)
            if cleaned_target:
                public_item["realtime_target"] = cleaned_target
        public_item["evidence_hint"] = _clean_text(item.get("evidence_hint"), "当前项目的相关画面已整理。")
    return public_item


def _public_presentation(presentation: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only the presentation knobs needed by the browser shell."""
    video_slot = presentation.get("video_slot", {})
    if not isinstance(video_slot, Mapping):
        video_slot = {}
    workflow = presentation.get("workflow_display", {})
    if not isinstance(workflow, Mapping):
        workflow = {}
    configured_stages = workflow.get("stages", [])
    stages = []
    if isinstance(configured_stages, list):
        for index, stage in enumerate(configured_stages):
            if not isinstance(stage, Mapping):
                continue
            stages.append({
                "stage_id": str(stage.get("stage_id") or f"stage-{index}"),
                "label": _clean_text(stage.get("label"), "分析阶段", 40),
                "order": int(stage.get("order", index)),
                "weight": float(stage.get("weight", 1 / max(1, len(configured_stages)))),
            })
    if not stages:
        stages = [dict(stage) for stage in WORKFLOW_DISPLAY_CONFIG["stages"]]
    cycle_value = workflow.get("cycle_ms")
    if cycle_value is None:
        cycle_value = WORKFLOW_DISPLAY_CONFIG["cycle_ms"]
    jitter_value = workflow.get("jitter_ratio")
    if jitter_value is None:
        jitter_value = WORKFLOW_DISPLAY_CONFIG["jitter_ratio"]
    return {
        "audience_mode": "live_only",
        "initial_state": str(presentation.get("initial_state") or "正在接入视频流"),
        "poll_interval_ms": int(presentation.get("poll_interval_ms") or 650),
        "video_slot": {
            "schema": VIDEO_SLOT_SCHEMA,
            "state": "reserved",
            "label": _clean_text(video_slot.get("label"), "实时视频", 40),
            "aspect_ratio": "16:9",
        },
        "workflow_display": {
            "schema": WORKFLOW_DISPLAY_SCHEMA,
            "cycle_ms": max(1000, int(cycle_value)),
            "jitter_ratio": max(0.0, min(0.25, float(jitter_value))),
            "stages": stages,
        },
        "footer": _clean_text(presentation.get("footer"), "本页面展示当前视频流的实时分析过程。"),
    }


def public_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strip non-display fields and inline only selected visual evidence."""
    validated_copy(payload)
    presentation = payload.get("presentation", {}) or {}
    items = [_public_item(item) for item in payload.get("items", []) or []]
    events: list[dict[str, Any]] = []
    for event in payload.get("events", []) or []:
        if not isinstance(event, Mapping):
            continue
        public_event: dict[str, Any] = {
            "event_id": event.get("event_id"),
            "item_id": event.get("item_id"),
            "delay_ms": int(event.get("delay_ms") or 900),
            "processing_ms": int(event.get("processing_ms") or 1200),
            "final_state": event.get("final_state", "证据生成中"),
            "evidence_ids": [str(x) for x in event.get("evidence_ids", []) or []],
        }
        if isinstance(event.get("item_patch"), Mapping):
            public_event["item_patch"] = _public_item(event["item_patch"])
        events.append(public_event)
    resolved = 0
    manual_review = 0
    current_score = 0
    for item in payload.get("items", []) or []:
        binding = item.get("live_binding", {}) or {}
        state = str(binding.get("state") or "待开始") if isinstance(binding, Mapping) else "待开始"
        score = _score_for_state(state)
        if score is not None:
            resolved += 1
            current_score += score
            manual_review += int(state == "待人工确认")
    return {
        "title": str(payload.get("title") or "实时智能实训分析"),
        "presentation": _public_presentation(presentation),
        "scope": {"active_item_count": len(items), "max_score": len(items)},
        "score_summary": {
            "current_score": current_score,
            "max_score": len(items),
            "resolved_item_count": resolved,
            "manual_review_count": manual_review,
        },
        "items": items,
        "events": events,
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>实时智能实训分析</title>
<style>
/* Duanyan tokens: paper / ink / rule / vermilion / gold */
:root{--ink:#1a2332;--ink-soft:#3a4556;--ink-mute:#7a8394;--ink-faint:#a8afbb;--paper:#fdfcf8;--paper-warm:#f7f4ec;--paper-cool:#fafaf7;--rule:#e8e4d9;--rule-soft:#efece3;--vermilion:#1661ab;--vermilion-soft:#5a8fc8;--vermilion-bg:#e9f0f9;--gold:#a8864b;--gold-bg:#f8f1e3;--accent-bg:#ecf2f8;--success:#6b7a3a;--success-bg:#f1f3e8;--warning:#a8864b;--warning-bg:#f8f1e3;--error:#c03030;--error-bg:#fdeaea;--shadow:0 8px 24px rgba(26,35,50,.08)}
*{box-sizing:border-box}html{background:var(--paper)}body{margin:0;background:radial-gradient(circle at 20% 0,rgba(22,97,171,.045) 0 1px,transparent 1px 100%),var(--paper);background-size:18px 18px;color:var(--ink);font-family:Inter,"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;line-height:1.45;-webkit-font-smoothing:antialiased}body.locked{overflow:hidden}button{font:inherit;color:inherit}.shell{max-width:1580px;margin:0 auto;padding:20px 28px 38px}.topbar{display:flex;gap:18px;align-items:center;justify-content:space-between;min-height:58px;margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid var(--rule)}.brand{display:flex;align-items:center;gap:14px}.brand-mark{width:42px;height:42px;border:1px solid var(--ink);border-radius:4px;display:grid;place-items:center;background:var(--ink);color:var(--paper);font-family:"Cormorant Garamond","Noto Serif SC",serif;font-size:18px;font-style:italic;letter-spacing:.04em}.eyebrow{color:var(--vermilion);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;letter-spacing:.15em;text-transform:uppercase}.brand h1{font-family:"Cormorant Garamond","Noto Serif SC",serif;font-size:27px;font-weight:600;letter-spacing:.01em;margin:1px 0 0}.live-pill{display:flex;align-items:center;gap:9px;border:1px solid #b8c7ad;background:var(--success-bg);border-radius:4px;padding:8px 12px;color:var(--success);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:11px}.pulse{width:8px;height:8px;border-radius:50%;background:var(--success);box-shadow:0 0 0 0 rgba(107,122,58,.45);animation:pulse 1.7s infinite}@keyframes pulse{70%{box-shadow:0 0 0 8px transparent}}
.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:18px}.metric{background:var(--paper-cool);border:1px solid var(--rule);border-radius:6px;padding:12px 14px}.metric-label{color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;letter-spacing:.04em}.metric-value{font-size:19px;font-weight:650;margin-top:5px;color:var(--ink)}.metric-value.cyan{color:var(--vermilion)}.metric-value.green{color:var(--success)}.metric-value.amber{color:var(--gold)}
.workspace{display:grid;grid-template-columns:248px minmax(0,1fr);gap:16px}.timeline,.content-card{background:rgba(253,252,248,.94);border:1px solid var(--rule);border-radius:8px;box-shadow:var(--shadow)}.timeline{padding:16px 12px;align-self:start;position:sticky;top:15px;max-height:calc(100vh - 30px);overflow:auto}.timeline-title{display:flex;align-items:center;justify-content:space-between;margin:0 8px 14px;font-family:"Noto Serif SC",serif;font-size:14px;font-weight:600}.timeline-title span{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;color:var(--ink-mute);font-weight:400}.timeline-list{display:flex;flex-direction:column;gap:3px;position:relative}.timeline-list:before{content:"";position:absolute;left:20px;top:16px;bottom:16px;border-left:1px dotted var(--rule);pointer-events:none}.timeline-btn{display:grid;grid-template-columns:31px 1fr auto;gap:9px;align-items:center;width:100%;text-align:left;background:transparent;border:1px solid transparent;border-radius:4px;padding:8px 7px;color:var(--ink-mute);cursor:pointer;position:relative}.timeline-btn:hover,.timeline-btn.active{background:var(--vermilion-bg);border-color:#c6d7ea;color:var(--ink)}.timeline-no{width:27px;height:27px;display:grid;place-items:center;border:1px solid var(--rule);border-radius:4px;background:var(--paper);color:var(--ink-soft);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:11px;font-weight:600;z-index:1}.timeline-btn.active .timeline-no{background:var(--vermilion);border-color:var(--vermilion);color:#fff}.timeline-name{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.timeline-state{display:block;font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;color:var(--ink-faint);margin-top:1px}.mini-dot{width:7px;height:7px;border:1px solid var(--ink-faint);border-radius:50%;background:var(--paper);z-index:1}.mini-dot.done{border-color:var(--success);background:var(--success)}.mini-dot.busy{border-color:var(--vermilion);background:var(--vermilion);animation:pulse 1.5s infinite}.mini-dot.review{border-color:var(--warning);background:var(--warning)}
.main{min-width:0}.hero{display:flex;justify-content:space-between;gap:22px;align-items:flex-end;padding:25px 26px;background:var(--paper-warm);border:1px solid var(--rule);border-radius:8px;margin-bottom:14px}.hero-kicker{color:var(--vermilion);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;letter-spacing:.08em}.hero h2{font-family:"Cormorant Garamond","Noto Serif SC",serif;font-size:35px;font-weight:600;line-height:1.2;margin:6px 0 9px}.hero p{margin:0;color:var(--ink-soft);max-width:730px;line-height:1.75;font-size:13px}.hero-side{text-align:right;min-width:190px}.stage-label{color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px}.stage-value{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:18px;font-weight:600;margin-top:6px}.progress{height:5px;background:var(--rule);border-radius:2px;overflow:hidden;margin-top:14px}.progress i{display:block;height:100%;background:var(--vermilion);border-radius:inherit;transition:width .35s ease}
.toolbar{display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;align-items:center;margin-bottom:13px}.filters,.controls{display:flex;gap:6px;flex-wrap:wrap}.difficulty-hint{color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;padding:7px 2px}.chip,.control,.detail-trigger{border:1px solid var(--rule);background:var(--paper);border-radius:4px;padding:7px 10px;color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;cursor:pointer;transition:background .15s,border-color .15s,color .15s}.chip.active,.chip:hover,.control:hover,.detail-trigger:hover{color:var(--ink);border-color:#b7cbe2;background:var(--vermilion-bg)}.control.primary{color:var(--paper);background:var(--ink);border-color:var(--ink);font-weight:600}.control.primary:hover{color:#fff;background:var(--vermilion);border-color:var(--vermilion)}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.item-card{background:var(--paper);border:1px solid var(--rule);border-radius:8px;padding:16px;min-width:0;transition:transform .2s,border-color .2s,box-shadow .2s}.item-card.current{border-color:var(--vermilion-soft);box-shadow:0 0 0 1px rgba(90,143,200,.18),var(--shadow);transform:translateY(-1px)}.item-card.review{border-color:#d8bd86;background:#fffdf8}.card-head{display:flex;gap:11px;align-items:flex-start;justify-content:space-between}.card-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end}.item-id{display:flex;align-items:center;gap:10px}.item-no{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:12px;font-weight:600;color:var(--vermilion);background:var(--vermilion-bg);border:1px solid #c6d7ea;padding:5px 7px;border-radius:4px}.item-title{font-family:"Noto Serif SC",serif;font-weight:600;font-size:15px}.difficulty{display:inline-block;margin-top:5px;font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;padding:3px 7px;border-radius:3px;background:var(--paper-warm);color:var(--ink-mute)}.difficulty.difficult{color:var(--error);background:var(--error-bg)}.difficulty.medium{color:var(--warning);background:var(--warning-bg)}.difficulty.easy{color:var(--success);background:var(--success-bg)}.status-badge{white-space:nowrap;font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;padding:4px 7px;border-radius:3px;background:var(--paper-warm);color:var(--ink-mute)}.status-badge.done{background:var(--success-bg);color:var(--success)}.status-badge.busy{background:var(--vermilion-bg);color:var(--vermilion)}.status-badge.review{background:var(--warning-bg);color:var(--warning)}.status-badge.located{background:var(--accent-bg);color:var(--vermilion)}.score-badge{white-space:nowrap;font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;font-weight:600;padding:4px 7px;border-radius:3px;background:var(--success-bg);color:var(--success)}.score-badge.zero{background:var(--error-bg);color:var(--error)}.target{margin:14px 0;color:var(--ink-soft);line-height:1.7;font-size:13px}.target-label{display:block;color:var(--vermilion);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;letter-spacing:.08em;margin-bottom:5px}.binding-line{display:flex;align-items:center;gap:8px;color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;border-top:1px solid var(--rule-soft);padding-top:10px}.binding-line strong{color:var(--ink);font-size:11px;font-weight:600}.card-footer{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:12px}.detail-trigger{color:var(--vermilion);border-color:#c6d7ea;background:var(--vermilion-bg)}
.evidence-box{margin-top:12px;background:var(--paper-cool);border:1px solid var(--rule-soft);border-radius:6px;padding:10px}.evidence-box.busy{min-height:75px;display:grid;place-items:center;color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px}.slot-row{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:9px}.slot{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;padding:3px 6px;border-radius:3px;background:var(--paper-warm);color:var(--ink-mute)}.slot.bound{color:var(--success);background:var(--success-bg)}.slot.empty{color:var(--ink-faint)}.evidence-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.evidence-thumb{min-width:0;position:relative;border-radius:4px;overflow:hidden;border:1px solid var(--rule);background:var(--paper-warm);aspect-ratio:4/3;padding:0;cursor:zoom-in}.evidence-thumb img{width:100%;height:100%;display:block;object-fit:cover}.evidence-thumb .empty-note{height:100%;display:grid;place-items:center}.evidence-thumb .tile-meta{position:absolute;left:0;right:0;bottom:0;background:linear-gradient(transparent,rgba(26,35,50,.82));padding:19px 6px 5px;font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;color:#fff;text-align:left}.evidence-thumb .confidence{float:right;color:#dbeaf7}.sequence{display:flex;gap:4px;align-items:center;margin-top:9px}.sequence span{flex:1;text-align:center;border:1px solid var(--rule);border-radius:3px;padding:5px 2px;background:var(--paper);color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px}.sequence span.on{background:var(--vermilion-bg);border-color:#c6d7ea;color:var(--vermilion)}.analysis-box{margin-top:12px;border-left:3px solid var(--vermilion);padding:10px 0 0 11px;background:var(--accent-bg)}.analysis-title{color:var(--vermilion);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;margin-bottom:8px;padding-right:10px}.tool-list{display:flex;flex-wrap:wrap;gap:5px;padding-right:10px}.tool-chip{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;padding:4px 6px;border:1px solid #d5e1ee;border-radius:3px;background:var(--paper);color:var(--ink-soft)}.profile-line{color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;line-height:1.6;margin-top:8px;padding:0 10px 9px 0}.explain{color:var(--ink-soft);font-size:11px;line-height:1.65;margin-top:9px}.empty-note{color:var(--ink-faint);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;padding:12px 0;text-align:center}
.drawer-backdrop,.lightbox{position:fixed;inset:0;background:rgba(26,35,50,.46);backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);z-index:30;opacity:1;transition:opacity .2s}.drawer-backdrop[hidden],.lightbox[hidden],.hover-preview[hidden]{display:none}.detail-drawer{position:absolute;right:0;top:0;height:100%;width:min(650px,92vw);background:var(--paper);border-left:1px solid var(--rule);box-shadow:-12px 0 36px rgba(26,35,50,.18);display:flex;flex-direction:column;transform:translateX(0);animation:drawer-in .2s ease-out}.drawer-head{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;padding:22px 22px 16px;border-bottom:1px solid var(--rule);background:var(--paper-warm)}.drawer-kicker{color:var(--vermilion);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px}.drawer-title{font-family:"Cormorant Garamond","Noto Serif SC",serif;font-size:26px;line-height:1.2;margin:5px 0}.drawer-meta{display:flex;flex-wrap:wrap;gap:6px;color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px}.drawer-close,.lightbox-close{border:1px solid var(--rule);background:var(--paper);border-radius:4px;width:32px;height:32px;cursor:pointer;font-size:20px;line-height:1}.drawer-close:hover,.lightbox-close:hover{border-color:var(--vermilion);color:var(--vermilion)}.drawer-body{overflow:auto;padding:18px 22px 34px}.drawer-status{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:16px}.drawer-status .status-badge{font-size:11px}.drawer-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-bottom:17px}.drawer-stat{border:1px solid var(--rule);background:var(--paper-cool);border-radius:4px;padding:9px}.drawer-stat label{display:block;color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px}.drawer-stat strong{display:block;margin-top:4px;font-size:12px}.drawer-section{border-top:1px solid var(--rule);padding:17px 0}.drawer-section:first-child{border-top:0;padding-top:0}.drawer-section h3{font-family:"Noto Serif SC",serif;font-size:15px;margin:0 0 10px}.drawer-section h3 span{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;color:var(--ink-mute);font-weight:400;margin-left:6px}.skeleton-groups{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}.skeleton-group{border:1px dashed var(--rule);border-radius:4px;padding:10px;color:var(--ink-mute);font-size:11px}.skeleton-group strong{display:block;color:var(--ink);font-family:"Noto Serif SC",serif;font-size:12px;margin-bottom:3px}.criteria-list{display:flex;flex-direction:column;gap:8px}.criterion-row{border:1px solid var(--rule-soft);border-radius:5px;background:var(--paper-cool);padding:10px}.criterion-head{display:flex;gap:8px;align-items:center;justify-content:space-between}.criterion-label{font-size:12px;font-weight:600}.criterion-group{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;color:var(--vermilion);margin-right:5px}.criterion-basis{color:var(--ink-soft);font-size:11px;line-height:1.6;margin-top:6px}.criterion-boundary{color:var(--ink-mute);font-size:10px;line-height:1.55;margin-top:5px}.check-list{display:flex;flex-direction:column;gap:7px}.check-row{border-left:3px solid var(--rule);background:var(--paper-cool);padding:9px 10px}.check-row.confirmed{border-color:var(--success)}.check-row.manual_review{border-color:var(--gold)}.check-row.not_confirmed{border-color:var(--error)}.check-row.pending{border-color:var(--vermilion-soft)}.check-line{display:flex;gap:7px;align-items:center;justify-content:space-between}.check-state{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;padding:3px 5px;border-radius:3px;background:var(--paper-warm);color:var(--ink-mute)}.check-state.confirmed{color:var(--success);background:var(--success-bg)}.check-state.manual_review{color:var(--gold);background:var(--gold-bg)}.check-state.not_confirmed{color:var(--error);background:var(--error-bg)}.check-state.pending{color:var(--vermilion);background:var(--vermilion-bg)}.check-confidence{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;color:var(--ink-mute)}.check-observation{font-size:11px;color:var(--ink-soft);line-height:1.6;margin-top:5px}.check-reason{font-size:10px;color:var(--gold);line-height:1.55;margin-top:4px}.evidence-ref-list{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}.evidence-ref{display:inline-flex;align-items:center;gap:4px;border:1px solid #c6d7ea;background:var(--vermilion-bg);border-radius:3px;padding:3px 5px;color:var(--vermilion);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;cursor:zoom-in}.evidence-ref:hover{border-color:var(--vermilion)}.risk-list{margin:0;padding-left:18px;color:var(--ink-soft);font-size:11px;line-height:1.7}.unresolved{border:1px solid #ead6aa;background:var(--gold-bg);border-radius:4px;padding:9px;color:#765d2e;font-size:11px;line-height:1.6}.high-level{border-left:3px solid var(--success);background:var(--success-bg);padding:10px 11px;color:var(--ink-soft);font-size:12px;line-height:1.65}.hover-preview{position:fixed;width:310px;min-height:205px;background:var(--paper);border:1px solid var(--rule);border-radius:6px;box-shadow:var(--shadow);z-index:60;padding:7px;pointer-events:none}.hover-preview img{display:block;width:100%;height:155px;object-fit:contain;background:var(--paper-warm);border-radius:3px}.hover-preview .preview-placeholder{height:155px;display:grid;place-items:center;background:var(--paper-warm);color:var(--ink-faint);font-size:11px;border-radius:3px}.preview-meta{display:flex;justify-content:space-between;gap:6px;margin-top:6px;color:var(--ink-soft);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px}.lightbox{z-index:80;display:grid;place-items:center;padding:28px}.lightbox-panel{position:relative;width:min(1120px,96vw);height:min(86vh,820px);background:var(--paper);border:1px solid var(--rule);border-radius:8px;box-shadow:0 18px 60px rgba(26,35,50,.25);display:flex;flex-direction:column;padding:16px}.lightbox-close{position:absolute;right:12px;top:12px;z-index:2}.lightbox-image{flex:1;min-height:0;width:100%;object-fit:contain;background:var(--paper-warm);border-radius:4px}.lightbox-placeholder{flex:1;display:grid;place-items:center;color:var(--ink-faint);background:var(--paper-warm);border-radius:4px}.lightbox-meta{padding:10px 3px 0;color:var(--ink-soft);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px}.footer{margin-top:17px;padding:13px 4px;color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;text-align:center;border-top:1px solid var(--rule)}.toast{position:fixed;right:22px;bottom:20px;background:var(--ink);border:1px solid var(--ink);border-radius:4px;padding:10px 13px;color:var(--paper);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;opacity:0;transform:translateY(8px);transition:.25s;pointer-events:none;box-shadow:var(--shadow);z-index:90}.toast.show{opacity:1;transform:none}
.evidence-thumb img{object-fit:contain;background:var(--paper-warm)}
@keyframes drawer-in{from{transform:translateX(20px);opacity:.4}to{transform:none;opacity:1}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.001ms!important}}
@media(max-width:1050px){.workspace{grid-template-columns:1fr}.timeline{position:static;max-height:none}.timeline-list{display:grid;grid-template-columns:repeat(3,1fr)}.timeline-list:before{display:none}.hero{align-items:flex-start;flex-direction:column}.hero-side{text-align:left}.cards{grid-template-columns:1fr}}@media(max-width:800px){.metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.shell{padding:14px}.topbar{align-items:flex-start;flex-direction:column}.metrics{grid-template-columns:repeat(2,1fr)}.timeline-list{grid-template-columns:repeat(2,1fr)}.hero h2{font-size:27px}.evidence-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.detail-drawer{width:100%}.drawer-head,.drawer-body{padding-left:16px;padding-right:16px}.drawer-grid{grid-template-columns:1fr 1fr}.lightbox{padding:10px}.lightbox-panel{height:92vh;width:100%}}
</style>
<style>
.video-slot{position:relative;aspect-ratio:16/9;width:100%;margin-bottom:14px;overflow:hidden;border:1px solid var(--rule);border-radius:8px;background:linear-gradient(135deg,#1a2332 0%,#253449 52%,#172331 100%);box-shadow:var(--shadow)}
.video-slot video{width:100%;height:100%;display:block;object-fit:contain;background:#172331}
.video-slot::after{content:"";position:absolute;inset:0;background:linear-gradient(120deg,rgba(255,255,255,.04),transparent 48%,rgba(255,255,255,.03));pointer-events:none}
.video-placeholder{position:absolute;inset:0;display:grid;place-items:center;text-align:center;color:#edf3f7;z-index:1;pointer-events:none}
.video-placeholder-inner{display:grid;gap:8px;justify-items:center}
.video-placeholder-mark{width:48px;height:48px;border:1px solid rgba(237,243,247,.55);border-radius:50%;display:grid;place-items:center;font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:13px;letter-spacing:.08em;color:#dbeaf7}
.video-placeholder-title{font-family:"Noto Serif SC",serif;font-size:18px;letter-spacing:.08em}.video-placeholder-note{font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;color:rgba(237,243,247,.7)}
.workflow-strip{display:flex;align-items:center;gap:14px;margin-bottom:14px;padding:12px 14px;border:1px solid var(--rule);border-radius:6px;background:var(--paper-cool);box-shadow:0 4px 14px rgba(26,35,50,.05)}
.workflow-heading{display:flex;align-items:center;gap:7px;flex:0 0 auto;color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px;letter-spacing:.04em}.workflow-heading i{width:7px;height:7px;border-radius:50%;background:var(--ink-faint)}.workflow-heading.active i{background:var(--vermilion);box-shadow:0 0 0 5px var(--vermilion-bg)}
.workflow-track{display:flex;align-items:stretch;gap:0;min-width:0;overflow-x:auto;scroll-behavior:smooth;flex:1;padding:2px 0}.workflow-stage{position:relative;display:flex;align-items:center;gap:7px;min-width:116px;padding:5px 13px 5px 8px;color:var(--ink-faint);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px;white-space:nowrap}.workflow-stage:not(:last-child)::after{content:"";height:1px;width:15px;background:var(--rule);margin-left:4px}.workflow-stage .workflow-light{width:10px;height:10px;border:1px solid var(--rule);border-radius:50%;background:var(--paper);flex:0 0 auto;transition:background .2s,border-color .2s,box-shadow .2s,transform .2s}.workflow-stage.active{color:var(--vermilion)}.workflow-stage.active .workflow-light{border-color:var(--vermilion);background:var(--vermilion);box-shadow:0 0 0 5px var(--vermilion-bg),0 0 16px rgba(22,97,171,.32);transform:scale(1.08)}.workflow-stage.done{color:var(--ink-soft)}.workflow-stage.done .workflow-light{border-color:var(--success);background:var(--success)}.workflow-stage.pending .workflow-light{background:var(--paper-warm)}.workflow-stage.active::after,.workflow-stage.done::after{background:#c5d5e6}.workflow-state{flex:0 0 auto;min-width:78px;text-align:right;color:var(--ink-mute);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:9px}
.cards-empty{grid-column:1/-1;border:1px dashed var(--rule);border-radius:7px;background:var(--paper-cool);padding:30px 18px;text-align:center;color:var(--ink-faint);font-family:"JetBrains Mono","SFMono-Regular",monospace;font-size:10px}
.item-card.completion-focus{border-color:var(--vermilion);box-shadow:0 0 0 3px var(--vermilion-bg),var(--shadow)}
.item-card.reveal{animation:card-reveal .28s ease-out}@keyframes card-reveal{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media(max-width:800px){.workflow-strip{align-items:flex-start;flex-direction:column;gap:7px}.workflow-track{width:100%}.workflow-state{align-self:flex-end;min-width:0}.video-placeholder-title{font-size:15px}}
@media(prefers-reduced-motion:reduce){.workflow-track{scroll-behavior:auto}.item-card.reveal{animation:none}.workflow-stage .workflow-light{transition:none}}
</style>
</head>
<body>
<div class="shell">
  <header class="topbar"><div class="brand"><div class="brand-mark">AI</div><div><div class="eyebrow">LIVE ANALYSIS CONSOLE</div><h1 id="title">实时智能实训分析</h1></div></div><div class="live-pill"><i class="pulse"></i><span id="connection">视频流已连接</span></div></header>
  <section class="metrics"><div class="metric"><div class="metric-label">当前分析阶段</div><div class="metric-value cyan" id="phase">正在接入视频流</div></div><div class="metric"><div class="metric-label">已处理项目</div><div class="metric-value green" id="done">0/13</div></div><div class="metric"><div class="metric-label">实时总分</div><div class="metric-value amber" id="score">0/13</div></div><div class="metric"><div class="metric-label">证据绑定</div><div class="metric-value" id="bound">0</div></div><div class="metric"><div class="metric-label">系统提示</div><div class="metric-value" id="quality">等待有效画面</div></div></section>
  <div class="workspace"><aside class="timeline"><div class="timeline-title">操作流程 <span>8 → 20</span></div><div class="timeline-list" id="timeline"></div></aside><main class="main"><section class="hero"><div><div class="hero-kicker" id="heroKicker">实时识别中 · 当前项目</div><h2 id="heroTitle">等待当前操作</h2><p id="heroText">系统正在从视频流中定位操作对象，证据生成后会自动整理到对应项目。</p></div><div class="hero-side"><div class="stage-label">实时完成度</div><div class="stage-value" id="heroProgress">0 / 13</div><div class="progress"><i id="progressBar" style="width:0%"></i></div></div></section><div class="toolbar"><div class="filters" id="difficultyFilters" hidden><button class="chip active" data-filter="all">全部</button><button class="chip" data-filter="difficult">困难</button><button class="chip" data-filter="medium">中等</button><button class="chip" data-filter="easy">简单</button></div><span class="difficulty-hint" id="difficultyHint">完成评分后显示评价难度</span><div class="controls"><button class="control primary" id="start">▶ 启动评测</button><button class="control" id="reset">重置</button></div></div><section class="cards" id="cards"></section></main></div>
  <footer class="footer" id="footer">本页面展示当前视频流的实时分析过程。证据生成后显示对应核验信息，低置信度结果保留待人工确认。</footer>
</div>
<div class="toast" id="toast"></div>
<div class="drawer-backdrop" id="drawer-backdrop" hidden><aside class="detail-drawer" id="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title"><div class="drawer-head"><div><div class="drawer-kicker" id="drawer-kicker">详细核验</div><h2 class="drawer-title" id="drawer-title">详细表单</h2><div class="drawer-meta" id="drawer-meta"></div></div><button class="drawer-close" id="drawer-close" type="button" aria-label="关闭详细表单">×</button></div><div class="drawer-body" id="drawer-body"></div></aside></div>
<div class="hover-preview" id="hover-preview" hidden><div id="hover-media"></div><div class="preview-meta"><span id="hover-phase">证据</span><span id="hover-confidence"></span></div></div>
<div class="lightbox" id="lightbox" hidden role="dialog" aria-modal="true" aria-label="证据大图"><div class="lightbox-panel"><button class="lightbox-close" id="lightbox-close" type="button" aria-label="关闭大图">×</button><div id="lightbox-media" style="display:flex;flex:1;min-height:0"></div><div class="lightbox-meta" id="lightbox-meta"></div></div></div>
<script>
const DATA = __REPORT_DATA__;
const statuses = {"待开始":"pending","已定位":"located","证据生成中":"busy","证据已绑定":"done","已完成评分":"done","待人工确认":"review"};
const labels = {difficult:"困难",medium:"中等",easy:"简单"};
const terminalStates = new Set(["证据已绑定","已完成评分","待人工确认"]);
const checkLabels = {pending:"待核验",confirmed:"已确认",not_confirmed:"未确认",manual_review:"待人工确认",demo_disabled:"暂不可用"};
const detailStateLabels = {locked:"等待核验",analyzing:"分析中",unlocked:"已提供逐条结果",unavailable:"详细结果待提供"};
let state = DATA.items.map((item,index)=>({...item,status:displayState(item.binding&&item.binding.state||"待开始"),index,binding:{...(item.binding||{}),evidence:[...((item.binding&&item.binding.evidence)||[])]}}));
let current = 0, evaluationActive = false, processing = false, pollTimer = null, filter = "all", pending = [], pendingKeys = new Set(), drawerIndex = null, newCardIndexes = new Set();
let hoverTimer = null, hoverButton = null;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
const WORKFLOW_FALLBACK = {schema:"realtime-workflow-display/v1",cycle_ms:3000,jitter_ratio:.15,stages:[
  {stage_id:"ingest",label:"接入画面",order:0,weight:.10},
  {stage_id:"planning",label:"任务规划",order:1,weight:.15},
  {stage_id:"orchestration",label:"工具编排",order:2,weight:.18},
  {stage_id:"visual_analysis",label:"视觉分析",order:3,weight:.30},
  {stage_id:"evidence",label:"证据整理",order:4,weight:.17},
  {stage_id:"decision",label:"结果判定",order:5,weight:.10}
]};
const workflowConfig = {...WORKFLOW_FALLBACK,...(DATA.presentation&&DATA.presentation.workflow_display||{})};
const workflowStages = Array.isArray(workflowConfig.stages)&&workflowConfig.stages.length===6?workflowConfig.stages:WORKFLOW_FALLBACK.stages;
let workflowTimer = null, workflowToken = 0, workflowItemIndex = null, workflowStageIndex = -1, workflowRng = Math.random;
function workflowReducedMotion(){return window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches}
function ensureRealtimeSurfaces(){
  const main=document.querySelector(".main"), hero=main&&main.querySelector(".hero");
  if(!main||!hero)return;
  if(!$("video-slot")){
    const slot=document.createElement("section");slot.id="video-slot";slot.className="video-slot";slot.setAttribute("aria-label","实时视频窗口");
    slot.innerHTML='<video id="live-video" data-video-slot="realtime" playsinline muted aria-label="实时视频画面"></video><div class="video-placeholder"><div class="video-placeholder-inner"><div class="video-placeholder-mark">LIVE</div><div class="video-placeholder-title">实时视频</div><div class="video-placeholder-note">等待视频源接入</div></div></div>';
    main.insertBefore(slot,hero);
  }
  if(!$("workflow-strip")){
    const strip=document.createElement("section");strip.id="workflow-strip";strip.className="workflow-strip";strip.setAttribute("aria-label","后端模型工作状态");
    strip.innerHTML='<div class="workflow-heading"><i></i><span>模型工作状态</span></div><div class="workflow-track" id="workflow-track" role="list">'+workflowStages.map(stage=>`<div class="workflow-stage pending" data-workflow-stage="${esc(stage.stage_id)}" role="listitem"><i class="workflow-light"></i><span>${esc(stage.label)}</span></div>`).join("")+'</div><span class="workflow-state" id="workflow-state" role="status" aria-live="polite">等待分析</span>';
    main.insertBefore(strip,hero);
  }
}
function workflowSetVisual(index,state){
  const strip=$("workflow-strip"),track=$("workflow-track"),heading=strip&&strip.querySelector(".workflow-heading"),stateLabel=$("workflow-state");
  if(!strip||!track)return;
  const stages=[...track.querySelectorAll(".workflow-stage")];stages.forEach((node,i)=>{node.classList.toggle("active",state==="running"&&i===index);node.classList.toggle("done",state==="running"&&i<index);node.classList.toggle("pending",!(state==="running"&&i<=index));});
  if(heading)heading.classList.toggle("active",state==="running");
  if(stateLabel)stateLabel.textContent=state==="running"?(workflowStages[index]&&workflowStages[index].label||"分析中"):state==="review"?"等待确认":state==="done"?"本轮完成":"等待分析";
  if(state==="running"&&stages[index])stages[index].scrollIntoView({behavior:workflowReducedMotion()?"auto":"smooth",block:"nearest",inline:"center"});
}
function workflowRandom(){const value=Number(workflowRng());return Number.isFinite(value)?Math.max(0,Math.min(1,value)):Math.random()}
function workflowSchedule(totalMs){
  const total=Math.max(1000,Number(totalMs)||Number(workflowConfig.cycle_ms)||3000),jitter=Math.max(0,Math.min(.25,Number(workflowConfig.jitter_ratio)||.15));
  const raw=workflowStages.map(stage=>Math.max(.001,Number(stage.weight)||1/workflowStages.length)*(1+(workflowRandom()*2-1)*jitter));
  const sum=raw.reduce((a,b)=>a+b,0),minimum=120,durations=raw.map(value=>Math.max(minimum,Math.round(total*value/sum)));
  let delta=Math.round(total)-durations.reduce((a,b)=>a+b,0);
  if(delta>=0){durations[durations.length-1]+=delta}else{for(const index of durations.map((_,i)=>i).sort((a,b)=>durations[b]-durations[a])){const room=Math.max(0,durations[index]-minimum),take=Math.min(room,-delta);durations[index]-=take;delta+=take;if(delta>=0)break}}
  return durations;
}
function workflowStop(resultState="idle"){
  workflowToken+=1;if(workflowTimer){clearTimeout(workflowTimer);workflowTimer=null}workflowItemIndex=null;workflowStageIndex=-1;workflowSetVisual(-1,resultState==="review"?"review":resultState==="done"?"done":"idle");
}
function workflowStart(itemIndex,totalMs){
  workflowStop();workflowItemIndex=itemIndex;const token=workflowToken,schedule=workflowSchedule(totalMs),reduced=workflowReducedMotion();let index=0;
  const advance=()=>{if(token!==workflowToken||workflowItemIndex!==itemIndex)return;workflowStageIndex=index;workflowSetVisual(index,"running");const wait=schedule[index];index+=1;if(index<schedule.length)workflowTimer=setTimeout(advance,reduced?Math.min(wait,80):wait);else workflowTimer=setTimeout(()=>{if(token===workflowToken&&workflowItemIndex===itemIndex){workflowStart(itemIndex,totalMs)}},reduced?80:0)};
  advance();
}
function cls(status){return statuses[status]||"pending"}
function displayState(status){return status==="证据已绑定"?"已完成评分":status}
function isTerminal(item){return terminalStates.has(item.status)}
function isCompleted(item){return displayState(item.status)==="已完成评分"}
function scoreFor(item){return item.status==="待人工确认"?0:(item.status==="证据已绑定"||item.status==="已完成评分"?1:null)}
function doneCount(){return state.filter(isTerminal).length}
function detailState(item){return item.detail&&item.detail.state||(!isTerminal(item)?(item.status==="证据生成中"?"analyzing":"locked"):"unavailable")}
function renderTimeline(){ $("timeline").innerHTML=state.map((item,i)=>`<button class="timeline-btn ${i===current?"active":""}" data-index="${i}" type="button"><span class="timeline-no">${item.item_number}</span><span class="timeline-name">${esc(item.display_name)}<small class="timeline-state">${esc(item.status)}</small></span><i class="mini-dot ${cls(item.status)}"></i></button>`).join(""); document.querySelectorAll(".timeline-btn").forEach(btn=>btn.onclick=()=>{current=Number(btn.dataset.index);render()}) }
function evidenceLabel(e){const stage=e.phase||e.kind||"证据";return e.round?`${e.round} · ${stage}`:stage}
function evidenceButtonMarkup(item,e,itemIndex){const phase=evidenceLabel(e);const time=e.timestamp?` · ${esc(e.timestamp)}`:"";const confidence=e.confidence!=null?`${Math.round(Number(e.confidence)*100)}%`:"";return `<button class="evidence-thumb" type="button" data-item-index="${itemIndex}" data-evidence-id="${esc(e.evidence_id)}" aria-label="查看${esc(phase)}证据">${e.src?`<img src="${e.src}" alt="${esc(phase)}证据">`:`<span class="empty-note">图像准备中</span>`}<span class="tile-meta">${esc(phase)}${time}<b class="confidence">${confidence}</b></span></button>`}
function evidenceMarkup(item,itemIndex){
  if(["待开始","已定位"].includes(item.status))return `<div class="evidence-box busy">${item.status==="待开始"?"等待当前操作进入识别区域":"已定位对象，准备整理证据"}</div>`;
  if(item.status==="证据生成中")return `<div class="evidence-box busy"><span class="pulse"></span>&nbsp;正在生成当前项目证据…</div>`;
  const evidence=item.binding&&item.binding.evidence||[];
  const slots=(item.required_slots||[]).map(slot=>`<span class="slot ${slot.bound?"bound":"empty"}">${esc(slot.label)}</span>`).join("");
  const tiles=evidence.slice(0,6).map(e=>evidenceButtonMarkup(item,e,itemIndex)).join("");
  const seq=evidence.filter(e=>e.phase||e.round).sort((a,b)=>(Number(a.order_index)||9999)-(Number(b.order_index)||9999)||(Number(a.timestamp_sec)||0)-(Number(b.timestamp_sec)||0));
  const explanation=item.binding&&item.binding.evidence_explanation||"当前项目的相关画面已整理。";
  return `<div class="evidence-box"><div class="slot-row">${slots}</div>${tiles?`<div class="evidence-grid">${tiles}</div>`:`<div class="empty-note">当前项目仍在等待可用证据图</div>`}${seq.length?`<div class="sequence">${["开始","动作中","完成"].map((p,i)=>`<span class="${seq[i]?"on":""}">${p}</span>`).join("")}</div>`:""}<div class="explain">${esc(explanation)}</div></div>`;
}
function analysisMarkup(item){if(!isTerminal(item))return "";const profile=item.analysis_profile||{};const tools=(item.analysis_tools||[]).map(tool=>`<span class="tool-chip">${esc(tool.label||tool.tool_id)}</span>`).join("");const features=(profile.complexity_features||[]).map(feature=>esc(feature)).join(" · ");const metrics=[profile.distinct_tool_count!=null?`${profile.distinct_tool_count} 个工具`:"",profile.actual_analysis_task_count!=null?`实际分析任务 ${profile.actual_analysis_task_count} 个`:""].filter(Boolean).join(" · ");return `<div class="analysis-box"><div class="analysis-title">分析链 · ${esc(item.difficulty_label||labels[item.difficulty]||"")}</div><div class="tool-list">${tools||`<span class="tool-chip">工具链整理中</span>`}</div><div class="profile-line">${esc(metrics)}${features?`<br>证据链：${features}`:""}</div></div>`}
function cardMarkup(item,i,extraClass=""){const terminal=isTerminal(item);const evaluation=isCompleted(item)&&item.realtime_target?`<div class="target"><span class="target-label">评分结论</span>${esc(item.realtime_target)}</div>`:"";const difficulty=terminal?`<span class="difficulty ${esc(item.difficulty||"")}">${esc(item.difficulty_label||labels[item.difficulty]||"")}</span>`:"";const score=terminal?`<span class="score-badge ${scoreFor(item)===0?"zero":""}">${scoreFor(item)} / 1 分</span>`:"";return `<article class="item-card ${i===current?"current":""} ${item.status==="待人工确认"?"review":""} ${extraClass}" data-item-index="${i}" data-difficulty="${terminal?esc(item.difficulty||""):""}"><div class="card-head"><div class="item-id"><span class="item-no">${item.item_number}</span><div><div class="item-title">${esc(item.display_name)}</div>${difficulty}</div></div><div class="card-actions">${score}<span class="status-badge ${cls(item.status)}">${esc(item.status)}</span></div></div>${evaluation}<div class="binding-line"><span>现场时间</span><strong>${esc(item.binding&&item.binding.live_timestamp||"等待")}</strong><span>·</span><span>置信度</span><strong>${item.binding&&item.binding.time_confidence!=null?Math.round(Number(item.binding.time_confidence)*100)+"%":"—"}</strong></div>${evidenceMarkup(item,i)}${analysisMarkup(item)}<div class="card-footer"><span class="difficulty-hint">详细核验 · ${item.detail&&item.detail.criterion_count||0} 项</span><button class="detail-trigger" type="button" data-detail-index="${i}" aria-controls="detail-drawer" aria-expanded="false">展开详细表单</button></div></article>`}
function renderCards(){const visible=state.map((item,i)=>({item,i})).filter(x=>x.item.status!=="待开始"&&(filter==="all"||(isTerminal(x.item)&&x.item.difficulty===filter)));$("cards").innerHTML=visible.length?visible.map(x=>cardMarkup(x.item,x.i,newCardIndexes.has(x.i)?"reveal":"")).join(""):"<div class=\"cards-empty\">等待识别到第一个操作项目</div>";if(newCardIndexes.size)requestAnimationFrame(()=>newCardIndexes.clear())}
function focusLatestCompleted(index){requestAnimationFrame(()=>{const card=document.querySelector(`.item-card[data-item-index="${index}"]`);if(!card)return;card.scrollIntoView({behavior:workflowReducedMotion()?"auto":"smooth",block:"center",inline:"nearest"});card.classList.add("completion-focus");setTimeout(()=>card.classList.remove("completion-focus"),700)})}
function render(){ensureRealtimeSurfaces();const item=state[current]||state[0];const complete=doneCount(),bound=state.reduce((n,x)=>n+(x.binding&&x.binding.evidence||[]).length,0),score=state.reduce((n,x)=>n+(scoreFor(x)||0),0),hasDifficulty=state.some(isTerminal);$("title").textContent=DATA.title;$("footer").textContent=DATA.presentation.footer;$("done").textContent=`${complete}/${state.length}`;$("score").textContent=`${score}/${state.length}`;$("bound").textContent=String(bound);$("heroProgress").textContent=`${complete} / ${state.length}`;$("progressBar").style.width=`${state.length?Math.round(complete/state.length*100):0}%`;$("difficultyFilters").hidden=!hasDifficulty;$("difficultyHint").hidden=hasDifficulty;$("phase").textContent=item.status==="待开始"?DATA.presentation.initial_state:item.status;$("quality").textContent=item.status==="待人工确认"?"需要人工确认":item.status==="证据生成中"?"证据生成中":complete?"证据链已整理":"等待有效画面";$("heroKicker").textContent=`实时识别中 · 项目 ${item.item_number}`;$("heroTitle").textContent=item.display_name;$("heroText").textContent=isCompleted(item)?(item.binding&&item.binding.evidence_explanation||item.realtime_target||"相关画面已整理。"):(item.binding&&item.binding.evidence_explanation&&item.binding.evidence_explanation!=="等待当前视频流中的有效证据。"?item.binding.evidence_explanation:"系统正在从视频流中定位操作对象，证据生成后会自动整理到对应项目。");renderTimeline();renderCards();bindEvidenceInteractions();if(drawerIndex!==null)renderDrawer(drawerIndex)}
function toast(message){$("toast").textContent=message;$("toast").classList.add("show");setTimeout(()=>$("toast").classList.remove("show"),1600)}
function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
function signature(item){const b=item.binding||{},d=item.detail||{};return JSON.stringify({binding:{revision:b.revision,state:b.state,live_timestamp:b.live_timestamp,live_start_sec:b.live_start_sec,live_end_sec:b.live_end_sec,time_confidence:b.time_confidence,evidence:(b.evidence||[]).map(e=>[e.evidence_id,e.timestamp,e.timestamp_sec,e.phase,e.round,e.order_index,e.confidence,e.caption,e.src])},detail:d})}
function mergeView(view){if(!view||!Array.isArray(view.items))return;view.items.forEach(incoming=>{const index=state.findIndex(item=>item.item_id===incoming.item_id);if(index<0)return;const currentItem=state[index];if(pendingKeys.has(incoming.item_id)||signature(incoming)===signature(currentItem))return;pending.push({index,incoming,finalState:displayState((incoming.binding||{}).state||"待开始"),processingMs:Number(view.processing_ms||0)});pendingKeys.add(incoming.item_id)});drainQueue()}
async function present(update){const old=state[update.index];if(!old)return;current=update.index;const wasPending=old.status==="待开始";state[update.index]={...old,status:"已定位"};if(wasPending)newCardIndexes.add(update.index);render();toast(`已定位：${old.display_name}`);await delay(430);if(!evaluationActive){pendingKeys.delete(update.incoming.item_id);return}state[update.index]={...state[update.index],status:"证据生成中",detail:{...(update.incoming.detail||state[update.index].detail||{}),state:"analyzing"}};workflowStart(update.index,update.processingMs||workflowConfig.cycle_ms);render();await delay(update.processingMs||950);if(!evaluationActive){workflowStop();pendingKeys.delete(update.incoming.item_id);return}const nextState=displayState(update.finalState||"已完成评分");state[update.index]={...update.incoming,status:nextState,index:update.index,binding:{...(update.incoming.binding||{}),evidence:[...((update.incoming.binding||{}).evidence||[])]}};if(nextState==="证据生成中")workflowStart(update.index,workflowConfig.cycle_ms);else workflowStop(nextState==="待人工确认"?"review":isCompleted(state[update.index])?"done":"idle");const newlyCompleted=isCompleted(state[update.index])&&!isCompleted(old);render();if(newlyCompleted)focusLatestCompleted(update.index);toast(`${old.display_name}：${nextState}`)}
async function drainQueue(){if(processing||!evaluationActive)return;processing=true;while(pending.length&&evaluationActive){const update=pending.shift();await present(update);pendingKeys.delete(update.incoming.item_id)}processing=false}
async function pollReport(){if(!evaluationActive||location.protocol==="file:")return;try{const response=await fetch(`/api/report?ts=${Date.now()}`,{cache:"no-store"});if(response.ok)mergeView(await response.json())}catch(error){$("connection").textContent="等待分析服务";$("quality").textContent="连接重试中"}}
async function runMockEvents(){for(const event of DATA.events||[]){if(!evaluationActive)break;await delay(Number(event.delay_ms||900));if(!evaluationActive)break;if(event.item_patch)mergeView({items:[event.item_patch],processing_ms:event.processing_ms});while(processing&&evaluationActive)await delay(120)}}
async function startEvaluation(){if(evaluationActive){evaluationActive=false;if(pollTimer)clearInterval(pollTimer);workflowStop();$("start").textContent="▶ 启动评测";$("connection").textContent="评测已暂停";toast("实时评测已暂停");return}const fileMode=location.protocol==="file:";if(fileMode&&!(DATA.events||[]).length){toast("请通过本地演示服务启动评测");return}evaluationActive=true;$("start").textContent="Ⅱ 暂停评测";$("connection").textContent="实时分析中";toast("已启动实时评测");if(!fileMode){await pollReport();pollTimer=setInterval(pollReport,Number(DATA.presentation.poll_interval_ms||650))}if((DATA.events||[]).length)runMockEvents()}
async function resetReport(){if(location.protocol==="file:"){toast("请通过本地演示服务执行重置");return}evaluationActive=false;if(pollTimer)clearInterval(pollTimer);workflowStop();pending=[];pendingKeys.clear();processing=false;newCardIndexes.clear();try{const response=await fetch("/api/reset",{method:"POST"});if(!response.ok)throw new Error("reset failed");const view=await response.json();state=view.items.map((item,index)=>({...item,status:"待开始",index,binding:{...(item.binding||{}),evidence:[...((item.binding||{}).evidence||[])]},detail:{...(item.detail||{}),state:"locked",checks:[]}}));current=0;filter="all";document.querySelectorAll(".chip").forEach(x=>x.classList.toggle("active",x.dataset.filter==="all"));closeDetail();$("start").textContent="▶ 启动评测";$("connection").textContent="视频流已连接";render();toast("已恢复初始评测状态")}catch(error){toast("重置失败，请检查本地演示服务")}}
function confidenceText(value){return value==null?"—":`${Math.round(Number(value)*100)}%`}
function formatSeconds(value){const number=Number(value);if(!Number.isFinite(number)||number<0)return "—";const total=Math.round(number);return `${String(Math.floor(total/60)).padStart(2,"0")}:${String(total%60).padStart(2,"0")}`}
function timeRangeText(item){const binding=item.binding||{},range=binding.time_range||{};const start=range.start!=null?range.start:binding.live_start_sec,end=range.end!=null?range.end:binding.live_end_sec;if(start==null&&end==null)return binding.live_timestamp||"—";if(start==null)return formatSeconds(end);if(end==null)return formatSeconds(start);return `${formatSeconds(start)} — ${formatSeconds(end)}`}
function evidenceById(item,id){return (item.binding&&item.binding.evidence||[]).find(e=>String(e.evidence_id)===String(id))}
function detailEvidenceRef(item,id){const evidence=evidenceById(item,id);if(!evidence)return `<span class="evidence-ref">证据未找到</span>`;return `<button class="evidence-ref" type="button" data-item-index="${state.indexOf(item)}" data-evidence-id="${esc(evidence.evidence_id)}">${esc(evidenceLabel(evidence))}${evidence.timestamp?` · ${esc(evidence.timestamp)}`:""}</button>`}
function drawerSkeleton(item,detail,message="详细结果将在当前项目完成分析后显示。"){return `<section class="drawer-section"><h3>核验维度 <span>${detail.criterion_count||0} 项</span></h3><div class="skeleton-groups">${(detail.sections||[]).map(section=>`<div class="skeleton-group"><strong>${esc(section.label)}</strong>${section.criterion_count||0} 项</div>`).join("")||`<div class="skeleton-group"><strong>详细核验</strong>等待数据</div>`}</div></section><section class="drawer-section"><div class="empty-note">${esc(message)}</div></section>`}
function drawerCriteria(item,detail){const checks=new Map((detail.checks||[]).map(check=>[String(check.criterion_id),check]));const summary=detail.summary?`<p class="detail-summary">${esc(detail.summary)}</p>`:"";return `<section class="drawer-section"><h3>判断依据 <span>${detail.criterion_count||0} 项</span></h3>${summary}<div class="criteria-list">${(detail.criteria||[]).map(criterion=>{const check=checks.get(String(criterion.criterion_id));return `<div class="criterion-row"><div class="criterion-head"><div class="criterion-label"><span class="criterion-group">${esc(criterion.group)}</span>${esc(criterion.label)}</div>${check?`<span class="check-state ${esc(check.status)}">${esc(checkLabels[check.status]||check.status)}</span>`:`<span class="check-state">待核验</span>`}</div><div class="criterion-basis">${esc(criterion.basis)}</div><div class="criterion-boundary">${esc(criterion.boundary)}</div>${check?`<div class="check-observation">${esc(check.observation||"")}</div>${check.reason?`<div class="check-reason">${esc(check.reason)}</div>`:""}<div class="check-line"><span class="check-confidence">置信度 ${confidenceText(check.confidence)}</span></div>${(check.evidence_ids||[]).length?`<div class="evidence-ref-list">${check.evidence_ids.map(id=>detailEvidenceRef(item,id)).join("")}</div>`:""}`:""}</div>`}).join("")}</div></section>`}
function drawerEvidence(item){const evidence=item.binding&&item.binding.evidence||[];if(!evidence.length)return `<section class="drawer-section"><h3>证据链</h3><div class="empty-note">当前项目暂无图像证据</div></section>`;const sorted=[...evidence].sort((a,b)=>{const ao=a.order_index==null?9999:Number(a.order_index),bo=b.order_index==null?9999:Number(b.order_index);return ao-bo||(Number(a.timestamp_sec)||0)-(Number(b.timestamp_sec)||0)});return `<section class="drawer-section"><h3>证据链 <span>${sorted.length} 条</span></h3><div class="evidence-grid">${sorted.map(e=>evidenceButtonMarkup(item,e,state.indexOf(item))).join("")}</div></section>`}
function drawerAnalysis(item){if(!isTerminal(item))return "";const profile=item.analysis_profile||{};const tools=(item.analysis_tools||[]).map(tool=>`<span class="tool-chip">${esc(tool.label||tool.tool_id)}</span>`).join("");const features=(profile.complexity_features||[]).map(feature=>esc(feature)).join(" · ");return `<section class="drawer-section"><h3>分析链</h3><div class="tool-list">${tools||`<span class="tool-chip">工具链整理中</span>`}</div><div class="profile-line">实际分析任务 ${profile.actual_analysis_task_count!=null?esc(profile.actual_analysis_task_count):"—"} 个${features?`<br>复杂度特征：${features}`:""}</div></section>`}
function drawerRisks(detail){const risks=[...new Set(detail.risk_boundaries||[])].filter(Boolean);return `<section class="drawer-section"><h3>易混淆与排除</h3>${risks.length?`<ul class="risk-list">${risks.map(risk=>`<li>${esc(risk)}</li>`).join("")}</ul>`:`<div class="empty-note">暂无补充提示</div>`}</section>`}
/* Keep the drawer header explicit about the observed time window and, once
   available, the item's evaluation difficulty. */
function renderDrawer(index){const item=state[index];if(!item)return;const detail=item.detail||{state:detailState(item),sections:[],criterion_count:0};drawerIndex=index;$("drawer-kicker").textContent=`项目 ${item.item_number} · 详细核验`;$("drawer-title").textContent=item.display_name;$("drawer-meta").innerHTML=`<span>${esc(item.status)}</span><span>·</span><span>修订 ${esc(item.binding&&item.binding.revision||0)}</span>`;const time=timeRangeText(item);const difficulty=isTerminal(item)?(item.difficulty_label||labels[item.difficulty]||"—"):"—";const detailLabel=detailStateLabels[detail.state]||detail.state||"等待核验";const header=`<div class="drawer-status"><span class="status-badge ${cls(item.status)}">${esc(item.status)}</span>${isTerminal(item)?`<span class="score-badge ${scoreFor(item)===0?"zero":""}">${scoreFor(item)} / 1 分</span>`:""}</div><div class="drawer-grid"><div class="drawer-stat"><label>现场时间范围</label><strong>${esc(time)}</strong></div><div class="drawer-stat"><label>时间置信度</label><strong>${confidenceText(item.binding&&item.binding.time_confidence)}</strong></div><div class="drawer-stat"><label>详细状态</label><strong>${esc(detailLabel)}</strong></div><div class="drawer-stat"><label>评价难度</label><strong>${esc(difficulty)}</strong></div></div>`;let body=header;if(!isTerminal(item)){body+=drawerSkeleton(item,detail)}else{if(isCompleted(item)&&detail.state==="unlocked"&&detail.high_level_evaluation)body+=`<section class="drawer-section"><h3>项目评价</h3><div class="high-level">${esc(detail.high_level_evaluation)}</div></section>`;if(item.status==="待人工确认"&&detail.unresolved_summary)body+=`<section class="drawer-section"><h3>待确认事项</h3><div class="unresolved">${esc(detail.unresolved_summary)}</div></section>`;if(detail.state==="unlocked")body+=drawerCriteria(item,detail);else body+=drawerSkeleton(item,detail,"逐条核验结果尚未提供。");body+=drawerEvidence(item);body+=drawerAnalysis(item);body+=drawerRisks(detail)}$("drawer-body").innerHTML=body;$("drawer-backdrop").hidden=false;document.body.classList.add("locked");document.querySelectorAll(".detail-trigger").forEach(btn=>btn.setAttribute("aria-expanded",String(Number(btn.dataset.detailIndex)===index)));bindEvidenceInteractions()}
function openDetail(index){current=index;render();renderDrawer(index);setTimeout(()=>$('drawer-close').focus(),0)}
function closeDetail(){drawerIndex=null;$("drawer-backdrop").hidden=true;document.body.classList.remove("locked");document.querySelectorAll(".detail-trigger").forEach(btn=>btn.setAttribute("aria-expanded","false"))}
function findEvidenceFromButton(button){const item=state[Number(button.dataset.itemIndex)];return item?evidenceById(item,button.dataset.evidenceId):null}
function showHover(button,fromKeyboard=false){const evidence=findEvidenceFromButton(button);if(!evidence||(!fromKeyboard&&window.matchMedia("(pointer:coarse)").matches))return;hoverButton=button;const media=$("hover-media");media.innerHTML=evidence.src?`<img src="${evidence.src}" alt="">`:`<div class="preview-placeholder">暂无图像</div>`;$("hover-phase").textContent=`${evidenceLabel(evidence)}${evidence.timestamp?` · ${evidence.timestamp}`:""}`;$("hover-confidence").textContent=confidenceText(evidence.confidence);const preview=$("hover-preview");preview.hidden=false;const rect=button.getBoundingClientRect(),width=Math.min(310,Math.max(120,window.innerWidth-24)),height=Math.min(270,Math.max(150,window.innerHeight-24));preview.style.width=`${width}px`;let left=rect.right+12,top=rect.top;if(left+width>window.innerWidth-12)left=rect.left-width-12;if(top+height>window.innerHeight-12)top=window.innerHeight-height-12;preview.style.left=`${Math.max(12,Math.min(left,Math.max(12,window.innerWidth-width-12)))}px`;preview.style.top=`${Math.max(12,Math.min(top,Math.max(12,window.innerHeight-height-12)))}px`}
function hideHover(){if(hoverTimer)clearTimeout(hoverTimer);hoverTimer=null;hoverButton=null;$("hover-preview").hidden=true}
function openLightbox(button){const evidence=findEvidenceFromButton(button);if(!evidence)return;hideHover();const media=$("lightbox-media");media.innerHTML=evidence.src?`<img class="lightbox-image" src="${evidence.src}" alt="证据大图">`:`<div class="lightbox-placeholder">暂无图像</div>`;$("lightbox-meta").textContent=`${evidenceLabel(evidence)}${evidence.timestamp?` · ${evidence.timestamp}`:""} · 置信度 ${confidenceText(evidence.confidence)}`;$("lightbox").hidden=false;document.body.classList.add("locked");setTimeout(()=>$("lightbox-close").focus(),0)}
function closeLightbox(){$("lightbox").hidden=true;if(drawerIndex===null)document.body.classList.remove("locked")}
function bindEvidenceInteractions(){document.querySelectorAll(".evidence-thumb,.evidence-ref").forEach(button=>{button.onpointerenter=event=>{if(event.pointerType==="touch")return;hoverTimer=setTimeout(()=>showHover(button),120)};button.onpointerleave=hideHover;button.onfocus=()=>{if(window.matchMedia("(pointer:coarse)").matches)return;hoverTimer=setTimeout(()=>showHover(button,true),120)};button.onblur=hideHover;button.onclick=()=>openLightbox(button)})}
$("start").onclick=startEvaluation;$("reset").onclick=resetReport;$("drawer-close").onclick=closeDetail;$("lightbox-close").onclick=closeLightbox;$("drawer-backdrop").onclick=event=>{if(event.target===event.currentTarget)closeDetail()};$("lightbox").onclick=event=>{if(event.target===event.currentTarget)closeLightbox()};document.addEventListener("click",event=>{const detailButton=event.target.closest&&event.target.closest(".detail-trigger");if(detailButton){event.preventDefault();openDetail(Number(detailButton.dataset.detailIndex));return}if(event.target.closest&&event.target.closest(".timeline-btn,.chip,.control,.evidence-thumb,.evidence-ref,.drawer-close,.lightbox-close"))return});document.addEventListener("keydown",event=>{if(event.key==="Escape"){if(!$('lightbox').hidden){closeLightbox();return}if(!$('drawer-backdrop').hidden){closeDetail();return}}if(event.key==="Tab"&&!$('drawer-backdrop').hidden){const focusables=[...$('detail-drawer').querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')];if(!focusables.length)return;const first=focusables[0],last=focusables[focusables.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}});document.querySelectorAll(".chip").forEach(btn=>btn.onclick=()=>{document.querySelectorAll(".chip").forEach(x=>x.classList.remove("active"));btn.classList.add("active");filter=btn.dataset.filter;renderCards();bindEvidenceInteractions()});render();
</script>
</body></html>'''


FORBIDDEN_HTML_MARKERS = (
    "26/26",
    "50/50",
    "标准结果",
    "预置",
    "离线",
    "offline_run_id",
    "scoring_report_summary",
    "source_path",
    "/mnt/shared-storage-user/",
    "口述",
    "口头",
    "音频",
    "字幕",
    "替代",
    "演示模式",
    "已听到",
    "可追溯",
    "同图",
    "仅凭",
    "倒推",
    "内部",
    "mock_live_stream",
    "c475-nested-10-r1",
    "自动播放",
    "下一项",
    "已完成展示",
    "高风险",
    "prefilled_score",
    "prefilled_result",
    "runNext",
    "runOne",
    "平均",
    "必填证据",
    "oral_evidence_analyzer",
    "video_mme_subtitle_analyzer",
)


def render_html(payload: Mapping[str, Any]) -> str:
    public = public_projection(payload)
    data = json.dumps(public, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    output = HTML_TEMPLATE.replace("__REPORT_DATA__", data)
    for marker in FORBIDDEN_HTML_MARKERS:
        if marker in output:
            raise ValueError(f"HTML projection contains forbidden marker: {marker}")
    return output


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成实时证据展示报告")
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser("template", help="写出 8-20 项实时报告模板 JSON")
    template.add_argument("--output", required=True, type=Path)
    template.add_argument("--profiles", type=Path, help="workflow tool profile JSON")
    render = sub.add_parser("render", help="读取 JSON 生成自包含 HTML")
    render.add_argument("--input", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "template":
        _write_json(args.output, template_payload(load_tool_profile(args.profiles) if args.profiles else None))
        print(f"已生成模板 JSON：{args.output}")
        return 0
    payload = _read_json(args.input)
    html_content = render_html(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_content, encoding="utf-8")
    print(f"已生成 HTML：{args.output}（{len(html_content):,} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
