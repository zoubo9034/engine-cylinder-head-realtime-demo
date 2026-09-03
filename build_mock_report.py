#!/usr/bin/env python3
"""Build a visual-only live replay from the ten Engine report artifacts.

The artifacts are used as a bounded source for a deterministic demonstration
fixture.  Their long descriptions, sample names and paths are retained only
in the private generation audit; captions and detail checks sent to the
browser are short visual statements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from detail_rules import criterion_map
from report_schema import ITEM_DEFINITIONS, template_payload, validated_copy
from workflow_tool_stats import build_profile


POSITIVE_JUDGMENTS = {"正确", "满足", "通过", "correct", "confirmed", "pass", "passed"}
VISUAL_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ROOT = Path(__file__).resolve().parent


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - surfaced by the CLI
        raise ValueError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _iter_summary_evidence(summary: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield evidence records without assuming a fixed breakdown category."""
    for category in (summary.get("breakdown") or {}).values():
        if not isinstance(category, Mapping):
            continue
        for evidence in category.get("evidence", []) or []:
            if isinstance(evidence, dict):
                yield evidence


def _session_images(session_dir: Path) -> list[Path]:
    """Collect a bounded, deterministic set of real image artifacts."""
    artifact_root = session_dir / "artifacts"
    if not artifact_root.is_dir():
        return []
    patterns = (
        "**/candidate_overlays/*",
        "**/*frame_strip*",
        "**/seed_box_frames/*",
        "**/keyframes/*.jpg",
        "**/keyframes/*.jpeg",
        "**/keyframes/*.png",
    )
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(artifact_root.glob(pattern)):
            if path.suffix.lower() not in VISUAL_IMAGE_SUFFIXES or path in seen:
                continue
            seen.add(path)
            found.append(path)
            if len(found) >= 80:
                return found
    return found


