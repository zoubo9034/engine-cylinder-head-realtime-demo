#!/usr/bin/env python3
"""Schema and template helpers for the isolated engine demo report.

This module deliberately contains no scoring implementation.  It defines the
live evidence contract used by the demo UI and validates that evidence is
bound to exactly one item.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from detail_rules import (
    DETAIL_CHECK_STATUSES,
    DETAIL_EVALUATION_STATES,
    DETAIL_RULES,
    DETAIL_RULES_BY_ITEM,
    DETAIL_SCHEMA,
    criterion_map,
    detail_form_for,
)


ITEM_DEFINITIONS = [
    {
        "item_number": 8,
        "item_id": "item_5069",
        "display_name": "第二次预松180°",
        "difficulty": "difficult",
        "target_text": "识别指针式扳手对气缸盖螺栓完成第二次180°预松，并确认动作连续、轮次正确。",
        "queries": ["第二次预松180度 指针扳手", "pointer wrench half turn cylinder head bolt"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["segmentation", "rotation_trace", "angle_card"],
        "evidence_hint": "展示扳手、螺栓目标框与开始—动作中—完成连续帧；轮次使用序列条表达。",
    },
    {
        "item_number": 9,
        "item_id": "cylinder_head",
        "display_name": "将气缸盖放置在垫块上",
        "difficulty": "easy",
        "target_text": "识别双手扶持气缸盖缓慢落到支撑垫块，并确认没有与工作台直接接触。",
        "queries": ["气缸盖 放置 垫块", "cylinder head placed on support pads"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["segmentation", "contact_relation"],
        "evidence_hint": "展示气缸盖与垫块的目标框，并用接触关系图确认落位。",
    },
    {
        "item_number": 10,
        "item_id": "gasket_remove",
        "display_name": "取下气缸垫",
        "difficulty": "easy",
        "target_text": "识别手部与旧气缸垫的接触、抬起和移出结合面，确认气缸垫进入待检区域。",
        "queries": ["取下 旧气缸垫", "lift cylinder gasket from engine block"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["gasket_outline", "before_after_comparison"],
        "evidence_hint": "展示薄片轮廓、手部接触和脱离气缸体前后的对比。",
    },
    {
        "item_number": 11,
        "item_id": "gasket_inspect",
        "display_name": "检查气缸垫",
        "difficulty": "medium",
        "target_text": "近景检查气缸垫的孔位、边缘和表面，并分别观察正面与反面。",
        "queries": ["检查气缸垫 变形 缺失", "inspect cylinder gasket holes edges"],
        "required_slots": ["live_timestamp", "representative_frame", "multi_frame_sequence"],
        "enhanced_slots": ["gasket_detail_segmentation", "inspection_regions", "inspection_result_card", "front_back_labels"],
        "evidence_hint": "使用近景卡片放大孔位、边缘和表面检查区域。",
    },
    {
        "item_number": 12,
        "item_id": "positioning",
        "display_name": "检查定位销",
        "difficulty": "difficult",
        "target_text": "识别气缸体边缘附近两枚金属圆柱定位销，并确认检查动作覆盖两枚目标。",
        "queries": ["检查定位销 金属圆柱", "two silver dowel pins engine block edge"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["segmentation", "pin_1_pin_2_labels", "position_relation"],
        "evidence_hint": "近景同时呈现定位销1、定位销2及各自的检查动作。",
    },
    {
        "item_number": 13,
        "item_id": "clean_head",
        "display_name": "清洁气缸盖",
        "difficulty": "difficult",
        "target_text": "识别白色无纺布与气缸盖下方结合面的直接擦拭接触，并确认主要密封区域被覆盖。",
        "queries": ["清洁气缸盖 结合面 白色无纺布", "white nonwoven cloth wipes cylinder head surface"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["surface_segmentation", "coverage_trace", "before_after_comparison", "contact_detail"],
        "evidence_hint": "突出白色无纺布、气缸盖结合面和擦拭覆盖轨迹。",
    },
    {
        "item_number": 14,
        "item_id": "clean_block",
        "display_name": "清洁气缸体",
        "difficulty": "difficult",
        "target_text": "识别白色无纺布擦拭气缸体上方结合面，并确认气缸孔周边和主要密封区域被覆盖。",
        "queries": ["清洁气缸体 结合面 无纺布", "white cloth cleans engine block sealing surface"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["surface_segmentation", "coverage_trace", "before_after_comparison", "contact_detail"],
        "evidence_hint": "画面突出气缸体上表面、气缸孔周边和无纺布擦拭范围。",
    },
    {
        "item_number": 15,
        "item_id": "clean_gasket",
        "display_name": "清洁气缸垫",
        "difficulty": "medium",
        "target_text": "识别白色无纺布对气缸垫正面和反面的擦拭，并确认清洁发生在安装前。",
        "queries": ["清洁气缸垫 两面 无纺布", "wipe both sides of cylinder gasket"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["double_side_segmentation", "front_back_labels", "clean_state_card", "temporal_order", "contact_detail"],
        "evidence_hint": "按正面擦拭、翻面、反面擦拭三段显示清洁过程。",
    },
    {
        "item_number": 16,
        "item_id": "clean_pins",
        "display_name": "清洁定位销",
        "difficulty": "difficult",
        "target_text": "识别白色无纺布逐一接触两枚金属定位销，并确认没有遗漏任一目标。",
        "queries": ["清洁两枚定位销 无纺布", "wipe two dowel pins with white cloth"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["pin_segmentation", "pin_sequence", "contact_detail"],
        "evidence_hint": "以定位销1→定位销2序列展示逐一清洁和接触区域。",
    },
    {
        "item_number": 17,
        "item_id": "report_gasket",
        "display_name": "报告更换气缸垫",
        "difficulty": "difficult",
        "target_text": "识别安装新气缸垫前的流程停顿和更换确认节点，并确认随后才进入安装阶段。",
        "queries": ["报告更换气缸垫 安装前", "gasket replacement report before installation"],
        "required_slots": ["live_timestamp", "process_node_frame", "temporal_order"],
        "enhanced_slots": ["new_gasket_detection", "node_confirmation_card", "multi_frame_sequence"],
        "evidence_hint": "展示待用垫片、流程节点和安装动作的先后关系。",
    },
    {
        "item_number": 18,
        "item_id": "install_gasket",
        "display_name": "安装气缸垫",
        "difficulty": "medium",
        "target_text": "识别新气缸垫方向、孔位与两枚定位销的匹配，以及平稳落座后的无明显错位状态。",
        "queries": ["安装气缸垫 定位销 孔位", "install cylinder gasket align dowel pins"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["gasket_segmentation", "orientation_card", "hole_pin_relation", "front_back_labels", "temporal_order"],
        "evidence_hint": "按对准—放置—落座三段显示方向和孔位匹配关系。",
    },
    {
        "item_number": 19,
        "item_id": "cylinder_head_bolt",
        "display_name": "报告更换气缸盖螺栓",
        "difficulty": "difficult",
        "target_text": "展示新螺栓待用、流程节点和后续安装动作的先后关系。",
        "queries": ["报告更换气缸盖螺栓 安装前", "new cylinder head bolt report before install"],
        "required_slots": ["live_timestamp", "process_node_frame", "temporal_order"],
        "enhanced_slots": ["new_bolt_detection", "node_confirmation_card", "multi_frame_sequence"],
        "evidence_hint": "展示新螺栓待安装区域、流程节点和后续动作。",
    },
    {
        "item_number": 20,
        "item_id": "install_1st",
        "display_name": "第一次安装预紧",
        "difficulty": "difficult",
        "target_text": "使用扭力扳手按维修手册顺序，依次对1—10号气缸盖螺栓完成第一次安装预紧；本轮预紧扭矩为25 N·m，且不与后续角度拧紧轮次混淆。",
        "queries": ["第一次安装预紧 1-10 25Nm", "torque wrench first tightening ten bolt order"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence", "sequence_order"],
        "enhanced_slots": ["tool_segmentation", "bolt_trace", "torque_parameter_card"],
        "evidence_hint": "按第一次预紧呈现扭力扳手与螺栓接触、1—10号顺序和25 N·m参数；角度拧紧动作另行区分。",
    },
]


SLOT_LABELS = {
    "live_timestamp": "现场时间",
    "representative_frame": "代表性关键帧",
    "object_detection": "目标检测",
    "multi_frame_sequence": "连续帧",
    "segmentation": "语义分割",
    "rotation_trace": "旋转轨迹",
    "angle_card": "角度提示",
    "contact_relation": "接触关系",
    "gasket_outline": "气缸垫轮廓",
    "before_after_comparison": "前后对比",
    "gasket_detail_segmentation": "气缸垫细节",
    "inspection_regions": "检查区域",
    "inspection_result_card": "检查结果卡",
    "pin_1_pin_2_labels": "定位销编号",
    "position_relation": "位置关系",
    "surface_segmentation": "结合面区域",
    "coverage_trace": "擦拭覆盖轨迹",
    "double_side_segmentation": "双面区域",
    "front_back_labels": "正反面标签",
    "clean_state_card": "清洁状态卡",
    "pin_segmentation": "定位销区域",
    "pin_sequence": "定位销顺序",
    "contact_detail": "接触细节",
    "process_node_frame": "流程节点帧",
    "temporal_order": "时序关系",
    "new_gasket_detection": "新气缸垫识别",
    "node_confirmation_card": "节点确认卡",
    "gasket_segmentation": "气缸垫区域",
    "orientation_card": "方向提示",
    "hole_pin_relation": "孔位匹配",
    "new_bolt_detection": "新螺栓识别",
    "representative_frame": "代表性关键帧",
    "tool_segmentation": "工具区域",
    "bolt_trace": "螺栓轨迹",
    "torque_parameter_card": "扭矩提示",
    "sequence_order": "顺序序列",
}


DIFFICULTY_LABELS = {
    "difficult": "困难",
    "medium": "中等",
    "easy": "简单",
}

DEFAULT_PROFILE_PATH = Path(__file__).with_name("workflow_tool_profile_10video.json")


def load_tool_profile(path: Path | None = None) -> dict[str, Any]:
    """Load the checked-in aggregate profile, if one is available."""
    profile_path = path or DEFAULT_PROFILE_PATH
    if not profile_path.is_file():
        return {}
    value = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("workflow tool profile 顶层必须是对象")
    return value


def _slot(slot_id: str, *, required: bool, kind: str = "image") -> dict[str, Any]:
    return {
        "slot_id": slot_id,
        "label": SLOT_LABELS.get(slot_id, slot_id),
        "required": required,
        "kind": kind,
        "status": "empty",
        "evidence": [],
    }


def template_payload(profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a fresh, JSON-serialisable live-only report template."""
    profile_items = dict((profile or load_tool_profile()).get("items", {}) or {})
    items = []
    for order, definition in enumerate(ITEM_DEFINITIONS, start=1):
        item_profile = profile_items.get(definition["item_id"], {}) or {}
        difficulty = str(item_profile.get("difficulty") or definition["difficulty"])
        difficulty_label = str(item_profile.get("difficulty_label") or DIFFICULTY_LABELS[difficulty])
        required = []
        for slot_id in definition["required_slots"]:
            kind = "timestamp" if slot_id == "live_timestamp" else ("sequence" if slot_id in {"multi_frame_sequence", "temporal_order", "sequence_order"} else "image")
            required.append(_slot(slot_id, required=True, kind=kind))
        enhanced = [_slot(slot_id, required=False) for slot_id in definition["enhanced_slots"]]
        items.append({
            "item_number": definition["item_number"],
            "item_id": definition["item_id"],
            "display_name": definition["display_name"],
            "difficulty": difficulty,
            "difficulty_label": difficulty_label,
            "analysis_profile": deepcopy(item_profile.get("analysis_profile", {})),
            "analysis_tools": deepcopy(item_profile.get("analysis_tools", [])),
            # The internal template keeps the rubric snapshot available to
            # auditors.  render_report.public_projection deliberately omits
            # these fields so the audience only sees live analysis.
            "prefilled_standard_text": definition["target_text"],
            "realtime_target": definition["target_text"],
            "retrieval_queries": definition["queries"],
            "required_evidence_slots": required,
            "enhanced_evidence_slots": enhanced,
            "evidence_hint": definition["evidence_hint"],
            "detail_form": detail_form_for(definition["item_id"]),
            "detail_evaluation": {
                "state": "locked",
                "updated_at": None,
                "checks": [],
                "unresolved_summary": "",
            },
            "completion_condition": {
                "required_slot_ids": definition["required_slots"],
                "success_state": "evidence_bound",
                "low_confidence_state": "manual_review",
                "missing_state": "evidence_generating",
            },
            "live_binding": {
                "state": "待开始",
                "revision": 0,
                "changed_slot_ids": [],
                "live_timestamp": None,
                "live_start_sec": None,
                "live_end_sec": None,
                "time_source": "live_stream",
                "time_confidence": None,
                "evidence_explanation": "等待当前视频流中的有效证据。",
                "evidence": [],
            },
            "score": None,
            "score_max": 1,
            "display_order": order,
        })
    return {
        "schema": "realtime-evidence-report/v1",
        "report_id": "engine-cylinder-head-realtime-8-20-v1",
        "title": "发动机气缸盖拆装智能实训分析",
        "source": {
            "offline_run_id": "2026-08-25-core380-engine120-rough29-r5-cpu32-10c",
            "core_version": "core-v1.2.380",
            "engine_version": "engine-v1.2.20",
            "rubric_id": "engine_cylinder_head",
            "rubric_version": "v1",
        },
        "presentation": {
            "audience_mode": "live_only",
            "show_scores": True,
            "score_reveal": "terminal_only",
            "show_source_provenance": False,
            "show_raw_paths": False,
            "initial_state": "正在接入视频流",
            "poll_interval_ms": 650,
            "footer": "本页面展示当前视频流的实时分析过程。证据未完成时不输出结论，低置信度结果保留人工确认状态。",
        },
        "scope": {
            "total_rubric_items": 28,
            "active_item_numbers": [d["item_number"] for d in ITEM_DEFINITIONS],
            "active_item_count": len(ITEM_DEFINITIONS),
            "max_score": len(ITEM_DEFINITIONS),
            "inactive_items_display": "本次演示不展示，不计入现场回填进度",
            "display_mode": "evidence_progress_only",
        },
        "demo_policy": {
            "time_source": "live_stream_only",
            "score_source": "live_evidence_state",
            "score_reveal": "terminal_only",
        },
        "demo_context": {
            "entry_item_number": 8,
            "pre_entry_context": "已由现场流程上下文确认",
            "time_source": "live_stream_only",
        },
        "items": items,
        "events": [],
    }


