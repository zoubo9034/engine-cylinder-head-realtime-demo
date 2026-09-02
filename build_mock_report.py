#!/usr/bin/env python3
"""Build a simulated live-stream report from existing Engine artifacts.

The generated payload is intentionally a live-event fixture.  Historical
scores, source versions and raw paths remain only in this JSON for generation
audit; ``render_report.py`` removes them from the public HTML projection.
"""

from __future__ import annotations

import argparse
import json
import statistics
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from report_schema import ITEM_DEFINITIONS, template_payload, validated_copy
from workflow_tool_stats import build_profile


ROOT = Path(__file__).resolve().parent


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - surfaced as CLI error
        raise ValueError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _iter_summary_evidence(summary: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for category in (summary.get("breakdown") or {}).values():
        if not isinstance(category, dict):
            continue
        for evidence in category.get("evidence", []) or []:
            if isinstance(evidence, dict):
                yield evidence


def _session_images(session_dir: Path) -> list[Path]:
    """Collect a bounded set of real visual artifacts for optional evidence."""
    artifact_root = session_dir / "artifacts"
    if not artifact_root.is_dir():
        return []
    patterns = (
        "**/candidate_overlays/*",
        "**/*frame_strip*",
        "**/seed_box_frames/*",
        "**/keyframes/*.jpg",
    )
    seen: set[Path] = set()
    found: list[Path] = []
    for pattern in patterns:
        for path in sorted(artifact_root.glob(pattern)):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or path in seen:
                continue
            seen.add(path)
            found.append(path)
            if len(found) >= 80:
                return found
    return found


def _timestamp_seconds(evidence: dict[str, Any]) -> float | None:
    value = evidence.get("timestamp_sec")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(evidence.get("timestamp") or "").strip().rstrip("s")
    if ":" in text:
        try:
            minutes, seconds = text.split(":", 1)
            return float(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _format_timestamp(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _candidate_record(
    item_id: str,
    sample_id: str,
    evidence: dict[str, Any],
    *,
    kind: str,
    phase: str | None = None,
    source_path: Path | None = None,
    evidence_id_suffix: str = "",
) -> dict[str, Any]:
    path = source_path or Path(str(evidence.get("keyframe_path") or evidence.get("keyframe") or ""))
    seconds = _timestamp_seconds(evidence)
    confidence = evidence.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.55
    digest_hint = f"{item_id}-{sample_id}-{kind}-{evidence_id_suffix or path.name}"
    return {
        "evidence_id": digest_hint.replace("/", "_").replace(" ", "_")[:180],
        "item_id": item_id,
        "sample_id": sample_id,
        "kind": kind,
        "phase": phase,
        "timestamp": _format_timestamp(seconds),
        "timestamp_sec": seconds,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
        "caption": str(evidence.get("description") or "当前视频流中的相关操作证据。")[:280],
        "source_path": str(path) if path else "",
    }


def _pick_unique(
    candidates: list[dict[str, Any]],
    path_owners: dict[str, str],
    item_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    local: set[str] = set()
    for candidate in candidates:
        path = str(candidate.get("source_path") or "")
        # A frame may support multiple slots of the same item (for example the
        # representative frame and its sequence strip), but it may never be
        # borrowed by another item.
        if not path or (path in path_owners and path_owners[path] != item_id) or path in local:
            continue
        local.add(path)
        selected.append(candidate)
        if len(selected) >= limit:
            break
    for path in local:
        path_owners.setdefault(path, item_id)
    return selected


def _state_for(confidence: float, required_bound: int, required_total: int) -> str:
    if required_bound < required_total:
        return "证据生成中"
    if confidence < 0.50:
        return "待人工确认"
    if confidence < 0.78:
        return "证据生成中"
    return "已完成评分"


def build_mock(template: dict[str, Any], source_run: Path) -> dict[str, Any]:
    summaries = sorted(source_run.glob("task*/**/reports/scoring_report_summary.json"))
    if len(summaries) != 10:
        raise ValueError(f"期望 10 个视频报告，实际找到 {len(summaries)} 个：{source_run}")

    payload = validated_copy(template)
    tool_profile = build_profile(source_run, expected_samples=10)
    profile_items = tool_profile.get("items", {}) or {}
    for item in payload["items"]:
        item_profile = profile_items.get(item["item_id"])
        if not isinstance(item_profile, dict):
            raise ValueError(f"缺少评分项工具 profile：{item['item_id']}")
        item["difficulty"] = item_profile["difficulty"]
        item["difficulty_label"] = item_profile["difficulty_label"]
        item["analysis_profile"] = deepcopy(item_profile["analysis_profile"])
        item["analysis_tools"] = deepcopy(item_profile["analysis_tools"])
    payload["demo_mode"] = "mock_live_stream"
    payload["presentation"]["initial_state"] = "正在接入视频流"
    payload["_mock_audit"] = {
        "fixture_label": "ten_video_fixture",
        "source_run": str(source_run.resolve()),
        "sample_count": len(summaries),
        "note": "仅供展示回放生成；不作为评分输入。",
    }

    by_item: dict[str, list[tuple[str, dict[str, Any], Path]]] = {d["item_id"]: [] for d in ITEM_DEFINITIONS}
    for summary_path in summaries:
        summary = _read_json(summary_path)
        session_dir = summary_path.parent.parent
        sample_id = session_dir.name
        for evidence in _iter_summary_evidence(summary):
            item_id = str(evidence.get("item") or "")
            if item_id in by_item:
                by_item[item_id].append((sample_id, evidence, session_dir))

    path_owners: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    for item in payload["items"]:
        item_id = item["item_id"]
        rows = by_item.get(item_id, [])
        # Highest-confidence rows first gives the UI a useful positive example,
        # while retaining lower-confidence rows for the manual-review state.
        rows.sort(key=lambda row: float(row[1].get("confidence") or 0.0), reverse=True)
        selected_rows = rows[:5]
        all_images: list[Path] = []
        for _, _, session_dir in selected_rows:
            all_images.extend(_session_images(session_dir))

        keyframe_candidates = [
            _candidate_record(item_id, sample_id, evidence, kind="representative_frame")
            for sample_id, evidence, _ in selected_rows
            if evidence.get("keyframe_path") or evidence.get("keyframe")
        ]
        frame_candidates = [
            dict(candidate, kind="multi_frame_sequence", phase=phase)
            for phase, candidate in zip(("开始", "动作中", "完成"), keyframe_candidates)
        ]
        extra_candidates: list[dict[str, Any]] = []
        for image in all_images:
            if str(image) in {str(c.get("source_path")) for c in keyframe_candidates}:
                continue
            lower = image.name.lower()
            kind = "object_detection" if "overlay" in lower or "candidate" in lower else "artifact_frame"
            extra_candidates.append(_candidate_record(item_id, "fixture", {}, kind=kind, source_path=image, evidence_id_suffix=image.name))

        binding_evidence: list[dict[str, Any]] = []
        slot_map: dict[str, list[dict[str, Any]]] = {}
        definition = next(d for d in ITEM_DEFINITIONS if d["item_id"] == item_id)
        for slot_id in definition["required_slots"]:
            if slot_id == "live_timestamp":
                candidates = [
                    _candidate_record(
                        item_id,
                        sample_id,
                        evidence,
                        kind="timestamp",
                        source_path=Path(f"timestamp://{item_id}/{sample_id}"),
                    )
                    for sample_id, evidence, _ in selected_rows
                    if _timestamp_seconds(evidence) is not None
                ]
                chosen = _pick_unique(candidates, path_owners, item_id, 1)
            elif slot_id == "representative_frame" or slot_id == "process_node_frame":
                chosen = _pick_unique(keyframe_candidates, path_owners, item_id, 1)
            elif slot_id in {"multi_frame_sequence", "temporal_order", "sequence_order"}:
                chosen = _pick_unique(frame_candidates, path_owners, item_id, 3)
                if len(chosen) < 3:
                    chosen.extend(_pick_unique(extra_candidates, path_owners, item_id, 3 - len(chosen)))
            elif slot_id == "object_detection":
                chosen = _pick_unique(extra_candidates + keyframe_candidates, path_owners, item_id, 1)
            else:
                chosen = _pick_unique(extra_candidates + keyframe_candidates, path_owners, item_id, 1)
            slot_map[slot_id] = chosen
            binding_evidence.extend(chosen)

        confidence_values = [float(row[1].get("confidence") or 0.55) for row in selected_rows]
        confidence = statistics.mean(confidence_values) if confidence_values else 0.0
        required_bound = sum(1 for slot_id in definition["required_slots"] if slot_map.get(slot_id))
        state = _state_for(confidence, required_bound, len(definition["required_slots"]))
        first_row = selected_rows[0] if selected_rows else None
        first_evidence = first_row[1] if first_row else {}
        first_seconds = _timestamp_seconds(first_evidence)
        item["live_binding"] = {
            "state": state,
            "revision": 1,
            "changed_slot_ids": list(definition["required_slots"]),
            "live_timestamp": _format_timestamp(first_seconds),
            "live_start_sec": first_seconds,
            "live_end_sec": (first_seconds + 8.0) if first_seconds is not None else None,
            "time_source": "mock_live_stream",
            "time_confidence": round(confidence, 3),
            "evidence_explanation": (
                item["evidence_hint"]
                if state == "已完成评分"
                else "当前证据已提取，置信度不足，等待人工确认。"
            ),
            "evidence": binding_evidence,
        }
        item["score"] = {"证据已绑定": 1, "已完成评分": 1, "待人工确认": 0}.get(state)
        for slot in item["required_evidence_slots"]:
            evidence = slot_map.get(slot["slot_id"], [])
            slot["status"] = "bound" if evidence else "empty"
            slot["evidence"] = evidence
        completed_item = deepcopy(item)
        # The mock file starts empty.  The completed item is carried by the
        # simulated update event and is ingested only after the user starts the
        # evaluation, matching the live JSON write path.
        item["live_binding"] = {
            "state": "待开始",
            "revision": 0,
            "changed_slot_ids": [],
            "live_timestamp": None,
            "live_start_sec": None,
            "live_end_sec": None,
            "time_source": "mock_live_stream",
            "time_confidence": None,
            "evidence_explanation": "等待当前视频流中的有效证据。",
            "evidence": [],
        }
        item["score"] = None
        for slot in item["required_evidence_slots"]:
            slot["status"] = "empty"
            slot["evidence"] = []
        events.append({
            "event_id": f"evt-{item_id}",
            "item_id": item_id,
            "delay_ms": 1150 + (900 if item["difficulty"] == "difficult" else 250),
            "processing_ms": 1900 if item["difficulty"] == "difficult" else 1200,
            "final_state": state,
            "evidence_ids": [e["evidence_id"] for e in binding_evidence],
            "item_patch": completed_item,
        })

    payload["events"] = events
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="从 10 个 Engine artifacts 构建模拟实时报告 JSON")
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    template = _read_json(args.template)
    result = build_mock(template, args.source_run)
    errors = validated_copy(result).get("items", [])
    del errors  # validation is performed by validated_copy above
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 mock JSON：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