def _timestamp_seconds(evidence: Mapping[str, Any]) -> float | None:
    value = evidence.get("timestamp_sec")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(evidence.get("timestamp") or "").strip().rstrip("s")
    if ":" in text:
        try:
            minutes, seconds = text.split(":", 1)
            return float(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _format_timestamp(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:14]


def _caption(kind: str, phase: str | None) -> str:
    labels = {
        "representative_frame": "目标对象画面",
        "object_detection": "对象位置画面",
        "multi_frame_sequence": "连续动作画面",
        "artifact_frame": "相关现场画面",
        "timestamp": "现场时间标记",
        "process_node_frame": "流程节点画面",
        "sequence_order": "顺序画面",
    }
    label = labels.get(kind, "相关画面")
    return f"{phase}·{label}" if phase else label


def _candidate_record(
    item_id: str,
    sample_id: str,
    evidence: Mapping[str, Any],
    *,
    kind: str,
    phase: str | None = None,
    source_path: Path | None = None,
    evidence_id_suffix: str = "",
    order_index: int | None = None,
    round_label: str | None = None,
) -> dict[str, Any]:
    """Create a private evidence record with a non-identifying public ID."""
    path = source_path or Path(str(evidence.get("keyframe_path") or evidence.get("keyframe") or ""))
    seconds = _timestamp_seconds(evidence)
    confidence = evidence.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = 0.55
    identity = f"{item_id}|{sample_id}|{kind}|{evidence_id_suffix or str(path)}|{seconds}"
    return {
        "evidence_id": f"ev-{_digest(identity)}",
        "item_id": item_id,
        # These fields are generation-audit fields.  render_report strips them.
        "sample_id": sample_id,
        "kind": kind,
        "phase": phase,
        "round": round_label,
        "order_index": order_index,
        "timestamp": _format_timestamp(seconds),
        "timestamp_sec": seconds,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
        "caption": _caption(kind, phase),
        "source_path": str(path) if path else "",
    }


def _pick_unique(
    candidates: list[dict[str, Any]],
    path_owners: dict[str, str],
    item_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Select candidates while preventing physical evidence reuse by items."""
    selected: list[dict[str, Any]] = []
    local: set[str] = set()
    for candidate in candidates:
        path = str(candidate.get("source_path") or "")
        if not path:
            continue
        if path in path_owners and path_owners[path] != item_id:
            continue
        if path in local:
            continue
        local.add(path)
        selected.append(candidate)
        if len(selected) >= limit:
            break
    for path in local:
        path_owners.setdefault(path, item_id)
    return selected


def _state_for(confidence: float, required_bound: int, required_total: int) -> str:
    """Retain the historical helper's conservative lifecycle mapping."""
    if required_bound < required_total:
        return "证据生成中"
    if confidence < 0.50:
        return "待人工确认"
    if confidence < 0.78:
        return "证据生成中"
    return "已完成评分"


def _row_is_positive(row: tuple[str, dict[str, Any], Path]) -> bool:
    evidence = row[1]
    judgment = str(evidence.get("judgment") or "").strip().lower()
    confidence = evidence.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    return judgment in {value.lower() for value in POSITIVE_JUDGMENTS} and confidence_value >= 0.78


def _row_is_direct_positive(row: tuple[str, dict[str, Any], Path]) -> bool:
    """Return whether a positive row carries an explicit visual support pointer."""
    if not _row_is_positive(row):
        return False
    evidence = row[1]
    supporting = evidence.get("supporting_artifacts")
    has_supporting_image = isinstance(supporting, (list, tuple)) and any(
        isinstance(value, str) and Path(value).suffix.lower() in VISUAL_IMAGE_SUFFIXES
        for value in supporting
    )
    return bool(
        evidence.get("policy_observation_id")
        or has_supporting_image
        or evidence.get("keyframe_path")
        or evidence.get("keyframe")
    )


def _timestamp_sort_key(evidence: Mapping[str, Any]) -> tuple[int, float, str]:
    """Sort real frames chronologically, with undated frames at the end."""
    seconds = _timestamp_seconds(evidence)
    return (
        1 if seconds is None else 0,
        seconds if seconds is not None else 0.0,
        str(evidence.get("keyframe_path") or evidence.get("keyframe") or ""),
    )


def _phase_for_index(index: int) -> str:
    return ("开始", "动作中", "完成")[min(max(index, 0), 2)]


def _check_copy(
    criterion: Mapping[str, Any],
    *,
    status: str,
    refs: list[str],
    confidence: float | None,
    observation: str,
    reason: str = "",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "criterion_id": str(criterion["criterion_id"]),
        "status": status,
        "confidence": confidence,
        "evidence_ids": refs,
        "observation": observation,
        "reason": reason,
    }
    return value


def _criterion_checks(
    item_id: str,
    rows: list[tuple[str, dict[str, Any], Path]],
    slot_map: Mapping[str, list[dict[str, Any]]],
    state: str,
) -> list[dict[str, Any]]:
    """Build per-criterion visual checks from direct item evidence.

    This function only consumes item judgment/confidence and the existence of
    visual evidence slots.  It never reads a description or any speech field.
    """
    criteria = criterion_map(item_id)
    positive = any(_row_is_direct_positive(row) for row in rows)
    best_confidence = max(
        [float(row[1].get("confidence") or 0.0) for row in rows] or [0.0]
    )
    checks: list[dict[str, Any]] = []
    for criterion_id, criterion in criteria.items():
        refs: list[str] = []
        for slot_id in criterion.get("evidence_slot_ids", []) or []:
            for evidence in slot_map.get(str(slot_id), []) or []:
                evidence_id = str(evidence.get("evidence_id") or "")
                if evidence_id and evidence_id not in refs:
                    refs.append(evidence_id)
        # The item-level positive result is intentionally narrowed for the two
        # completed examples: a static frame confirms object/placement facts,
        # while a missing motion or order frame remains for review.
        confirmed_ids = {
            "cylinder_head": {"pad_under_head", "stable_pad_support"},
            "install_gasket": {"new_gasket_identity", "hole_outline_match", "flat_seat"},
        }.get(item_id, set())
        if state == "证据生成中":
            status = "pending"
            observation = "正在整理对象、动作和时序画面。"
            reason = ""
            confidence: float | None = None
        elif not refs:
            status = "manual_review"
            observation = "尚未找到清晰的相关画面。"
            reason = "请补充对象和动作画面。"
            confidence = None
        elif positive and criterion_id in confirmed_ids:
            status = "confirmed"
            observation = "相关对象关系在清晰画面中得到确认。"
            reason = ""
            confidence = round(min(1.0, max(0.0, best_confidence)), 3)
        elif positive:
            status = "manual_review"
            observation = "相关画面已经出现，完整过程仍待确认。"
            reason = "请继续查看动作顺序和完成状态。"
            confidence = round(min(1.0, max(0.0, best_confidence)), 3)
        else:
            status = "manual_review"
            observation = "当前画面尚未完整呈现这一动作。"
            reason = "需要看到对象、动作和完成状态的连续画面。"
            confidence = round(min(1.0, max(0.0, best_confidence)), 3)
        checks.append(_check_copy(criterion, status=status, refs=refs, confidence=confidence, observation=observation, reason=reason))
    return checks


def _empty_binding() -> dict[str, Any]:
    return {
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


def _empty_slots(item: dict[str, Any]) -> None:
    for slot in item.get("required_evidence_slots", []) or []:
        slot["status"] = "empty"
        slot["evidence"] = []
    for slot in item.get("enhanced_evidence_slots", []) or []:
        slot["status"] = "empty"
        slot["evidence"] = []


def _all_slots(item: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    yield from item.get("required_evidence_slots", []) or []
    yield from item.get("enhanced_evidence_slots", []) or []


def _slot_candidates(
    slot_id: str,
    image_candidates: list[dict[str, Any]],
    timestamp_candidates: list[dict[str, Any]],
    path_owners: dict[str, str],
    item_id: str,
) -> list[dict[str, Any]]:
    if slot_id == "live_timestamp":
        return _pick_unique(timestamp_candidates, path_owners, item_id, 1)
    if slot_id in {"multi_frame_sequence", "temporal_order", "sequence_order", "pin_sequence"}:
        return _pick_unique(image_candidates, path_owners, item_id, 3)
    return _pick_unique(image_candidates, path_owners, item_id, 1)


def _build_item_event(
    item: dict[str, Any],
    rows: list[tuple[str, dict[str, Any], Path]],
    path_owners: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    item_id = str(item["item_id"])
    # Select the strongest source rows for lifecycle/status decisions, then
    # present those same rows in real chronological order.  Confidence is a
    # selection signal; it must not scramble the start/action/completion
    # sequence shown to a viewer.
    ranked_rows = sorted(
        rows,
        key=lambda row: (
            float(row[1].get("confidence") or 0.0),
            _timestamp_seconds(row[1]) if _timestamp_seconds(row[1]) is not None else -1.0,
        ),
        reverse=True,
    )
    selected_rows = ranked_rows[:6]
    sequence_rows = sorted(selected_rows, key=lambda row: _timestamp_sort_key(row[1]))
    image_candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    # Put one keyframe from each selected source row first.  This gives the
    # sequence slots meaningful start/action/completion phases instead of
    # filling all three with the same representative frame.
    for _index, (sample_id, evidence, _session_dir) in enumerate(sequence_rows):
        raw_path = str(evidence.get("keyframe_path") or evidence.get("keyframe") or "")
        if not raw_path or raw_path in seen_paths:
            continue
        if raw_path in path_owners and path_owners[raw_path] != item_id:
            continue
        seen_paths.add(raw_path)
        if not Path(raw_path).suffix.lower() in VISUAL_IMAGE_SUFFIXES:
            continue
        # Derive the phase from the frames that survive local de-duplication;
        # a skipped/duplicate frame must not leave a sequence starting at
        # “动作中”.
        phase = _phase_for_index(len(image_candidates))
        round_label = None
        if item_id == "item_5069":
            round_label = "第二次预松"
        elif item_id == "install_1st":
            round_label = "第一次预紧"
        image_candidates.append(
            _candidate_record(
                item_id,
                sample_id,
                evidence,
                kind="representative_frame",
                phase=phase,
                order_index=len(image_candidates) + 1,
                round_label=round_label,
            )
        )
    # Add additional real artifacts only after the row keyframes.  The path
    # owner map prevents any of them being borrowed by another item.
    for sample_id, evidence, session_dir in sequence_rows:
        phase = _phase_for_index(len(image_candidates))
        round_label = "第二次预松" if item_id == "item_5069" else "第一次预紧" if item_id == "install_1st" else None

        # Positive observations often carry a small set of cropped visual
        # artifacts.  Prefer those real frames before the broad session
        # collection so a confirmed check is linked to the artifact that
        # actually supported it.
        supporting = evidence.get("supporting_artifacts", []) if isinstance(evidence, Mapping) else []
        if isinstance(supporting, (list, tuple)):
            for raw_support in supporting:
                if not isinstance(raw_support, str):
                    continue
                support_path = Path(raw_support)
                if support_path.suffix.lower() not in VISUAL_IMAGE_SUFFIXES:
                    continue
                image_text = str(support_path)
                if image_text in seen_paths:
                    continue
                if image_text in path_owners and path_owners[image_text] != item_id:
                    continue
                seen_paths.add(image_text)
                image_candidates.append(
                    _candidate_record(
                        item_id,
                        sample_id,
                        evidence,
                        kind="artifact_frame",
                        phase=phase,
                        source_path=support_path,
                        evidence_id_suffix=image_text,
                        order_index=len(image_candidates) + 1,
                        round_label=round_label,
                    )
                )
                if len(image_candidates) >= 30:
                    break
        if len(image_candidates) >= 30:
            break
        for image in _session_images(session_dir)[:12]:
            image_text = str(image)
            if image_text in seen_paths:
                continue
            if image_text in path_owners and path_owners[image_text] != item_id:
                continue
            seen_paths.add(image_text)
            image_candidates.append(
                _candidate_record(
                    item_id,
                    sample_id,
                    {},
                    kind="artifact_frame",
                    phase=phase,
                    source_path=image,
                    evidence_id_suffix=image_text,
                    order_index=len(image_candidates) + 1,
                    round_label=round_label,
                )
            )
            if len(image_candidates) >= 30:
                break
        if len(image_candidates) >= 30:
            break

    # Timestamp evidence is metadata-only and deliberately has no image URI.
    timestamp_candidates = [
        _candidate_record(
            item_id,
            sample_id,
            evidence,
            kind="timestamp",
            source_path=Path(f"timestamp://{item_id}/{_digest(sample_id + str(_timestamp_seconds(evidence)))}"),
            evidence_id_suffix=f"timestamp-{sample_id}",
        )
        for sample_id, evidence, _ in sequence_rows
        if _timestamp_seconds(evidence) is not None
    ]

    definition = next(d for d in ITEM_DEFINITIONS if d["item_id"] == item_id)
    slot_map: dict[str, list[dict[str, Any]]] = {}
    binding_evidence: list[dict[str, Any]] = []
    for slot in _all_slots(item):
        slot_id = str(slot.get("slot_id") or "")
        chosen = _slot_candidates(slot_id, image_candidates, timestamp_candidates, path_owners, item_id)
        slot_map[slot_id] = chosen
        slot["status"] = "bound" if chosen else "empty"
        slot["evidence"] = chosen
        for evidence in chosen:
            if evidence not in binding_evidence:
                binding_evidence.append(evidence)

    # Slot iteration follows the schema, not the visual sequence.  Keep the
    # shared evidence list chronological so cards, drawer and lightbox all
    # tell the same story.  Timestamp-only records remain after image frames.
    binding_evidence.sort(
        key=lambda evidence: (
            1 if evidence.get("kind") == "timestamp" else 0,
            evidence.get("order_index") is None,
            int(evidence.get("order_index") or 0),
            _timestamp_seconds(evidence) if _timestamp_seconds(evidence) is not None else float("inf"),
            str(evidence.get("evidence_id") or ""),
        )
    )

    confidence_values = [float(row[1].get("confidence") or 0.55) for row in selected_rows]
    confidence = statistics.mean(confidence_values) if confidence_values else 0.0
    required_total = len(definition["required_slots"])
    required_bound = sum(1 for slot_id in definition["required_slots"] if slot_map.get(slot_id))
    state = _state_for(confidence, required_bound, required_total)
    # Keep a deterministic mixture useful for the replay.  The two positive
    # artifact-backed object/installation examples are complete; item 8 shows
    # the in-progress lifecycle even when a keyframe is available.
    if item_id == "item_5069":
        state = "证据生成中"
    elif item_id in {"cylinder_head", "install_gasket"} and any(_row_is_direct_positive(row) for row in selected_rows):
        state = "已完成评分"
    elif state != "证据生成中":
        # A high-confidence negative or mixed source judgment is not a pass.
        # Keep it visible as a manual-review terminal so the score remains 0.
        state = "待人工确认"
    elif state == "证据生成中" and confidence < 0.50:
        state = "待人工确认"
    if state == "证据生成中":
        detail_state = "analyzing"
        checks: list[dict[str, Any]] = []
        unresolved = ""
    else:
        detail_state = "unlocked"
        checks = _criterion_checks(item_id, selected_rows, slot_map, state)
        unresolved = "仍有核验项缺少完整画面，建议人工复核后确认。" if state == "待人工确认" else ""

    sequence_seconds = [
        seconds
        for _sample_id, evidence, _session_dir in sequence_rows
        for seconds in [_timestamp_seconds(evidence)]
        if seconds is not None
    ]
    first_evidence = sequence_rows[0][1] if sequence_rows else {}
    first_seconds = min(sequence_seconds) if sequence_seconds else _timestamp_seconds(first_evidence)
    last_seconds = max(sequence_seconds) if sequence_seconds else first_seconds
    # A single timestamp still gets a small visible window; with multiple
    # observations the range spans the actual earliest/latest frame.
    end_seconds = (
        last_seconds
        if last_seconds is not None and first_seconds is not None and last_seconds > first_seconds
        else first_seconds + 8.0 if first_seconds is not None else None
    )
    completed_item = deepcopy(item)
    completed_item["live_binding"] = {
        "state": state,
        "revision": 1,
        "changed_slot_ids": [str(slot.get("slot_id")) for slot in _all_slots(completed_item) if slot.get("status") == "bound"],
        "live_timestamp": _format_timestamp(first_seconds),
        "live_start_sec": first_seconds,
        "live_end_sec": end_seconds,
        "time_source": "mock_live_stream",
        "time_confidence": round(confidence, 3) if selected_rows else None,
        "evidence_explanation": (
            "对象、动作和时序画面已整理。" if state == "已完成评分" else "相关画面已提取，仍需人工确认。" if state == "待人工确认" else "正在整理当前项目的连续画面。"
        ),
        "evidence": binding_evidence,
    }
    completed_item["detail_evaluation"] = {
        "state": detail_state,
        "updated_at": _format_timestamp(first_seconds) if state != "证据生成中" else None,
        "checks": checks,
        "unresolved_summary": unresolved,
    }
    if state == "已完成评分":
        completed_item["detail_evaluation"]["high_level_evaluation"] = "相关画面已整理，逐项核验状态见下方。"
    completed_item["score"] = {"已完成评分": 1, "待人工确认": 0}.get(state)

    # The on-disk fixture starts empty; this complete item is delivered by the
    # replay event after the user starts evaluation.
    item["live_binding"] = _empty_binding()
    item["detail_evaluation"] = {"state": "locked", "updated_at": None, "checks": [], "unresolved_summary": ""}
    item["score"] = None
    _empty_slots(item)

    event = {
        "event_id": f"evt-{item_id}",
        "item_id": item_id,
        "delay_ms": 1150 + (900 if item.get("difficulty") == "difficult" else 250),
        "processing_ms": 1900 if item.get("difficulty") == "difficult" else 1200,
        "final_state": state,
        "evidence_ids": [str(evidence["evidence_id"]) for evidence in binding_evidence],
        "item_patch": completed_item,
    }
    return item, event


def build_mock(template: Mapping[str, Any], source_run: Path) -> dict[str, Any]:
    source_run = source_run.resolve()
    summaries = sorted(source_run.glob("task*/**/reports/scoring_report_summary.json"))
    if len(summaries) != 10:
        raise ValueError(f"期望 10 个视频报告，实际找到 {len(summaries)} 个：{source_run}")

    payload = validated_copy(template)
    tool_profile = build_profile(source_run, expected_samples=10)
    profile_items = tool_profile.get("items", {}) or {}
    for item in payload["items"]:
        item_profile = profile_items.get(item["item_id"])
        if not isinstance(item_profile, Mapping):
            raise ValueError(f"缺少评分项工具 profile：{item['item_id']}")
        item["difficulty"] = item_profile["difficulty"]
        item["difficulty_label"] = item_profile["difficulty_label"]
        item["analysis_profile"] = deepcopy(item_profile["analysis_profile"])
        item["analysis_tools"] = deepcopy(item_profile["analysis_tools"])

    payload["demo_mode"] = "mock_live_stream"
    payload["presentation"]["initial_state"] = "正在接入视频流"
    payload["_mock_audit"] = {
        "fixture_label": "ten_video_fixture",
        "source_run": str(source_run),
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
        _, event = _build_item_event(item, by_item.get(str(item["item_id"]), []), path_owners)
        events.append(event)
    payload["events"] = events
    return validated_copy(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 10 个 Engine artifacts 构建模拟实时报告 JSON")
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_mock(_read_json(args.template), args.source_run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 mock JSON：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