def _iter_slots(item: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield from item.get("required_evidence_slots", []) or []
    yield from item.get("enhanced_evidence_slots", []) or []


def _iter_slot_evidence(item: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield evidence attached to both required and enhanced slots."""
    for slot in _iter_slots(item):
        if not isinstance(slot, Mapping):
            continue
        for evidence in slot.get("evidence", []) or []:
            if isinstance(evidence, Mapping):
                yield evidence


LIVE_STATES = {
    "待开始",
    "已定位",
    "证据生成中",
    "证据已绑定",
    "已完成评分",
    "待人工确认",
}
TERMINAL_LIVE_STATES = {"证据已绑定", "已完成评分", "待人工确认"}


def _validate_detail_form(
    item: Mapping[str, Any],
    slot_ids: set[str],
    errors: list[str],
) -> set[str]:
    item_id = str(item.get("item_id") or "")
    rule = DETAIL_RULES_BY_ITEM.get(item_id)
    if rule is None:
        errors.append(f"{item_id}: missing detailed rule")
        return set()
    form = item.get("detail_form")
    canonical = rule["detail_form"]
    if not isinstance(form, Mapping):
        errors.append(f"{item_id}: detail_form must be an object")
        return set()
    # The rule text is immutable in the live contract.  A recogniser may only
    # send evaluation results, never replace the rubric shown in the drawer.
    if dict(form) != canonical:
        errors.append(f"{item_id}: detail_form does not match the registered rule")
    if form.get("schema") != DETAIL_SCHEMA:
        errors.append(f"{item_id}: detail_form.schema must be {DETAIL_SCHEMA}")
    criteria = form.get("criteria")
    if not isinstance(criteria, list):
        errors.append(f"{item_id}: detail_form.criteria must be a list")
        return set()
    expected = {
        str(criterion.get("criterion_id")): criterion
        for criterion in canonical.get("criteria", [])
    }
    actual_ids: list[str] = []
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            errors.append(f"{item_id}: criterion must be an object")
            continue
        criterion_id = str(criterion.get("criterion_id") or "")
        actual_ids.append(criterion_id)
        if criterion_id not in expected:
            errors.append(f"{item_id}: unknown criterion {criterion_id}")
            continue
        if not set(str(value) for value in criterion.get("evidence_slot_ids", []) or []).issubset(slot_ids):
            errors.append(f"{item_id}/{criterion_id}: evidence slot is not owned by item")
        if criterion.get("required") is not True:
            errors.append(f"{item_id}/{criterion_id}: required must be true")
        if criterion.get("demo_gate") != "required":
            errors.append(f"{item_id}/{criterion_id}: demo_gate must be required")
    if len(actual_ids) != len(set(actual_ids)):
        errors.append(f"{item_id}: duplicate criterion ID")
    if actual_ids != list(expected):
        errors.append(f"{item_id}: detail criteria do not match the registered rule")

    sections = form.get("sections")
    if not isinstance(sections, list):
        errors.append(f"{item_id}: detail_form.sections must be a list")
    else:
        expected_sections = canonical.get("sections", [])
        if sections != expected_sections:
            errors.append(f"{item_id}: detail sections do not match the registered rule")
    return set(expected)


def _validate_detail_evaluation(
    item: Mapping[str, Any],
    criterion_ids: set[str],
    evidence_ids: set[str],
    errors: list[str],
) -> None:
    item_id = str(item.get("item_id") or "")
    evaluation = item.get("detail_evaluation")
    if not isinstance(evaluation, Mapping):
        errors.append(f"{item_id}: detail_evaluation must be an object")
        return
    state = str(evaluation.get("state") or "")
    if state not in DETAIL_EVALUATION_STATES:
        errors.append(f"{item_id}: unknown detail evaluation state {state}")
    updated_at = evaluation.get("updated_at")
    if updated_at is not None and not isinstance(updated_at, str):
        errors.append(f"{item_id}: detail_evaluation.updated_at must be a string or null")
    checks = evaluation.get("checks")
    if not isinstance(checks, list):
        errors.append(f"{item_id}: detail_evaluation.checks must be a list")
        return
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, Mapping):
            errors.append(f"{item_id}: detail check must be an object")
            continue
        criterion_id = str(check.get("criterion_id") or "")
        if criterion_id not in criterion_ids:
            errors.append(f"{item_id}: detail check references unknown criterion {criterion_id}")
        if criterion_id in seen:
            errors.append(f"{item_id}: duplicate detail check {criterion_id}")
        seen.add(criterion_id)
        status = str(check.get("status") or "")
        if status not in DETAIL_CHECK_STATUSES:
            errors.append(f"{item_id}/{criterion_id}: unknown detail check status {status}")
        confidence = check.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
                errors.append(f"{item_id}/{criterion_id}: confidence must be between 0 and 1")
        refs = check.get("evidence_ids", [])
        if not isinstance(refs, list):
            errors.append(f"{item_id}/{criterion_id}: evidence_ids must be a list")
        else:
            for evidence_id in refs:
                if str(evidence_id) not in evidence_ids:
                    errors.append(f"{item_id}/{criterion_id}: evidence ID is not owned by item")
        for field in ("observation", "reason"):
            value = check.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"{item_id}/{criterion_id}: {field} must be a string")
    unresolved = evaluation.get("unresolved_summary")
    if unresolved is not None and not isinstance(unresolved, str):
        errors.append(f"{item_id}: unresolved_summary must be a string")


def validate_report(payload: Mapping[str, Any], *, allow_mock: bool = True) -> list[str]:
    """Return validation errors; callers decide whether to raise them."""
    errors: list[str] = []
    if payload.get("schema") != "realtime-evidence-report/v1":
        errors.append("schema must be realtime-evidence-report/v1")
    if payload.get("presentation", {}).get("show_scores") is not True:
        errors.append("presentation.show_scores must be true")
    if payload.get("presentation", {}).get("score_reveal") != "terminal_only":
        errors.append("presentation.score_reveal must be terminal_only")
    if payload.get("presentation", {}).get("show_source_provenance") is not False:
        errors.append("presentation.show_source_provenance must be false")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != len(ITEM_DEFINITIONS):
        errors.append(f"items must contain exactly {len(ITEM_DEFINITIONS)} entries")
        return errors
    expected = [(d["item_number"], d["item_id"]) for d in ITEM_DEFINITIONS]
    actual = [(item.get("item_number"), item.get("item_id")) for item in items if isinstance(item, Mapping)]
    if actual != expected:
        errors.append(f"items must follow exact 8-20 order: expected {expected}, got {actual}")
    if payload.get("scope", {}).get("max_score") != len(ITEM_DEFINITIONS):
        errors.append(f"scope.max_score must be {len(ITEM_DEFINITIONS)}")
    if len(DETAIL_RULES) != len(ITEM_DEFINITIONS) or set(DETAIL_RULES_BY_ITEM) != {d["item_id"] for d in ITEM_DEFINITIONS}:
        errors.append("registered detailed rules must match the 13 active items")
    rule_numbers = [rule.get("item_number") for rule in DETAIL_RULES]
    if len(rule_numbers) != len(set(rule_numbers)):
        errors.append("registered detailed rules contain duplicate item numbers")
    expected_rule_numbers = {d["item_id"]: d["item_number"] for d in ITEM_DEFINITIONS}
    for rule in DETAIL_RULES:
        rule_item_id = str(rule.get("item_id") or "")
        if rule.get("item_number") != expected_rule_numbers.get(rule_item_id):
            errors.append(f"detailed rule item number mismatch: {rule_item_id}")
    criterion_ids = [
        str(criterion.get("criterion_id"))
        for rule in DETAIL_RULES
        for criterion in rule.get("detail_form", {}).get("criteria", [])
        if isinstance(criterion, Mapping)
    ]
    if len(criterion_ids) != len(set(criterion_ids)):
        errors.append("registered detailed rules contain duplicate criterion IDs")
    used_paths: dict[str, str] = {}
    used_evidence_ids: dict[str, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            errors.append("item must be an object")
            continue
        required_ids = set(item.get("completion_condition", {}).get("required_slot_ids", []) or [])
        slots = list(_iter_slots(item))
        slot_ids = [str(slot.get("slot_id")) for slot in slots if isinstance(slot, Mapping)]
        if len(slot_ids) != len(slots):
            errors.append(f"{item.get('item_id')}: evidence slot must be an object")
        if len(slot_ids) != len(set(slot_ids)):
            errors.append(f"{item.get('item_id')}: duplicate evidence slot")
        if not required_ids.issubset(set(slot_ids)):
            errors.append(f"{item.get('item_id')}: completion slots missing from slot definitions")
        difficulty = str(item.get("difficulty") or "")
        if difficulty not in DIFFICULTY_LABELS:
            errors.append(f"{item.get('item_id')}: unknown difficulty {difficulty}")
        if str(item.get("difficulty_label") or "") != DIFFICULTY_LABELS.get(difficulty, ""):
            errors.append(f"{item.get('item_id')}: difficulty label mismatch")
        if item.get("score_max") != 1:
            errors.append(f"{item.get('item_id')}: score_max must be 1")
        binding = item.get("live_binding", {}) or {}
        if not isinstance(binding, Mapping):
            errors.append(f"{item.get('item_id')}: live_binding must be an object")
            binding = {}
        item_evidence_ids: set[str] = set()
        item_evidence_records: list[Mapping[str, Any]] = []
        binding_records = list(binding.get("evidence", []) or [])
        slot_records = list(_iter_slot_evidence(item))
        for is_binding, evidence in [(True, entry) for entry in binding_records] + [(False, entry) for entry in slot_records]:
            if not isinstance(evidence, Mapping):
                errors.append(f"{item.get('item_id')}: evidence must be an object")
                continue
            item_evidence_records.append(evidence)
            path = str(evidence.get("source_path") or "")
            if path:
                owner = used_paths.setdefault(path, str(item.get("item_id")))
                if owner != str(item.get("item_id")):
                    errors.append(f"evidence path reused across items: {path}")
            evidence_id = str(evidence.get("evidence_id") or "")
            if evidence_id:
                if is_binding and evidence_id in item_evidence_ids:
                    errors.append(f"{item.get('item_id')}: duplicate evidence ID {evidence_id}")
                item_evidence_ids.add(evidence_id)
                owner = used_evidence_ids.setdefault(evidence_id, str(item.get("item_id")))
                if owner != str(item.get("item_id")):
                    errors.append(f"evidence ID reused across items: {evidence_id}")
        # A slot reference is allowed only when the same record is part of the
        # item's live evidence set.  This catches a detail result that points
        # at another item's frame, even if the frame was omitted from binding.
        for evidence in item_evidence_records:
            evidence_item = evidence.get("item_id")
            if evidence_item not in (None, "", item.get("item_id")):
                errors.append(f"{item.get('item_id')}: evidence item_id does not match owner")
        criterion_ids = _validate_detail_form(item, set(slot_ids), errors)
        _validate_detail_evaluation(item, criterion_ids, item_evidence_ids, errors)
        state = str(binding.get("state") or "")
        if state and state not in LIVE_STATES:
            errors.append(f"{item.get('item_id')}: unknown live state {state}")
        expected_score = {"证据已绑定": 1, "已完成评分": 1, "待人工确认": 0}.get(state)
        score = item.get("score")
        if expected_score is None and score is not None:
            errors.append(f"{item.get('item_id')}: non-terminal item must not have a score")
        elif expected_score is not None and score != expected_score:
            errors.append(f"{item.get('item_id')}: score does not match state {state}")
    # Replay events carry complete item patches.  Validate their detailed
    # evidence as well, so an event cannot smuggle a reference belonging to a
    # different project into the browser during playback.
    # Event patches and the current snapshot share the same evidence
    # namespace.  Seed the replay maps with the snapshot owners so a frame or
    # ID cannot be reused by another item only by moving it into an event.
    event_paths: dict[str, str] = dict(used_paths)
    event_evidence_ids: dict[str, str] = dict(used_evidence_ids)
    for event in payload.get("events", []) or []:
        if not isinstance(event, Mapping):
            errors.append("event must be an object")
            continue
        event_item_id = str(event.get("item_id") or "")
        if event_item_id and event_item_id not in DETAIL_RULES_BY_ITEM:
            errors.append(f"event {event.get('event_id')}: unknown item_id {event_item_id}")
        event_state = str(event.get("final_state") or "")
        if event_state and event_state not in LIVE_STATES:
            errors.append(f"event {event.get('event_id')}: unknown final_state {event_state}")
        patch = event.get("item_patch")
        if not isinstance(patch, Mapping):
            # A compact event may carry only an envelope and references to
            # evidence already present in the current snapshot.  It still
            # must not smuggle an unknown or another item's evidence ID.
            declared_event_ids = event.get("evidence_ids", [])
            if not isinstance(declared_event_ids, list):
                errors.append(f"event {event.get('event_id')}: evidence_ids must be a list")
            else:
                for evidence_id in declared_event_ids:
                    owner = event_evidence_ids.get(str(evidence_id))
                    if owner != event_item_id:
                        errors.append(f"event {event.get('event_id')}: evidence ID is not owned by event item")
            continue
        # Compact replay patches may carry the owner only on the event
        # envelope.  Use that owner for validation while still checking an
        # explicit patch item_id when one is supplied.
        patch_item_id = str(patch.get("item_id") or event_item_id)
        if patch_item_id not in DETAIL_RULES_BY_ITEM:
            errors.append(f"event {event.get('event_id')}: unknown item_patch item_id {patch_item_id}")
        if event_item_id and patch_item_id != event_item_id:
            errors.append(f"event {event.get('event_id')}: item_patch.item_id does not match event item")
        expected_number = expected_rule_numbers.get(patch_item_id)
        if "item_number" in patch and patch.get("item_number") != expected_number:
            errors.append(f"event {event.get('event_id')}: item_patch.item_number does not match item")
        patch_binding = patch.get("live_binding", {})
        if isinstance(patch_binding, Mapping):
            patch_state = str(patch_binding.get("state") or "")
            if patch_state and patch_state not in LIVE_STATES:
                errors.append(f"event {event.get('event_id')}: unknown item_patch live state {patch_state}")
            if event_state and patch_state and event_state != patch_state:
                errors.append(f"event {event.get('event_id')}: final_state does not match item_patch state")
            expected_patch_score = {"证据已绑定": 1, "已完成评分": 1, "待人工确认": 0}.get(patch_state)
            if "score" in patch:
                if expected_patch_score is None and patch.get("score") is not None:
                    errors.append(f"event {event.get('event_id')}: non-terminal item_patch must not have a score")
                elif expected_patch_score is not None and patch.get("score") != expected_patch_score:
                    errors.append(f"event {event.get('event_id')}: item_patch score does not match state")
        patch_slots = list(_iter_slots(patch))
        patch_slot_ids = {str(slot.get("slot_id")) for slot in patch_slots if isinstance(slot, Mapping)}
        # A patch may update only the detail result while referring to an
        # evidence frame already bound in the current snapshot (or in an
        # earlier replay event for the same item).  Carry those owned IDs
        # forward; IDs owned by another item are still rejected below.
        patch_ids: set[str] = {
            evidence_id
            for evidence_id, owner in event_evidence_ids.items()
            if owner == patch_item_id
        }
        patch_records = list((patch.get("live_binding", {}) or {}).get("evidence", []) or []) if isinstance(patch.get("live_binding", {}), Mapping) else []
        patch_records += list(_iter_slot_evidence(patch))
        for evidence in patch_records:
            if not isinstance(evidence, Mapping):
                continue
            evidence_item = evidence.get("item_id")
            if evidence_item not in (None, "", patch_item_id):
                errors.append(f"event {event.get('event_id')}: evidence item_id does not match item patch")
            path = str(evidence.get("source_path") or "")
            if path:
                owner = event_paths.setdefault(path, patch_item_id)
                if owner != patch_item_id:
                    errors.append(f"event evidence path reused across items: {path}")
            evidence_id = str(evidence.get("evidence_id") or "")
            if evidence_id:
                patch_ids.add(evidence_id)
                owner = event_evidence_ids.setdefault(evidence_id, patch_item_id)
                if owner != patch_item_id:
                    errors.append(f"event evidence ID reused across items: {evidence_id}")
        declared_event_ids = event.get("evidence_ids", [])
        if not isinstance(declared_event_ids, list):
            errors.append(f"event {event.get('event_id')}: evidence_ids must be a list")
        else:
            for evidence_id in declared_event_ids:
                if str(evidence_id) not in patch_ids:
                    errors.append(f"event {event.get('event_id')}: evidence ID is not owned by item patch")
        # Older clients may enqueue a small binding-only patch.  Full replay
        # patches carry the immutable form and receive complete validation;
        # binding-only patches are still checked for item/evidence ownership.
        if "detail_form" in patch:
            criterion_ids = _validate_detail_form(patch, patch_slot_ids, errors)
            _validate_detail_evaluation(patch, criterion_ids, patch_ids, errors)
        elif "detail_evaluation" in patch:
            criterion_ids = set(criterion_map(patch_item_id)) if patch_item_id in DETAIL_RULES_BY_ITEM else set()
            _validate_detail_evaluation(patch, criterion_ids, patch_ids, errors)
    if not allow_mock and payload.get("demo_mode"):
        errors.append("mock payload is not allowed")
    return errors


def validated_copy(payload: Mapping[str, Any], *, allow_mock: bool = True) -> dict[str, Any]:
    errors = validate_report(payload, allow_mock=allow_mock)
    if errors:
        raise ValueError("报告校验失败：" + "；".join(errors))
    return deepcopy(dict(payload))
