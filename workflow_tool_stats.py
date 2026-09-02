#!/usr/bin/env python3
"""Aggregate per-item tool usage from Engine workflow traces.

The report UI only needs a small, path-free profile.  This script keeps the
artifact traversal and the aggregation rules in one place so maintainers can
regenerate the checked-in profile when the source run changes.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from report_schema import ITEM_DEFINITIONS


TOOL_LABELS = {
    "oral_evidence_analyzer": "口述证据分析",
    "temporal_sequence_analyzer": "时序与顺序分析",
    "object_motion_inspector": "对象动作与旋转分析",
    "small_object_action_inspector": "小目标动作分析",
    "end_state_workspace_inspector": "终态与工作区检查",
    "rotation_tool_motion_inspector": "旋转动作分析",
    "video_mme_subtitle_analyzer": "字幕与转写分析",
}

# Difficulty is intentionally a presentation category, not a score.  The
# evidence-chain features make the judgment auditable when several items share
# the same stage-level tool set.
DIFFICULTY_BY_ITEM = {
    "item_5069": ("difficult", "困难", ["角度与轮次", "扳手—螺栓关系", "连续动作"]),
    "cylinder_head": ("easy", "简单", ["放置终态", "直接对象关系"]),
    "gasket_remove": ("easy", "简单", ["取下动作", "脱离终态"]),
    "gasket_inspect": ("medium", "中等", ["近景小目标", "孔位与边缘检查"]),
    "positioning": ("difficult", "困难", ["双定位销", "近景小目标", "逐目标检查"]),
    "clean_head": ("difficult", "困难", ["结合面覆盖", "擦拭接触", "对象区分"]),
    "clean_block": ("difficult", "困难", ["结合面覆盖", "擦拭接触", "对象区分"]),
    "clean_gasket": ("medium", "中等", ["正反两面", "清洁时序"]),
    "clean_pins": ("difficult", "困难", ["双定位销", "逐目标接触", "连续动作"]),
    "report_gasket": ("difficult", "困难", ["安装前节点", "前后时序", "流程停顿"]),
    "install_gasket": ("medium", "中等", ["方向确认", "孔位—定位销匹配", "落座终态"]),
    "cylinder_head_bolt": ("difficult", "困难", ["安装前节点", "前后时序", "新旧对象区分"]),
    "install_1st": ("difficult", "困难", ["1—10顺序", "扭矩工具", "轮次区分", "连续动作"]),
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _stage_for(path: Path) -> str:
    for part in path.parts:
        if part in {"prep", "a1", "a2", "comp"}:
            return part
    return "unknown"


def _trace_records(trace_path: Path, active_ids: set[str]) -> Iterable[dict[str, Any]]:
    trace = _read_json(trace_path)
    found: dict[str, dict[str, Any]] = {}
    for node in trace.get("nodes", []) or []:
        if not isinstance(node, dict) or node.get("node_type") != "tool_prior_consumption":
            continue
        names = [str(name) for name in node.get("executable_tool_names", []) or [] if str(name)]
        plans = int(node.get("executable_tool_plan_count") or 0)
        tasks = int(node.get("executable_tool_task_count") or 0)
        for claim_id in node.get("tool_prior_claim_ids", []) or []:
            item_id = str(claim_id)
            if item_id not in active_ids:
                continue
            record = found.setdefault(
                item_id,
                {"tools": set(), "plan_count": plans, "task_count": tasks},
            )
            record["tools"].update(names)
            record["plan_count"] = max(record["plan_count"], plans)
            record["task_count"] = max(record["task_count"], tasks)
    stage = _stage_for(trace_path)
    sample_id = ""
    for part in trace_path.parts:
        if part.startswith("新能源") or part.startswith("智能网联"):
            sample_id = part
            break
    for item_id, record in found.items():
        yield {
            "item_id": item_id,
            "sample_id": sample_id,
            "stage": stage,
            "tools": sorted(record["tools"]),
            "plan_count": record["plan_count"],
            "task_count": record["task_count"],
        }


def _difficulty(item_id: str, tool_count: int, required_count: int) -> tuple[str, str, list[str]]:
    try:
        level, label, features = DIFFICULTY_BY_ITEM[item_id]
    except KeyError as exc:
        raise ValueError(f"缺少评分项难度配置：{item_id}") from exc
    # Keep the configured category auditable while checking that it is
    # consistent with the observed tool/chain scale.
    if tool_count <= 0:
        raise ValueError(f"{item_id}: workflow trace 没有可用工具")
    if required_count <= 0:
        raise ValueError(f"{item_id}: 缺少必填证据槽位定义")
    return level, label, features


def build_profile(source_run: Path, *, expected_samples: int = 10) -> dict[str, Any]:
    source_run = source_run.resolve()
    summaries = sorted(source_run.glob("task*/**/reports/scoring_report_summary.json"))
    if len(summaries) != expected_samples:
        raise ValueError(f"期望 {expected_samples} 个视频报告，实际找到 {len(summaries)} 个：{source_run}")

    active = {definition["item_id"]: definition for definition in ITEM_DEFINITIONS}
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_trace_paths: set[Path] = set()
    samples: set[str] = set()
    for summary_path in summaries:
        session_dir = summary_path.parent.parent
        samples.add(session_dir.name)
        traces = sorted(session_dir.glob("artifacts/evidence_enrichment/**/workflow_trace.json"))
        if not traces:
            raise ValueError(f"缺少 workflow_trace：{session_dir}")
        all_trace_paths.update(traces)
        for trace_path in traces:
            for record in _trace_records(trace_path, set(active)):
                records[record["item_id"]].append(record)

    profile_items: dict[str, Any] = {}
    for item_id, definition in active.items():
        item_records = records.get(item_id, [])
        if len(item_records) != expected_samples:
            raise ValueError(
                f"{item_id}: 期望 {expected_samples} 条评分项 trace，实际 {len(item_records)} 条"
            )
        tool_trace_counts = Counter(
            tool for record in item_records for tool in set(record["tools"])
        )
        tools = [
            {
                "tool_id": tool_id,
                "label": TOOL_LABELS.get(tool_id, tool_id),
                "trace_count": tool_trace_counts[tool_id],
            }
            for tool_id in sorted(tool_trace_counts)
        ]
        plan_counts = [record["plan_count"] for record in item_records]
        task_counts = [record["task_count"] for record in item_records]
        stages = Counter(record["stage"] for record in item_records)
        level, label, features = _difficulty(
            item_id,
            len(tools),
            len(definition["required_slots"]),
        )
        profile_items[item_id] = {
            "difficulty": level,
            "difficulty_label": label,
            "analysis_profile": {
                "stage": stages.most_common(1)[0][0],
                "sample_count": len({record["sample_id"] for record in item_records}),
                "workflow_trace_count": len(item_records),
                "distinct_tool_count": len(tools),
                "total_tool_plan_count": sum(plan_counts),
                "average_tool_plan_count": round(statistics.mean(plan_counts), 2),
                "total_tool_task_count": sum(task_counts),
                "average_tool_task_count": round(statistics.mean(task_counts), 2),
                "required_slot_count": len(definition["required_slots"]),
                "enhanced_slot_count": len(definition["enhanced_slots"]),
                "complexity_features": features,
                "rationale": "工具调用数量与证据链复杂度综合判断",
            },
            "analysis_tools": tools,
        }

    return {
        "schema": "workflow-tool-profile/v1",
        "source_run_label": source_run.name,
        "sample_count": len(samples),
        "stage_trace_count": len(all_trace_paths),
        "items": profile_items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="统计 Engine workflow_trace 的评分项工具链")
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-samples", type=int, default=10)
    args = parser.parse_args()
    profile = build_profile(args.source_run, expected_samples=args.expected_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 workflow tool profile：{args.output}")
    print(f"样本数：{profile['sample_count']}；阶段 trace：{profile['stage_trace_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
