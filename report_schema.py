#!/usr/bin/env python3
"""Schema and template helpers for the isolated engine demo report.

This module deliberately contains no scoring implementation.  It defines the
live evidence contract used by the demo UI and validates that evidence is
bound to exactly one item.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping


ITEM_DEFINITIONS = [
    {
        "item_number": 8,
        "item_id": "item_5069",
        "display_name": "第二次预松180°",
        "difficulty": "high",
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
        "difficulty": "stable",
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
        "difficulty": "stable",
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
        "target_text": "识别对气缸垫孔位、边缘和表面状态的近景检查，保持正反面观察关系可追溯。",
        "queries": ["检查气缸垫 变形 缺失", "inspect cylinder gasket holes edges"],
        "required_slots": ["live_timestamp", "representative_frame", "multi_frame_sequence"],
        "enhanced_slots": ["gasket_detail_segmentation", "inspection_regions", "inspection_result_card"],
        "evidence_hint": "使用近景卡片放大孔位、边缘和表面检查区域。",
    },
    {
        "item_number": 12,
        "item_id": "positioning",
        "display_name": "检查定位销",
        "difficulty": "high",
        "target_text": "识别气缸体边缘附近两枚金属圆柱定位销，并确认检查动作覆盖两枚目标。",
        "queries": ["检查定位销 金属圆柱", "two silver dowel pins engine block edge"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["segmentation", "pin_1_pin_2_labels", "position_relation"],
        "evidence_hint": "近景同时标出定位销1和定位销2，避免用单个小目标代替两枚检查。",
    },
    {
        "item_number": 13,
        "item_id": "clean_head",
        "display_name": "清洁气缸盖",
        "difficulty": "high",
        "target_text": "识别白色无纺布与气缸盖下方结合面的直接擦拭接触，并确认主要密封区域被覆盖。",
        "queries": ["清洁气缸盖 结合面 白色无纺布", "white nonwoven cloth wipes cylinder head surface"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["surface_segmentation", "coverage_trace", "before_after_comparison"],
        "evidence_hint": "突出白色无纺布、气缸盖结合面和擦拭覆盖轨迹。",
    },
    {
        "item_number": 14,
        "item_id": "clean_block",
        "display_name": "清洁气缸体",
        "difficulty": "high",
        "target_text": "识别白色无纺布擦拭气缸体上方结合面，并确认气缸孔周边和主要密封区域被覆盖。",
        "queries": ["清洁气缸体 结合面 无纺布", "white cloth cleans engine block sealing surface"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["surface_segmentation", "coverage_trace", "before_after_comparison"],
        "evidence_hint": "目标必须绑定气缸体上表面，避免将气缸盖清洁画面混用。",
    },
    {
        "item_number": 15,
        "item_id": "clean_gasket",
        "display_name": "清洁气缸垫",
        "difficulty": "medium",
        "target_text": "识别白色无纺布对气缸垫正面和反面的擦拭，并确认清洁发生在安装前。",
        "queries": ["清洁气缸垫 两面 无纺布", "wipe both sides of cylinder gasket"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["double_side_segmentation", "front_back_labels", "clean_state_card"],
        "evidence_hint": "正面、翻面、反面三段证据分开显示，不以翻面动作本身代替清洁接触。",
    },
    {
        "item_number": 16,
        "item_id": "clean_pins",
        "display_name": "清洁定位销",
        "difficulty": "high",
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
        "difficulty": "high",
        "target_text": "识别安装新气缸垫前的流程停顿和更换确认节点，并确认随后才进入安装阶段。",
        "queries": ["报告更换气缸垫 安装前", "gasket replacement report before installation"],
        "required_slots": ["live_timestamp", "process_node_frame", "temporal_order"],
        "enhanced_slots": ["new_gasket_detection", "node_confirmation_card"],
        "evidence_hint": "演示模式用视觉停顿、下一工序前置和流程节点确认替代口述门槛。",
    },
    {
        "item_number": 18,
        "item_id": "install_gasket",
        "display_name": "安装气缸垫",
        "difficulty": "medium",
        "target_text": "识别新气缸垫方向、孔位与两枚定位销的匹配，以及平稳落座后的无明显错位状态。",
        "queries": ["安装气缸垫 定位销 孔位", "install cylinder gasket align dowel pins"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence"],
        "enhanced_slots": ["gasket_segmentation", "orientation_card", "hole_pin_relation"],
        "evidence_hint": "按对准—放置—落座三段显示方向和孔位匹配关系。",
    },
    {
        "item_number": 19,
        "item_id": "cylinder_head_bolt",
        "display_name": "报告更换气缸盖螺栓",
        "difficulty": "high",
        "target_text": "识别安装气缸盖螺栓前的新螺栓流程节点，避免将安装后的补充说明误判为事前报告。",
        "queries": ["报告更换气缸盖螺栓 安装前", "new cylinder head bolt report before install"],
        "required_slots": ["live_timestamp", "process_node_frame", "temporal_order"],
        "enhanced_slots": ["new_bolt_detection", "node_confirmation_card"],
        "evidence_hint": "展示待安装区域和安装前节点，不生成口述文本。",
    },
    {
        "item_number": 20,
        "item_id": "install_1st",
        "display_name": "第一次安装预紧",
        "difficulty": "high",
        "target_text": "使用扭力扳手按维修手册顺序，依次对1—10号气缸盖螺栓完成第一次安装预紧；本轮预紧扭矩为25 N·m，且不与后续角度拧紧轮次混淆。",
        "queries": ["第一次安装预紧 1-10 25Nm", "torque wrench first tightening ten bolt order"],
        "required_slots": ["live_timestamp", "representative_frame", "object_detection", "multi_frame_sequence", "sequence_order"],
        "enhanced_slots": ["tool_segmentation", "bolt_trace", "torque_parameter_card"],
        "evidence_hint": "完成后展示扭力扳手与螺栓接触、1—10号顺序和25 N·m参数；角度拧紧轮次不得混入本项。",
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


def _slot(slot_id: str, *, required: bool, kind: str = "image") -> dict[str, Any]:
    return {
        "slot_id": slot_id,
        "label": SLOT_LABELS.get(slot_id, slot_id),
        "required": required,
        "kind": kind,
        "status": "empty",
        "evidence": [],
    }


def template_payload() -> dict[str, Any]:
    """Return a fresh, JSON-serialisable live-only report template."""
    items = []
    for order, definition in enumerate(ITEM_DEFINITIONS, start=1):
        required = []
        for slot_id in definition["required_slots"]:
            kind = "timestamp" if slot_id == "live_timestamp" else ("sequence" if slot_id in {"multi_frame_sequence", "temporal_order", "sequence_order"} else "image")
            required.append(_slot(slot_id, required=True, kind=kind))
        enhanced = [_slot(slot_id, required=False) for slot_id in definition["enhanced_slots"]]
        items.append({
            "item_number": definition["item_number"],
            "item_id": definition["item_id"],
            "display_name": definition["display_name"],
            "difficulty": definition["difficulty"],
            # The internal template keeps the rubric snapshot available to
            # auditors.  render_report.public_projection deliberately omits
            # these fields so the audience only sees live analysis.
            "prefilled_score": 2,
            "prefilled_standard_text": definition["target_text"],
            "realtime_target": definition["target_text"],
            "retrieval_queries": definition["queries"],
            "required_evidence_slots": required,
            "enhanced_evidence_slots": enhanced,
            "evidence_hint": definition["evidence_hint"],
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
            "display_order": order,
            "oral_policy": {
                "rubric_preserved": True,
                "demo_gate": "disabled",
                "replacement": "流程节点确认与视觉时序证据",
            },
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
            "show_scores": False,
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
            "display_score": "26/26",
            "full_rubric_score": "50",
            "inactive_items_display": "本次演示不展示，不计入现场回填进度",
            "display_mode": "evidence_progress_only",
        },
        "demo_policy": {
            "time_source": "live_stream_only",
            "score_source": "offline_standard_report",
            "score_reveal": "required_evidence_bound",
            "oral_gate": "disabled_for_demo",
            "oral_requirement_preserved_in_rubric": True,
            "oral_replacement": "流程节点确认与视觉时序证据",
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


def validate_report(payload: Mapping[str, Any], *, allow_mock: bool = True) -> list[str]:
    """Return validation errors; callers decide whether to raise them."""
    errors: list[str] = []
    if payload.get("schema") != "realtime-evidence-report/v1":
        errors.append("schema must be realtime-evidence-report/v1")
    if payload.get("presentation", {}).get("show_scores") is not False:
        errors.append("presentation.show_scores must be false")
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
    used_paths: dict[str, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            errors.append("item must be an object")
            continue
        required_ids = set(item.get("completion_condition", {}).get("required_slot_ids", []) or [])
        slots = list(_iter_slots(item))
        slot_ids = [str(slot.get("slot_id")) for slot in slots]
        if len(slot_ids) != len(set(slot_ids)):
            errors.append(f"{item.get('item_id')}: duplicate evidence slot")
        if not required_ids.issubset(set(slot_ids)):
            errors.append(f"{item.get('item_id')}: completion slots missing from slot definitions")
        binding = item.get("live_binding", {}) or {}
        for evidence in binding.get("evidence", []) or []:
            if not isinstance(evidence, Mapping):
                errors.append(f"{item.get('item_id')}: evidence must be an object")
                continue
            path = str(evidence.get("source_path") or "")
            if path:
                owner = used_paths.setdefault(path, str(item.get("item_id")))
                if owner != str(item.get("item_id")):
                    errors.append(f"evidence path reused across items: {path}")
        state = str(binding.get("state") or "")
        if state and state not in {"待开始", "已定位", "证据生成中", "证据已绑定", "已完成评分", "待人工确认"}:
            errors.append(f"{item.get('item_id')}: unknown live state {state}")
    if not allow_mock and payload.get("demo_mode"):
        errors.append("mock payload is not allowed")
    return errors


def validated_copy(payload: Mapping[str, Any], *, allow_mock: bool = True) -> dict[str, Any]:
    errors = validate_report(payload, allow_mock=allow_mock)
    if errors:
        raise ValueError("报告校验失败：" + "；".join(errors))
    return deepcopy(dict(payload))
