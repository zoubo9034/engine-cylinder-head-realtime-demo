#!/usr/bin/env python3
"""纯视觉详细核验规则。

基础评分项定义留在 :mod:`report_schema`，本模块只维护抽屉需要的解释层。
规则文字面向现场查看者，避免暴露模型实现、数据来源或审计过程。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DETAIL_SCHEMA = "realtime-detail-form/v1"
DETAIL_EVALUATION_STATES = {"locked", "analyzing", "unlocked", "unavailable"}
DETAIL_CHECK_STATUSES = {
    "pending",
    "confirmed",
    "not_confirmed",
    "manual_review",
    # Kept for compatibility with an upstream recogniser.  No rule in this
    # visual-only demo emits it.
    "demo_disabled",
}
SECTION_DEFINITIONS = (
    ("object", "对象识别"),
    ("action", "动作过程"),
    ("timing", "时序关系"),
    ("result", "完成状态"),
)


def _criterion(
    criterion_id: str,
    group: str,
    label: str,
    basis: str,
    evidence_slot_ids: list[str],
    boundary: str,
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "group": group,
        "label": label,
        "basis": basis,
        "evidence_slot_ids": list(evidence_slot_ids),
        "required": required,
        "demo_gate": "required",
        "boundary": boundary,
    }


def _form(criteria: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = {label: [] for _, label in SECTION_DEFINITIONS}
    for criterion in criteria:
        grouped.setdefault(str(criterion["group"]), []).append(str(criterion["criterion_id"]))
    sections = [
        {
            "section_id": section_id,
            "label": label,
            "criterion_ids": grouped.get(label, []),
        }
        for section_id, label in SECTION_DEFINITIONS
    ]
    return {
        "schema": DETAIL_SCHEMA,
        "summary": "本项目从对象、动作、时序和完成状态四个方面进行核验。",
        "sections": sections,
        "criteria": criteria,
    }


def _rule(item_number: int, item_id: str, criteria: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "item_number": item_number,
        "item_id": item_id,
        "detail_form": _form(criteria),
    }


# The wording is deliberately short and visual.  Keep criterion IDs stable:
# realtime recognisers use them when sending detail_evaluation.checks.
DETAIL_RULES = [
    _rule(
        8,
        "item_5069",
        [
            _criterion("wrench_bolt_identity", "对象识别", "扳手与螺栓", "画面中能辨认指针式扳手和气缸盖螺栓。", ["object_detection", "segmentation"], "关注目标工具和目标螺栓，区分相邻零件。"),
            _criterion("wrench_head_contact", "动作过程", "接触与旋转", "扳手头部套住螺栓并持续转动。", ["object_detection", "rotation_trace"], "观察扳手头部和螺栓的接触位置。"),
            _criterion("second_round_angle", "时序关系", "第二次轮次与角度", "画面能显示第二次预松轮次，以及一次 180°或两次 90°的转动关系。", ["multi_frame_sequence", "angle_card", "rotation_trace"], "本轮动作与其他角度轮次区分清楚。"),
            _criterion("continuous_rotation_sequence", "完成状态", "连续动作", "画面连续呈现开始、动作中和完成三个阶段，动作过程不跳步。", ["multi_frame_sequence"], "三个阶段应按动作先后自然衔接。"),
        ],
    ),
    _rule(
        9,
        "cylinder_head",
        [
            _criterion("two_hand_support", "动作过程", "双手扶持", "双手扶住气缸盖并控制下降。", ["object_detection", "multi_frame_sequence"], "观察双手、气缸盖和下降动作的关系。"),
            _criterion("pad_under_head", "对象识别", "垫块位置", "垫块位于气缸盖下方并形成支撑面。", ["object_detection", "contact_relation"], "区分垫块、工作台和其他支撑物。"),
            _criterion("stable_pad_support", "完成状态", "支撑关系", "气缸盖落位后由垫块稳定托住，工作台与气缸盖之间保持可见间隙，没有直接接触工作台。", ["contact_relation", "representative_frame"], "观察落位后的支撑关系和气缸盖姿态。"),
            _criterion("placement_sequence", "时序关系", "放置过程", "画面依次呈现抬起、移动、下降和落位。", ["multi_frame_sequence"], "抬起、移动、下降和落位四个阶段前后连贯。"),
        ],
    ),
    _rule(
        10,
        "gasket_remove",
        [
            _criterion("old_gasket_identity", "对象识别", "旧气缸垫", "画面中能辨认待取下的旧气缸垫。", ["object_detection", "gasket_outline"], "区分气缸垫、气缸盖和气缸体。"),
            _criterion("hand_edge_contact", "动作过程", "边缘接触", "手部接触薄片边缘并将其抬起。", ["object_detection", "multi_frame_sequence"], "观察手部与薄片边缘的接触位置。"),
            _criterion("lift_clear_separation", "时序关系", "抬起并脱离", "气缸垫从气缸体结合面抬起并完全脱离。", ["multi_frame_sequence", "before_after_comparison"], "关注结合面和气缸垫之间的距离变化。"),
            _criterion("inspection_area_transfer", "完成状态", "移至待检区", "分开后将气缸垫移动到待检区域。", ["multi_frame_sequence", "gasket_outline"], "观察取下后的移动方向和落点。"),
        ],
    ),
    _rule(
        11,
        "gasket_inspect",
        [
            _criterion("gasket_close_view", "对象识别", "近景对象", "近景画面能够辨认气缸垫整体。", ["representative_frame", "gasket_detail_segmentation"], "近景清楚呈现孔位、边缘和表面，而不是只看到远景或清洁动作。"),
            _criterion("hole_region_check", "动作过程", "孔位检查", "检查画面覆盖气缸垫的孔位区域。", ["inspection_regions", "gasket_detail_segmentation"], "关注孔位边缘和排列情况。"),
            _criterion("edge_region_check", "动作过程", "边缘检查", "检查画面覆盖气缸垫的外缘和内缘。", ["inspection_regions", "gasket_detail_segmentation"], "关注边缘是否完整清晰。"),
            _criterion("surface_condition_check", "完成状态", "表面状态", "画面中能观察表面并记录变形、缺失或破损情况。", ["inspection_regions", "inspection_result_card"], "表面近景清晰呈现变形、缺失或破损情况。"),
            _criterion("front_back_observation", "时序关系", "正反面观察", "正面和反面都出现清晰观察画面，观察完成后再进入清洁准备。", ["front_back_labels", "multi_frame_sequence"], "两面分别呈现清晰的观察过程。"),
        ],
    ),
    _rule(
        12,
        "positioning",
        [
            _criterion("two_pin_identity", "对象识别", "两枚定位销", "画面中能辨认气缸体边缘的两枚金属圆柱定位销。", ["object_detection", "pin_1_pin_2_labels"], "将两枚定位销与周围孔位和零件区分开。"),
            _criterion("pin_one_close_view", "动作过程", "定位销 1", "定位销 1 进入清晰近景。", ["pin_1_pin_2_labels", "segmentation"], "保持定位销 1 的位置和外观清楚。"),
            _criterion("pin_two_close_view", "动作过程", "定位销 2", "定位销 2 进入清晰近景。", ["pin_1_pin_2_labels", "segmentation"], "保持定位销 2 的位置和外观清楚。"),
            _criterion("pin_check_relation", "时序关系", "逐枚检查", "手指或手部对两枚定位销分别进行检查。", ["multi_frame_sequence", "position_relation"], "分别观察两枚目标的检查动作。"),
            _criterion("pin_condition", "完成状态", "位置与外观", "两枚定位销位置清楚、姿态前后一致，表面没有明显损伤。", ["position_relation", "representative_frame"], "两枚定位销的位置、姿态和表面状态分别清楚呈现。"),
        ],
    ),
    _rule(
        13,
        "clean_head",
        [
            _criterion("head_mating_surface_identity", "对象识别", "气缸盖结合面", "画面能辨认气缸盖下方结合面，并出现燃烧室/气门侧特征。", ["object_detection", "surface_segmentation"], "区分气缸盖下方与气缸体上方表面。"),
            _criterion("white_nonwoven_contact", "动作过程", "布料接触", "白色无纺布直接接触结合面。", ["object_detection", "contact_detail"], "关注布料与目标表面的接触区域。"),
            _criterion("head_seal_coverage", "动作过程", "密封区域覆盖", "擦拭覆盖主要密封区域和边缘。", ["surface_segmentation", "coverage_trace"], "观察结合面中央和边缘的覆盖范围。"),
            _criterion("head_object_disambiguation", "时序关系", "对象区分", "清洁对象保持为气缸盖，不与气缸体、活塞或气缸孔混淆。", ["object_detection", "representative_frame"], "燃烧室/气门侧特征与气缸盖所在位置保持一致。"),
            _criterion("head_wipe_sequence", "完成状态", "擦拭过程", "连续画面显示布料移动和擦拭过程。", ["multi_frame_sequence", "coverage_trace"], "布料与结合面的接触位置随擦拭动作连续移动。"),
        ],
    ),
    _rule(
        14,
        "clean_block",
        [
            _criterion("block_mating_surface_identity", "对象识别", "气缸体结合面", "画面能辨认气缸体上方结合面及其气缸孔周边。", ["object_detection", "surface_segmentation"], "区分气缸体上表面与气缸盖表面。"),
            _criterion("block_cloth_contact", "动作过程", "布料接触", "白色无纺布直接接触气缸体结合面。", ["object_detection", "contact_detail"], "关注布料和结合面的接触位置。"),
            _criterion("block_seal_coverage", "动作过程", "密封区域覆盖", "擦拭覆盖气缸孔周边和主要密封区域。", ["surface_segmentation", "coverage_trace"], "观察孔周边和外缘的覆盖范围。"),
            _criterion("block_object_disambiguation", "时序关系", "对象区分", "清洁对象保持为气缸体，不与气缸盖或其他零件混淆。", ["object_detection", "representative_frame"], "气缸孔周边特征与气缸体所在位置保持一致。"),
            _criterion("block_wipe_sequence", "完成状态", "擦拭过程", "连续画面显示布料在气缸体表面移动并形成擦拭变化。", ["multi_frame_sequence", "coverage_trace"], "画面显示布料的连续接触和移动，便于区分擦拭与刮除。"),
        ],
    ),
    _rule(
        15,
        "clean_gasket",
        [
            _criterion("gasket_identity", "对象识别", "气缸垫对象", "画面中能辨认待清洁的气缸垫。", ["object_detection", "double_side_segmentation"], "区分薄片气缸垫与其他金属零件。"),
            _criterion("front_wipe", "动作过程", "正面擦拭", "无纺布与气缸垫正面接触并完成擦拭。", ["double_side_segmentation", "contact_detail"], "正面画面清楚呈现布料接触和移动。"),
            _criterion("flip_action", "时序关系", "翻面", "画面显示气缸垫由正面翻到反面。", ["front_back_labels", "multi_frame_sequence"], "观察翻面前后的方向变化。"),
            _criterion("back_wipe", "动作过程", "反面擦拭", "无纺布与气缸垫反面接触并完成擦拭。", ["double_side_segmentation", "contact_detail"], "翻面画面与反面布料接触画面分别出现。"),
            _criterion("pre_install_timing", "完成状态", "安装前完成", "清洁过程发生在气缸垫安装之前。", ["temporal_order", "clean_state_card"], "按清洁和安装的先后顺序观察。"),
        ],
    ),
    _rule(
        16,
        "clean_pins",
        [
            _criterion("both_pin_identity", "对象识别", "两枚定位销", "同一检查画面中能辨认两枚金属定位销。", ["object_detection", "pin_segmentation"], "区分两枚定位销与周围孔位。"),
            _criterion("pin_one_wipe", "动作过程", "定位销 1 清洁", "无纺布与定位销 1 接触。", ["contact_detail", "pin_segmentation"], "观察布料和定位销 1 的接触位置。"),
            _criterion("pin_two_wipe", "动作过程", "定位销 2 清洁", "无纺布与定位销 2 接触。", ["contact_detail", "pin_segmentation"], "观察布料和定位销 2 的接触位置。"),
            _criterion("pin_wipe_order", "时序关系", "清洁顺序", "画面按定位销1→定位销2的顺序呈现清洁动作。", ["pin_sequence", "multi_frame_sequence"], "定位销编号与清洁先后顺序一致。"),
            _criterion("pin_coverage_complete", "完成状态", "覆盖完整", "两枚定位销都出现对应的擦拭过程。", ["pin_sequence", "contact_detail"], "两枚定位销各自呈现清晰的擦拭画面，避免遗漏任一目标。"),
        ],
    ),
    _rule(
        17,
        "report_gasket",
        [
            _criterion("replacement_process_node", "时序关系", "更换流程节点", "更换流程中出现清晰的视觉节点或停顿。", ["process_node_frame", "temporal_order"], "视觉节点位于气缸垫安装流程中。"),
            _criterion("new_gasket_ready", "对象识别", "新垫片待用", "新气缸垫在安装前处于待用位置。", ["new_gasket_detection", "process_node_frame"], "区分待用垫片与已经落座的垫片。"),
            _criterion("node_before_install", "时序关系", "节点在安装前", "流程节点先于气缸垫进入安装位置。", ["temporal_order", "process_node_frame"], "按画面先后观察节点和安装动作。"),
            _criterion("replacement_sequence", "完成状态", "流程顺序", "待用、节点和安装三个阶段顺序清晰。", ["temporal_order", "multi_frame_sequence"], "待用、节点和安装三段工序前后连贯。"),
        ],
    ),
    _rule(
        18,
        "install_gasket",
        [
            _criterion("new_gasket_identity", "对象识别", "新气缸垫", "画面中能辨认穿孔气缸垫及其外轮廓。", ["object_detection", "gasket_segmentation"], "区分气缸垫与气缸盖、气缸体。"),
            _criterion("gasket_orientation", "对象识别", "方向与正反面", "方向和正反面线索与安装位置相符。", ["orientation_card", "front_back_labels"], "观察标记、孔位和外轮廓方向。"),
            _criterion("hole_outline_match", "动作过程", "孔位匹配", "气缸孔、螺栓孔、定位销孔和外轮廓相互对应。", ["hole_pin_relation", "gasket_segmentation"], "关注孔位与外缘的对应关系。"),
            _criterion("flat_seat", "完成状态", "平整落座", "气缸垫平整落在结合面上，没有明显错位。", ["representative_frame", "hole_pin_relation"], "落座后定位销被垫片遮挡属于正常现象。"),
            _criterion("align_place_seat_sequence", "时序关系", "安装顺序", "画面依次呈现对准、放置和落座。", ["multi_frame_sequence", "temporal_order"], "按三个阶段的先后观察安装过程。"),
        ],
    ),
    _rule(
        19,
        "cylinder_head_bolt",
        [
            _criterion("new_bolt_ready", "对象识别", "新螺栓待用", "新螺栓出现在待安装区域。", ["new_bolt_detection", "process_node_frame"], "区分待用螺栓和已经插入的螺栓。"),
            _criterion("pre_install_node", "时序关系", "安装前节点", "螺栓安装前出现清晰的流程节点。", ["process_node_frame", "temporal_order"], "节点应与待安装区域同一流程相连。"),
            _criterion("node_before_bolt_action", "时序关系", "节点先于动作", "流程节点先于螺栓插入或紧固。", ["temporal_order", "multi_frame_sequence"], "按画面先后观察节点和螺栓动作。"),
            _criterion("bolt_report_sequence", "完成状态", "流程顺序", "待用、节点和安装三个阶段按先后出现。", ["temporal_order", "process_node_frame"], "待用、节点和安装三段画面前后连贯。"),
        ],
    ),
    _rule(
        20,
        "install_1st",
        [
            _criterion("torque_wrench_contact", "动作过程", "扭力扳手接触", "扭力扳手与气缸盖螺栓接触并出现可见旋转。", ["object_detection", "tool_segmentation", "bolt_trace"], "区分扭力扳手、普通扳手和徒手操作。"),
            _criterion("first_pass_anchor", "时序关系", "第一次轮次", "画面能定位第一次安装预紧轮次。", ["multi_frame_sequence", "bolt_trace"], "区分第一次预紧和后续轮次。"),
            _criterion("bolt_order_1_to_10", "时序关系", "1—10 顺序", "顺序条按 1→10 展示气缸盖螺栓处理先后。", ["sequence_order", "bolt_trace"], "观察每个编号的出现顺序。"),
            _criterion("torque_25_nm_card", "完成状态", "25 N·m 参数", "画面显示本轮 25 N·m 工艺参数卡。", ["torque_parameter_card"], "参数卡与第一次预紧轮次相对应。"),
            _criterion("round_disambiguation", "对象识别", "轮次区分", "画面能区分第一次预紧、角度轮次和徒手补紧（手动补拧）。", ["multi_frame_sequence", "bolt_trace", "torque_parameter_card"], "工具、动作和轮次标签清楚区分三类操作。"),
            _criterion("no_manual_retightening", "完成状态", "完成后状态", "第一次预紧完成后不再出现手动补拧动作。", ["multi_frame_sequence", "bolt_trace"], "关注第一次预紧结束后的连续画面。"),
        ],
    ),
]


DETAIL_RULES_BY_ITEM = {rule["item_id"]: rule for rule in DETAIL_RULES}


def detail_rule_for(item_id: str) -> dict[str, Any]:
    """Return a defensive copy of one item's rule."""
    try:
        return deepcopy(DETAIL_RULES_BY_ITEM[item_id])
    except KeyError as exc:
        raise KeyError(f"未知详细规则项目：{item_id}") from exc


def detail_form_for(item_id: str) -> dict[str, Any]:
    return detail_rule_for(item_id)["detail_form"]


def criterion_map(item_id: str) -> dict[str, dict[str, Any]]:
    return {
        str(criterion["criterion_id"]): criterion
        for criterion in detail_form_for(item_id).get("criteria", [])
    }
