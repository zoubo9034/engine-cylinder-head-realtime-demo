#!/usr/bin/env python3
"""Build a visual-only live replay from real Engine report artifacts.

The artifacts are used as a bounded source for a demonstration fixture.  A
mock item is sourced from one complete-score video and its own item-analysis
frames; the selection can be seeded for a reproducible checked-in fixture.
Long descriptions, sample names and paths are retained only in the private
generation audit; captions and detail checks sent to the browser are short
visual statements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlparse

from detail_rules import criterion_map, prefilled_result_for
from report_schema import ITEM_DEFINITIONS, load_tool_profile, template_payload, validated_copy
from workflow_tool_stats import build_profile


POSITIVE_JUDGMENTS = {"正确", "满足", "通过", "correct", "confirmed", "pass", "passed"}
VISUAL_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ROOT = Path(__file__).resolve().parent
# Mock replay is deliberately much shorter than the live service's 8–20 second
# analysis window.  Keep this constant local to the mock generator so changing
# a fixture can never alter the real-time service contract in ``serve_demo``.
MOCK_ANALYSIS_DURATION_MS = 3_000
SUPPORTED_SOURCE_COUNTS = (10, 29)
# Flat Engine exports put several item keyframes in the same analyzer
# directory.  A small neighbourhood can complete an action sequence, while
# a wide neighbourhood silently pulls frames from the next scoring item.
# The source reports normally expose one anchor frame per item.  Only a short
# continuation of that anchor is considered a process sequence; a long scan
# through a shared ``keyframes/a1`` directory can silently enter the next
# operation.  This is independent from the mock event's three-second
# processing window below.
PROCESS_FRAME_NEIGHBOURHOOD_SECONDS = 4.0
VISUALIZATION_MATCH_TOLERANCE_SECONDS = 1.0
MAX_SESSION_IMAGE_INDEX = 4_000


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - surfaced by the CLI
        raise ValueError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _iter_summary_evidence(summary: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield item evidence from both legacy and enriched report layouts.

    Engine artifacts have existed in two closely related shapes: some reports
    put evidence on the category, while newer reports also repeat it on the
    item record and in ``all_evidence``.  The mock must see the complete item
    analysis stream, but duplicate records must not create duplicate cards.
    """
    seen: set[tuple[str, str, str, str, str, str]] = set()

    def emit(value: Any) -> Iterable[dict[str, Any]]:
        if not isinstance(value, dict):
            return
        signature = (
            str(value.get("item") or ""),
            str(value.get("timestamp_sec") or ""),
            str(value.get("timestamp") or ""),
            str(value.get("keyframe_path") or value.get("keyframe") or ""),
            str(value.get("status") or value.get("judgment") or ""),
            str(value.get("description") or value.get("reason") or ""),
        )
        if signature in seen:
            return
        seen.add(signature)
        yield value

    breakdown = summary.get("breakdown") or {}
    if isinstance(breakdown, Mapping):
        for category in breakdown.values():
            if not isinstance(category, Mapping):
                continue
            for evidence in category.get("evidence", []) or []:
                yield from emit(evidence)
            nested = category.get("breakdown") or {}
            if isinstance(nested, Mapping):
                for record in nested.values():
                    if not isinstance(record, Mapping):
                        continue
                    for evidence in record.get("evidence", []) or []:
                        yield from emit(evidence)
    for evidence in summary.get("all_evidence", []) or []:
        yield from emit(evidence)


def _iter_intermediate_evidence(session_dir: Path) -> Iterable[dict[str, Any]]:
    """Yield item-scoped findings and their process frames from one session.

    The compact ``scoring_report_summary.json`` normally keeps only one
    representative frame per item.  The adjacent adapter artifacts retain the
    frames that were actually supplied to the visual analysis step (including
    their item tags and signal).  Reading those bounded files makes the mock
    use the analysis process output rather than a generic frame from a nearby
    operation.  The records stay private and are never rendered as prose.
    """
    active_ids = {str(definition["item_id"]) for definition in ITEM_DEFINITIONS}
    roots = [Path(session_dir) / "intermediate", Path(session_dir) / "artifacts" / "evidence_enrichment"]
    paths: list[Path] = []
    seen_paths: set[Path] = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            patterns = ("*_adapter_result.json", "*adapter-result.json", "*_result.json")
            for pattern in patterns:
                for path in sorted(root.glob(pattern)):
                    if path in seen_paths or not path.is_file():
                        continue
                    seen_paths.add(path)
                    paths.append(path)
                    if len(paths) >= 160:
                        break
                if len(paths) >= 160:
                    break
        except OSError:
            continue
        if len(paths) >= 160:
            break

    emitted: set[tuple[str, str, str, str, str]] = set()
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping):
            continue
        findings = value.get("findings")
        if not isinstance(findings, (list, tuple)):
            findings = []
        # ``item_tag_map`` is present in some adapter versions even when the
        # normalized ``findings`` list is omitted.  Treat its values as the
        # same item-scoped process records.
        if not findings and isinstance(value.get("item_tag_map"), Mapping):
            flattened: list[Mapping[str, Any]] = []
            for records in value.get("item_tag_map", {}).values():
                if isinstance(records, (list, tuple)):
                    flattened.extend(record for record in records if isinstance(record, Mapping))
            findings = flattened
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            tags: set[str] = set()
            raw_tags = finding.get("tags")
            if isinstance(raw_tags, str):
                tags.add(raw_tags)
            elif isinstance(raw_tags, (list, tuple, set)):
                tags.update(str(tag) for tag in raw_tags)
            raw_item = finding.get("item") or finding.get("item_id")
            if raw_item:
                tags.add(str(raw_item))
            owned = sorted(tags & active_ids)
            if not owned:
                continue
            keyframe = finding.get("keyframe_path") or finding.get("keyframe")
            for item_id in owned:
                record = dict(finding)
                record["item"] = item_id
                if keyframe:
                    record["keyframe"] = keyframe
                    record["keyframe_path"] = keyframe
                signature = (
                    item_id,
                    str(record.get("timestamp_sec") or ""),
                    str(record.get("timestamp") or ""),
                    str(record.get("keyframe_path") or record.get("keyframe") or ""),
                    str(record.get("signal") or record.get("status") or record.get("judgment") or ""),
                )
                if signature in emitted:
                    continue
                emitted.add(signature)
                yield record


def _session_images(session_dir: Path) -> list[Path]:
    """Collect a bounded, deterministic set of real image artifacts.

    This broad collector is used only as a last resort when an item record has
    no explicit frame pointer.  Normal selection goes through the item row's
    keyframe/supporting-artifact paths, which prevents unrelated item frames
    from entering the replay.
    """
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


def _discover_summary_paths(source_run: Path) -> list[Path]:
    """Find report summaries in either a nested 10-video run or a flat run.

    The recent Engine snapshots use ``task*/<video>/reports`` while the older
    29-video artifact export uses ``<video>/reports``.  Keep discovery scoped
    to the supplied source root and de-duplicate paths before validation.
    """
    source_run = source_run.resolve()
    patterns = (
        "task*/**/reports/scoring_report_summary.json",
        "*/reports/scoring_report_summary.json",
    )
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        try:
            paths = sorted(source_run.glob(pattern))
        except OSError:
            paths = []
        for path in paths:
            try:
                if path.is_file() and path not in seen:
                    seen.add(path)
                    found.append(path)
            except OSError:
                continue
    # A caller may provide one more level of grouping around the video
    # sessions.  Only use the recursive scan when the two known layouts found
    # nothing, so a large archive cannot silently inflate a 10/29 fixture.
    if not found:
        try:
            for path in sorted(source_run.glob("**/reports/scoring_report_summary.json")):
                if len(found) >= 1000:
                    break
                try:
                    if path.is_file() and path not in seen:
                        seen.add(path)
                        found.append(path)
                except OSError:
                    continue
        except OSError:
            pass
    return found


def _normalise_video_key(value: Any) -> str:
    """Normalise a URL/path or session name for manifest matching."""
    text = unquote(str(value or "")).strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme and parsed.path:
        text = parsed.path
    stem = Path(text).stem
    # Label Studio exports carry a timestamp and the recording mode in the
    # filename; Engine report video_path values do not.  Remove those wrappers
    # before comparing the two forms.
    stem = re.sub(r"^\d{12,14}_", "", stem)
    stem = re.sub(r"_(?:skill|机器)_?\d+(?:_\d+)?$", "", stem, flags=re.IGNORECASE)
    return "".join(
        character.casefold()
        for character in stem
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


def _manifest_keys(manifest: Path) -> list[str]:
    """Read video keys from a JSON/line manifest without exposing them."""
    values = _manifest_values(manifest)
    keys: list[str] = []
    for value in values:
        candidate = _manifest_video_value(value)
        key = _normalise_video_key(candidate)
        if key and key not in keys:
            keys.append(key)
    return keys


def _manifest_values(manifest: Path) -> list[Any]:
    """Load the small manifest value list once for both selection and votes."""
    try:
        text = manifest.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - CLI diagnostic
        raise ValueError(f"无法读取 mock 视频清单：{manifest}: {exc}") from exc
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(decoded, list):
        return decoded
    if isinstance(decoded, Mapping):
        values = decoded.get("videos") or decoded.get("items") or decoded.get("paths") or []
        return list(values) if isinstance(values, (list, tuple)) else []
    return []


def _manifest_video_value(value: Any) -> Any:
    """Return the URL/path field used to identify one manifest video."""
    if not isinstance(value, Mapping):
        return value
    data = value.get("data") if isinstance(value.get("data"), Mapping) else {}
    return (
        data.get("video_url")
        or data.get("video_path")
        or value.get("video_url")
        or value.get("video_path")
        or value.get("summary_path")
        or value.get("path")
    )


def _score_label_value(label: Any) -> tuple[float | None, bool | None]:
    """Extract a numeric/full-score hint from a manifest label.

    Label Studio exports used by the Engine archive contain labels such as
    ``2分`` and ``0分``.  Other callers may provide textual pass/fail labels;
    those are accepted without making the manifest a requirement for normal
    report generation.  The numeric value is resolved to a full-score vote by
    ``_manifest_positive_items`` after looking at the values for that item.
    """
    text = str(label or "").strip().casefold()
    if not text:
        return None, None
    if any(token in text for token in ("通过", "正确", "满足", "合格", "pass", "passed", "correct", "confirmed", "success")):
        return None, True
    if any(token in text for token in ("不通过", "错误", "不满足", "失败", "fail", "failed", "incorrect")):
        return None, False
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None, None
    try:
        return float(match.group(0)), None
    except ValueError:
        return None, None


def _manifest_positive_items(manifest: Path | None) -> dict[str, set[str]]:
    """Return per-video items with a majority full-score manifest label.

    This is an optional visual-candidate hint for Label Studio-style exports.
    It is deliberately kept separate from the report score authority: when a
    manifest has no positive vote for an item (for example an item omitted by
    a coarse annotation), normal report outcomes remain eligible.  No label
    text or source identity is copied into the public payload.
    """
    if manifest is None:
        return {}
    values = _manifest_values(manifest)
    active_by_number = {
        int(definition["item_number"]): str(definition["item_id"])
        for definition in ITEM_DEFINITIONS
    }
    # key -> item number -> (numeric votes, explicit bool votes)
    votes: dict[str, dict[int, list[tuple[float | None, bool | None]]]] = {}
    for value in values:
        key = _normalise_video_key(_manifest_video_value(value))
        if not key or not isinstance(value, Mapping):
            continue
        item_votes = votes.setdefault(key, {})
        annotations = value.get("annotations") or value.get("labels") or []
        if isinstance(annotations, Mapping):
            annotations = [annotations]
        for annotation in annotations if isinstance(annotations, (list, tuple)) else []:
            results = annotation.get("result", []) if isinstance(annotation, Mapping) else []
            if not results and isinstance(annotation, Mapping):
                results = annotation.get("results", []) or []
            for result in results if isinstance(results, (list, tuple)) else []:
                if not isinstance(result, Mapping):
                    continue
                match = re.fullmatch(r"score-(\d+)", str(result.get("from_name") or "").strip(), flags=re.IGNORECASE)
                if not match:
                    continue
                item_number = int(match.group(1))
                if item_number not in active_by_number:
                    continue
                raw_value = result.get("value")
                labels: list[Any] = []
                if isinstance(raw_value, Mapping):
                    for field in ("choices", "labels", "value", "text"):
                        candidate = raw_value.get(field)
                        if isinstance(candidate, (list, tuple)):
                            labels.extend(candidate)
                        elif candidate is not None:
                            labels.append(candidate)
                elif raw_value is not None:
                    labels.append(raw_value)
                for label in labels[:1]:
                    numeric, explicit = _score_label_value(label)
                    if numeric is not None or explicit is not None:
                        item_votes.setdefault(item_number, []).append((numeric, explicit))

    result: dict[str, set[str]] = {}
    for key, item_map in votes.items():
        positive_items: set[str] = set()
        for item_number, entries in item_map.items():
            if not entries:
                continue
            numeric_values = [numeric for numeric, explicit in entries if numeric is not None]
            # For numeric labels, the largest observed positive value denotes
            # the full score in this manifest.  This handles both 0/1 and
            # 0/2 annotation schemes without hard-coding a rubric score.
            full_value = max(numeric_values) if numeric_values else None
            positives = 0
            negatives = 0
            for numeric, explicit in entries:
                if explicit is True or (numeric is not None and full_value is not None and numeric == full_value and numeric > 0):
                    positives += 1
                elif explicit is False or (numeric is not None and (numeric <= 0 or (full_value is not None and numeric < full_value))):
                    negatives += 1
            if positives > negatives and positives:
                positive_items.add(active_by_number[item_number])
        if positive_items:
            result[key] = positive_items
    return result


def _select_manifest_summaries(
    summary_paths: list[Path],
    *,
    manifest: Path | None,
) -> list[Path]:
    """Restrict a larger flat archive to one report per manifest video."""
    if manifest is None:
        return summary_paths
    keys = _manifest_keys(manifest)
    if not keys:
        raise ValueError(f"mock 视频清单没有可用视频：{manifest}")
    metadata: dict[Path, tuple[str, str]] = {}
    for path in summary_paths:
        try:
            summary = _read_json(path)
        except ValueError:
            continue
        video_key = _normalise_video_key(summary.get("video_path"))
        if not video_key:
            video_key = _normalise_video_key(path.parent.parent.name)
        metadata[path] = (video_key, path.parent.parent.name)
    selected: list[Path] = []
    used: set[Path] = set()
    for key in keys:
        matches = [path for path, (video_key, _name) in metadata.items() if video_key == key]
        if not matches:
            # Be tolerant of punctuation differences left after URL decoding.
            matches = [
                path
                for path, (video_key, _name) in metadata.items()
                if key in video_key or video_key in key
            ]
        if not matches:
            continue
        # If a video was scored more than once, prefer the newer dated export
        # and then a stable lexical order.  This still leaves the per-item
        # choice random; it only chooses the report representing the video.
        matches.sort(
            key=lambda path: (
                0 if re.search(r"202605(?:1[4-9]|2\d)", path.parent.parent.name) else 1,
                path.parent.parent.name,
            )
        )
        chosen = next((path for path in matches if path not in used), matches[0])
        if chosen not in used:
            selected.append(chosen)
            used.add(chosen)
    return selected


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


def _item_outcomes(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract the authoritative per-item score records from a summary."""
    outcomes: dict[str, dict[str, Any]] = {}
    breakdown = summary.get("breakdown") or {}
    if not isinstance(breakdown, Mapping):
        return outcomes
    active_ids = {str(definition["item_id"]) for definition in ITEM_DEFINITIONS}
    for category in breakdown.values():
        if not isinstance(category, Mapping):
            continue
        nested = category.get("breakdown") or {}
        if not isinstance(nested, Mapping):
            continue
        for item_id, record in nested.items():
            item_key = str(item_id)
            if item_key in active_ids and isinstance(record, Mapping):
                outcomes[item_key] = dict(record)
    return outcomes


def _outcome_is_correct(outcome: Mapping[str, Any] | None) -> bool:
    """Whether a source video received the complete score for one item.

    The final item record is the selection authority.  Evidence judgments are
    intentionally *not* used to turn a failed/partial video into a successful
    one; they are used only to order the process frames after a correct video
    has been chosen.
    """
    if not isinstance(outcome, Mapping):
        return False
    try:
        score = float(outcome.get("score"))
        maximum = float(outcome.get("max_score"))
        if maximum > 0 and score >= maximum:
            return True
    except (TypeError, ValueError):
        pass
    return str(outcome.get("status") or "").strip().casefold() in {
        "pass",
        "passed",
        "success",
        "confirmed",
        "complete",
    }


def _evidence_is_positive(evidence: Mapping[str, Any]) -> bool:
    status = str(evidence.get("status") or "").strip().casefold()
    judgment = str(evidence.get("judgment") or "").strip().casefold()
    signal = str(evidence.get("signal") or evidence.get("finding_signal") or "").strip().casefold()
    positive_statuses = {"pass", "passed", "positive", "confirmed", "success", "complete"}
    positive_judgments = {value.casefold() for value in POSITIVE_JUDGMENTS}
    positive_signals = {"positive", "pass", "passed", "confirmed", "success", "complete"}
    return (
        status in positive_statuses
        or judgment in positive_judgments
        or signal in positive_signals
    )


def _path_is_image(path: Path) -> bool:
    try:
        return path.is_file() and path.suffix.lower() in VISUAL_IMAGE_SUFFIXES
    except OSError:
        return False


def _session_image_index(session_dir: Path) -> list[Path]:
    """Return image artifacts physically contained in one source session.

    A report can be produced on a different mount and retain an absolute
    ``keyframe_path`` that no longer exists verbatim.  Building a small index
    lets the resolver recover the same path by its session-local suffix (for
    example ``keyframes/a1/keyframe_00128s.jpg``), while keeping all recovery
    inside the selected video directory.  The cap is intentional: mock
    generation must never turn a large archive into an unbounded recursive
    walk.
    """
    session_dir = Path(session_dir)
    roots = [session_dir / "keyframes", session_dir / "visualizations", session_dir / "artifacts"]
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            # Direct keyframes/visualizations are cheap and are always scanned
            # first.  Artifacts may contain thousands of crops, so the shared
            # cap applies across all roots.
            for suffix in sorted(VISUAL_IMAGE_SUFFIXES):
                for path in sorted(root.glob(f"**/*{suffix}")):
                    if len(found) >= MAX_SESSION_IMAGE_INDEX:
                        return found
                    try:
                        resolved = path.resolve()
                        if resolved not in seen and path.is_file():
                            seen.add(resolved)
                            found.append(resolved)
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _path_parts(value: str) -> tuple[str, ...]:
    value = unquote(value.replace("\\", "/")).strip()
    return tuple(part for part in value.split("/") if part not in {"", "."})


def _resolve_image_path(
    raw: Any,
    session_dir: Path,
    image_index: Iterable[Path] | None = None,
) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    # Avoid interpreting diagnostic prose that happens to contain a filename
    # as an artifact pointer.
    if any(character in value for character in ("\n", "\r", "\t")):
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = session_dir / path
    if _path_is_image(path):
        return path.resolve()

    # Reports copied between workers often retain an old absolute prefix or a
    # stage-run prefix (``15/keyframes/...``).  Recover the path directly from
    # the selected session whenever possible.  This avoids scanning thousands
    # of rich crops for every field in a task result and, importantly, keeps
    # recovery inside this one video directory.
    wanted = _path_parts(value)
    if not wanted:
        return None
    session_dir = Path(session_dir)
    session_name = session_dir.name
    # Absolute artifact paths normally contain the selected session directory
    # verbatim.  Preserve everything after that component.
    for index, part in enumerate(wanted):
        if part == session_name and index + 1 < len(wanted):
            candidate = session_dir.joinpath(*wanted[index + 1 :])
            if _path_is_image(candidate):
                return candidate.resolve()
    # A few exports omit the session component but retain a stable root such
    # as ``artifacts/`` or ``keyframes/``.  Try the longest such suffix before
    # falling back to the bounded index.
    root_markers = {"artifacts", "keyframes", "visualizations", "intermediate"}
    marker_positions = [index for index, part in enumerate(wanted) if part.casefold() in root_markers]
    for index in reversed(marker_positions):
        candidate = session_dir.joinpath(*wanted[index:])
        if _path_is_image(candidate):
            return candidate.resolve()

    # Match only a suffix within the selected session, never a sibling video
    # directory.  ``image_index`` is normally a cached list; do not copy it on
    # every lookup because rich task results can contain hundreds of refs.
    if image_index is None:
        candidates = _session_image_index(session_dir)
    elif isinstance(image_index, list):
        candidates = image_index
    else:
        candidates = list(image_index)
    ranked: list[tuple[int, int, str, Path]] = []
    for candidate in candidates:
        candidate_parts = _path_parts(str(candidate))
        if not candidate_parts:
            continue
        score = 0
        if len(wanted) <= len(candidate_parts) and candidate_parts[-len(wanted) :] == wanted:
            score = 3
        elif candidate_parts[-1:] == wanted[-1:]:
            # Basename-only recovery is a last resort.  Prefer the candidate
            # whose directory also carries the same keyframe/visualization
            # marker so duplicate names remain deterministic.
            score = 1
            if len(wanted) >= 2 and wanted[-2] in candidate_parts:
                score = 2
        if score:
            ranked.append((score, -len(candidate_parts), str(candidate), candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    return ranked[0][3] if _path_is_image(ranked[0][3]) else None


def _artifact_seconds(path: Path) -> float | None:
    """Read a timestamp encoded in common Engine artifact filenames."""
    name = path.name
    matches = list(re.finditer(r"(\d+(?:\.\d+)?)(ms|s)(?:\D|$)", name, flags=re.IGNORECASE))
    if not matches:
        return None
    match = matches[-1]
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value / 1000.0 if match.group(2).casefold() == "ms" else value


def _supporting_image_paths(
    evidence: Mapping[str, Any],
    session_dir: Path,
    image_index: Iterable[Path] | None = None,
) -> list[Path]:
    """Return exact image artifacts attached to one evidence row."""
    paths: list[Path] = []
    seen: set[Path] = set()
    # Some rich task results contain large nested diagnostics (sampling plans,
    # image-quality records and model observations).  Walking every value in
    # those objects made mock generation both slow and prone to accidentally
    # treating prose as a file reference.  Keep this collector deliberately
    # shallow and path-key aware; task-result parsing below supplies the few
    # structured fields that need deeper handling.
    visited = 0
    max_nodes = 2_000
    max_paths = 96
    path_key_tokens = (
        "path",
        "image",
        "frame",
        "mask",
        "bbox",
        "box",
        "overlay",
        "crop",
        "artifact",
        "visual",
    )

    def visit(value: Any, *, key_hint: str = "", depth: int = 0) -> None:
        nonlocal visited
        if visited >= max_nodes or len(paths) >= max_paths or depth > 7:
            return
        visited += 1
        if isinstance(value, str):
            # A path-like key is required for strings nested in a mapping.
            # Top-level supporting-artifact lists are passed with an empty
            # hint and are allowed because their values are already scoped.
            if key_hint and not any(token in key_hint for token in path_key_tokens):
                return
            path = _resolve_image_path(value, session_dir, image_index)
            if path is not None and path not in seen:
                seen.add(path)
                paths.append(path)
            return
        if isinstance(value, Mapping):
            for key, nested in value.items():
                key_text = str(key).casefold()
                # Image-quality metadata and free-form observations cannot
                # contain useful artifact pointers for this collector.
                if key_text in {"image_quality", "raw_observations", "description", "reason", "issues", "observations"}:
                    continue
                visit(nested, key_hint=key_text, depth=depth + 1)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                visit(nested, key_hint=key_hint, depth=depth + 1)

    # Supporting artifacts are preferred over a generic scorer keyframe.  The
    # latter remains a valid process frame when no mask/box/crop was emitted.
    preferred_keys = (
        "supporting_artifacts",
        "supporting_images",
        "artifact_paths",
        "artifacts",
        "keyframe_path",
        "keyframe",
    )
    for key in preferred_keys:
        visit(evidence.get(key), key_hint=str(key).casefold())

    # Enriched Engine rows use several names for detector output.  Inspect
    # only image/artifact-like fields so prose, URLs and descriptions cannot
    # accidentally become an image pointer.
    image_key_tokens = (
        "image",
        "frame",
        "mask",
        "bbox",
        "box",
        "overlay",
        "crop",
        "artifact",
        "visual",
    )
    for key, value in evidence.items():
        key_text = str(key).casefold()
        if key in preferred_keys or any(token in key_text for token in image_key_tokens):
            visit(value, key_hint=key_text)
    return paths


def _session_index_for(session: Mapping[str, Any]) -> list[Path]:
    """Get (and lazily cache) the image index for a loaded session."""
    cached = session.get("image_index")
    if isinstance(cached, list):
        return [path for path in cached if isinstance(path, Path)]
    session_dir = Path(str(session.get("session_dir") or ""))
    index = _session_image_index(session_dir)
    # ``session`` is a mutable dict in normal generation.  Keep this guarded
    # for callers that pass a read-only Mapping in unit probes.
    try:
        session["image_index"] = index  # type: ignore[index]
    except (TypeError, AttributeError):
        pass
    return index


TASK_RESULT_SCAN_LIMIT = 500
TASK_RESULT_IMAGE_LIMIT = 72
TASK_RESULT_FRAME_LIMIT = 12
TASK_RESULT_CROP_LIMIT = 16
PROCESS_SEGMENT_SCAN_LIMIT = 180
PROCESS_SEGMENT_FRAME_LIMIT = 36
PROCESS_SEGMENT_TOLERANCE_SECONDS = 0.25

# These hints are used only to rank private source artifacts.  They never
# leave the generator and are intentionally phrased as observable actions or
# objects, so a coarse report row can still be matched to the right process
# frame when its status field is missing.
_ITEM_PROCESS_HINTS: dict[str, tuple[str, ...]] = {
    "item_5069": ("第二次", "预松", "180", "180°", "pointer", "wrench", "扳手"),
    "cylinder_head": ("气缸盖", "垫块", "支架", "放置", "下降", "place", "pad"),
    "gasket_remove": ("取下", "移除", "脱离", "气缸垫", "gasket", "remove", "lift"),
    "gasket_inspect": ("检查", "孔位", "边缘", "表面", "正面", "反面", "inspect", "gasket"),
    "positioning": ("定位销", "两枚", "pin", "position", "检查"),
    "clean_head": ("清洁", "擦拭", "气缸盖", "结合面", "无纺布", "布料", "wipe", "clean"),
    "clean_block": ("清洁", "擦拭", "气缸体", "气缸孔", "结合面", "无纺布", "布料", "wipe", "clean"),
    "clean_gasket": ("清洁", "擦拭", "气缸垫", "正面", "反面", "翻面", "布", "wipe", "clean"),
    "clean_pins": ("清洁", "擦拭", "定位销", "两枚", "pin", "wipe", "clean"),
    "report_gasket": ("报告", "更换", "气缸垫", "待用", "节点", "安装前", "gasket"),
    "install_gasket": ("安装", "气缸垫", "垫片", "孔位", "定位销", "落座", "对准", "gasket", "install"),
    "cylinder_head_bolt": ("螺栓", "气缸盖", "新", "待用", "报告", "安装", "bolt"),
    "install_1st": ("第一次", "预紧", "扭力", "扭矩", "1", "10", "25", "螺栓", "wrench", "tighten"),
}
_ITEM_PROCESS_EXCLUSIONS: tuple[str, ...] = (
    "未找到",
    "未看到",
    "未见",
    "没有",
    "无法",
    "缺乏",
    "不涉及",
    "无直接",
    "不清楚",
    "not found",
    "no direct",
    "no evidence",
    "not explicitly",
    "not clear",
    "doesn't",
    "don't",
    "absence",
    "unrelated",
    "未显示",
    "未出现",
    "未看到",
    "未明确",
    "无法确认",
    "不明确",
    "可能是",
    "可能为",
    "cannot",
    "unable",
    "unrelated",
    "negative",
)
_TASK_POSITIVE_STATUSES = {"success", "pass", "passed", "confirmed", "complete", "completed", "supported"}
_TASK_NEGATIVE_STATUSES = {"failed", "fail", "unsupported", "not_confirmed", "not supported", "negative", "unrelated"}


def _string_values(value: Any) -> list[str]:
    """Flatten a small claim/identifier field without traversing diagnostics."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(entry) for entry in value if isinstance(entry, (str, int, float))]
    return []


def _task_claim_values(value: Mapping[str, Any]) -> list[str]:
    claims: list[str] = []
    for key in ("claim_ids", "item_id", "item", "items", "item_ids", "criterion_ids"):
        claims.extend(_string_values(value.get(key)))
    return claims


def _owned_task_items(value: Mapping[str, Any], task_path: Path | None = None) -> tuple[set[str], bool]:
    """Return exact scored-item ownership for a task artifact.

    A criterion such as ``a1.clean_block.pass`` is accepted because the item
    token is an exact dot-separated component.  Loose substring matching is
    deliberately avoided: ``clean_head`` must never claim a ``clean_head_bolt``
    artifact, and artifacts for another item cannot supply this item's frame.
    """
    active = {str(definition["item_id"]) for definition in ITEM_DEFINITIONS}
    owned: set[str] = set()
    exact = False
    values = _task_claim_values(value)
    for raw in values:
        text = str(raw).strip()
        pieces = {piece for piece in re.split(r"[^A-Za-z0-9_]+", text) if piece}
        for item_id in active:
            if text == item_id or item_id in pieces:
                owned.add(item_id)
                exact = True
    # Some adapter snapshots keep the finding's item only in the filename or
    # in a sibling task directory.  Use that fallback only when the path part
    # is itself an exact item token; never infer ownership from a description.
    if not owned and task_path is not None:
        for part in task_path.parts:
            if part in active:
                owned.add(part)
                exact = True
    return owned, exact


def _normalised_status(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _task_positive(value: Mapping[str, Any]) -> bool:
    status = _normalised_status(value.get("status"))
    evidence_status = _normalised_status(value.get("evidence_status"))
    if status in _TASK_NEGATIVE_STATUSES or evidence_status in _TASK_NEGATIVE_STATUSES:
        return False
    if status in _TASK_POSITIVE_STATUSES or evidence_status in _TASK_POSITIVE_STATUSES:
        return evidence_status not in {"insufficient", "unsupported", "not_confirmed", "failed"}
    return False


def _task_visual_candidate(value: Mapping[str, Any]) -> bool:
    """Whether an image-bearing task is a useful visual fallback.

    Rich runs may mark a task ``insufficient`` because a separate requirement
    was unresolved even though its sampled frames clearly show the object and
    motion needed by this visual-only demo.  Such a record is never treated as
    a report score; it is merely ranked below a supported task.
    """
    status = _normalised_status(value.get("status"))
    evidence_status = _normalised_status(value.get("evidence_status"))
    if status in _TASK_NEGATIVE_STATUSES or evidence_status in _TASK_NEGATIVE_STATUSES:
        return False
    if status in {"insufficient", "pending", "analyzing", ""} or evidence_status in {"insufficient", "pending", "analyzing", ""}:
        visual_fields = (
            "object_visible",
            "action_observed",
            "contact_observed",
            "motion_supported",
            "rotation_supported",
            "projected_angle_supported",
            "verified_checks",
            "visible_targets",
        )
        return any(bool(value.get(field)) for field in visual_fields)
    return False


def _task_confidence(value: Mapping[str, Any]) -> float:
    for key in ("confidence", "motion_observed_confidence", "localization_confidence", "rerank_score"):
        candidate = value.get(key)
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return max(0.0, min(1.0, float(candidate)))
    return 0.0


def _task_timestamp(value: Any) -> float | None:
    if isinstance(value, Mapping):
        for key in ("time_sec", "timestamp_sec", "seconds", "timestamp", "time"):
            candidate = _timestamp_seconds({key: value.get(key)}) if key in value else None
            if candidate is not None:
                return candidate
        for key in ("source_time_sec", "source_timestamp_sec"):
            candidate = value.get(key)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return float(candidate)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _task_image_entries(
    value: Mapping[str, Any],
    task_path: Path,
    session_dir: Path,
    image_index: Iterable[Path],
) -> list[dict[str, Any]]:
    """Extract process images and their source timestamps from one task."""
    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    frame_count = 0
    crop_count = 0

    def add(raw: Any, *, kind: str, timestamp: float | None = None, role: str = "") -> None:
        nonlocal frame_count, crop_count
        if len(entries) >= TASK_RESULT_IMAGE_LIMIT or not isinstance(raw, str):
            return
        path = _resolve_image_path(raw, session_dir, image_index)
        if path is None or path in seen:
            return
        is_crop = kind == "object_detection" or "crop" in kind
        if is_crop and crop_count >= TASK_RESULT_CROP_LIMIT:
            return
        if not is_crop and frame_count >= TASK_RESULT_FRAME_LIMIT:
            return
        seen.add(path)
        if is_crop:
            crop_count += 1
        else:
            frame_count += 1
        if timestamp is None:
            timestamp = _artifact_seconds(path)
        entries.append({
            "path": path,
            "kind": kind,
            "timestamp_sec": timestamp,
            "role": str(role or ""),
        })

    def add_structured(raw: Any, *, default_kind: str, time_keys: tuple[str, ...] = ()) -> None:
        if isinstance(raw, Mapping):
            timestamp = _task_timestamp(raw)
            role = raw.get("sample_role") or raw.get("sample_source") or raw.get("role") or raw.get("region_type") or ""
            for key in ("frame_path", "source_frame_path", "image_path", "crop_path", "mask_path", "bbox_path", "overlay_path", "path"):
                candidate = raw.get(key)
                if isinstance(candidate, str):
                    kind = default_kind
                    key_text = key.casefold()
                    if "crop" in key_text or "mask" in key_text or "bbox" in key_text or "box" in key_text or "overlay" in key_text:
                        kind = "object_detection"
                    elif "strip" in candidate.casefold() or "sequence" in candidate.casefold():
                        kind = "multi_frame_sequence"
                    add(candidate, kind=kind, timestamp=timestamp, role=str(role))
            return
        if isinstance(raw, str):
            add(raw, kind=default_kind, timestamp=None)

    # The structured lists retain the analysis sampler's ordering and timing.
    frames = value.get("supporting_frames")
    if isinstance(frames, (list, tuple)):
        for frame in frames:
            add_structured(frame, default_kind="representative_frame")
    crops = value.get("supporting_crops")
    if isinstance(crops, (list, tuple)):
        for crop in crops:
            add_structured(crop, default_kind="object_detection")

    # Artifact lists may contain frame strips, masks, bbox overlays and the
    # original sampled frames.  Traverse only path-bearing keys and stop as
    # soon as the bounded image budget is reached.
    def visit(raw: Any, key_hint: str = "", depth: int = 0) -> None:
        if len(entries) >= TASK_RESULT_IMAGE_LIMIT or depth > 6:
            return
        if isinstance(raw, str):
            if Path(raw).suffix.casefold() not in VISUAL_IMAGE_SUFFIXES:
                return
            hint = key_hint.casefold()
            kind = "object_detection" if any(token in hint for token in ("crop", "mask", "bbox", "box", "overlay", "segmentation")) else "artifact_frame"
            if "strip" in raw.casefold() or "sequence" in raw.casefold():
                kind = "multi_frame_sequence"
            add(raw, kind=kind)
            return
        if isinstance(raw, Mapping):
            for key, nested in raw.items():
                key_text = str(key).casefold()
                if key_text in {"task_dir", "sampling_plan", "image_quality", "raw_observations", "description", "reason", "observations", "issues"}:
                    continue
                if any(token in key_text for token in ("path", "image", "frame", "mask", "bbox", "box", "overlay", "crop", "artifact", "visual")):
                    visit(nested, key_text, depth + 1)
            return
        if isinstance(raw, (list, tuple)):
            for nested in raw:
                visit(nested, key_hint, depth + 1)

    visit(value.get("supporting_artifacts"), "supporting_artifacts")
    visit(value.get("artifact_paths"), "artifact_paths")
    # A task result can keep the image pointer under one of these aliases.
    for key in ("images", "frames", "crops", "masks", "bboxes", "overlays", "visualizations"):
        if key in value:
            visit(value.get(key), key)

    # Some object-motion records only point to a frame strip in the result's
    # sibling directory.  Add direct siblings as a final, task-local fallback
    # (never the session-wide keyframe directory).
    if not entries:
        try:
            siblings = sorted(task_path.parent.glob("*"))
        except OSError:
            siblings = []
        for path in siblings:
            if _path_is_image(path):
                add(str(path), kind="multi_frame_sequence" if "strip" in path.name.casefold() else "artifact_frame")
                if len(entries) >= 8:
                    break
    return entries


def _adapter_result_paths(session_dir: Path) -> list[Path]:
    """Return a bounded list of adapter outputs for one source video.

    Both the flat archive and nested Engine runs keep the adapter JSON beside
    the report.  Keeping discovery in one helper ensures that the item-task
    parser and the process-frame parser inspect exactly the same files and do
    not accidentally walk a sibling video directory.
    """
    roots = (session_dir / "intermediate", session_dir / "artifacts" / "evidence_enrichment")
    patterns = (
        "*_adapter_result.json",
        "*adapter-result.json",
        "**/*_adapter_result.json",
        "**/adapter-result.json",
    )
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if len(paths) >= PROCESS_SEGMENT_SCAN_LIMIT:
            break
        try:
            if not root.is_dir():
                continue
            for pattern in patterns:
                for path in sorted(root.glob(pattern)):
                    if path in seen or not path.is_file():
                        continue
                    seen.add(path)
                    paths.append(path)
                    if len(paths) >= PROCESS_SEGMENT_SCAN_LIMIT:
                        break
                if len(paths) >= PROCESS_SEGMENT_SCAN_LIMIT:
                    break
        except OSError:
            continue
    return paths


def _numeric_seconds(value: Any) -> float | None:
    """Parse a segment/keyframe timestamp without treating prose as time."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().rstrip("s")
    if not text:
        return None
    if ":" in text:
        try:
            minutes, seconds = text.split(":", 1)
            return float(minutes) * 60.0 + float(seconds)
        except (TypeError, ValueError):
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _item_process_frame_records(
    item_id: str,
    session: Mapping[str, Any],
    image_index: Iterable[Path],
) -> list[dict[str, Any]]:
    """Recover keyframes emitted inside this item's adapter analysis window.

    Adapter outputs contain a ``merged_segments`` entry for each item and a
    ``keyframe_map`` containing the frames sampled while that stage was being
    analysed.  The compact finding usually keeps only one representative
    frame; using the segment/map pair restores the actual local sequence while
    retaining exact item ownership.  No score or public conclusion is derived
    from these records.
    """
    cached = session.get("item_process_frame_records")
    if isinstance(cached, Mapping) and item_id in cached:
        value = cached.get(item_id)
        return [entry for entry in value if isinstance(entry, Mapping)] if isinstance(value, list) else []

    session_dir = Path(str(session.get("session_dir") or ""))
    by_item: dict[str, list[dict[str, Any]]] = {
        str(definition["item_id"]): [] for definition in ITEM_DEFINITIONS
    }
    # A physical frame can be mentioned by two overlapping adapter segments;
    # keep the highest-quality segment metadata for that frame.
    best_by_path: dict[tuple[str, Path], dict[str, Any]] = {}
    for adapter_path in _adapter_result_paths(session_dir):
        try:
            decoded = json.loads(adapter_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(decoded, Mapping):
            continue
        keyframe_map = decoded.get("keyframe_map")
        if not isinstance(keyframe_map, Mapping):
            continue
        # Normalize the map once.  A few exports use a one-field object rather
        # than a plain path string as the value.
        mapped_frames: list[tuple[float, str]] = []
        for raw_time, raw_path in keyframe_map.items():
            timestamp = _numeric_seconds(raw_time)
            if isinstance(raw_path, Mapping):
                raw_path = (
                    raw_path.get("frame_path")
                    or raw_path.get("keyframe_path")
                    or raw_path.get("path")
                )
            if timestamp is None or not isinstance(raw_path, str):
                continue
            mapped_frames.append((timestamp, raw_path))
        if not mapped_frames:
            continue
        for segment in decoded.get("merged_segments", []) or []:
            if not isinstance(segment, Mapping):
                continue
            tags = segment.get("tags")
            if isinstance(tags, str):
                tags = [tags]
            if not isinstance(tags, (list, tuple, set)):
                continue
            # Parse every exact item tag in one pass.  The session cache is
            # shared by the 13 item lookups; filtering on the first requested
            # item would otherwise cache an incomplete map and make later
            # cards fall back to a generic neighbouring keyframe.
            owned_items = {str(tag) for tag in tags} & set(by_item)
            if not owned_items:
                continue
            start = _numeric_seconds(segment.get("start"))
            end = _numeric_seconds(segment.get("end"))
            if start is None or end is None:
                continue
            if end < start:
                start, end = end, start
            avg_score = segment.get("avg_score")
            try:
                segment_quality = max(0.0, min(1.0, float(avg_score)))
            except (TypeError, ValueError):
                segment_quality = 0.0
            for owned_item in owned_items:
                for timestamp, raw_path in mapped_frames:
                    if timestamp < start - PROCESS_SEGMENT_TOLERANCE_SECONDS or timestamp > end + PROCESS_SEGMENT_TOLERANCE_SECONDS:
                        continue
                    path = _resolve_image_path(raw_path, session_dir, image_index)
                    if path is None:
                        continue
                    record = {
                        "path": path,
                        "timestamp_sec": timestamp,
                        "kind": _candidate_kind(path, "representative_frame"),
                        "segment_start": start,
                        "segment_end": end,
                        "segment_quality": segment_quality,
                        "analysis_task": adapter_path.stem.removesuffix("_adapter_result"),
                        "analysis_status": str(decoded.get("status") or "process_frames"),
                    }
                    key = (owned_item, path)
                    previous = best_by_path.get(key)
                    if previous is None or (
                        float(record["segment_quality"]),
                        -abs(timestamp - (start + end) / 2.0),
                    ) > (
                        float(previous.get("segment_quality") or 0.0),
                        -abs(float(previous.get("timestamp_sec") or 0.0) - (float(previous.get("segment_start") or 0.0) + float(previous.get("segment_end") or 0.0)) / 2.0),
                    ):
                        best_by_path[key] = record

    for (owned_item, _path), record in best_by_path.items():
        by_item.setdefault(owned_item, []).append(record)
    for records in by_item.values():
        # Select one coherent, highest-quality segment first.  This prevents a
        # broad stage map from joining two distant operations that happen to
        # carry the same item tag.
        groups: dict[tuple[str, float, float], list[dict[str, Any]]] = {}
        for record in records:
            group_key = (
                str(record.get("analysis_task") or ""),
                float(record.get("segment_start") or 0.0),
                float(record.get("segment_end") or 0.0),
            )
            groups.setdefault(group_key, []).append(record)
        if groups:
            ranked_groups = sorted(
                groups.values(),
                key=lambda group: (
                    -max(float(entry.get("segment_quality") or 0.0) for entry in group),
                    -len(group),
                    min(float(entry.get("timestamp_sec") or 0.0) for entry in group),
                ),
            )
            # One primary segment is enough to provide the requested process
            # keyframes; a second group is admitted only when the primary has
            # fewer than three frames (common in short object checks).
            chosen = ranked_groups[0][:PROCESS_SEGMENT_FRAME_LIMIT]
            if len(chosen) < 3 and len(ranked_groups) > 1:
                chosen.extend(ranked_groups[1][: PROCESS_SEGMENT_FRAME_LIMIT - len(chosen)])
            records[:] = sorted(
                chosen,
                key=lambda entry: (
                    float(entry.get("timestamp_sec") or 0.0),
                    str(entry.get("path") or ""),
                ),
            )
        else:
            records[:] = []
    try:
        session["item_process_frame_records"] = by_item  # type: ignore[index]
    except (TypeError, AttributeError):
        pass
    value = by_item.get(item_id, [])
    return value


def _task_record_quality(item_id: str, record: Mapping[str, Any]) -> float:
    text = " ".join(str(record.get(key) or "") for key in ("task_name", "task_type", "reason", "verified_checks", "visual_targets"))
    lower = text.casefold()
    hints = _ITEM_PROCESS_HINTS.get(item_id, ())
    hint_hits = sum(1 for hint in hints if str(hint).casefold() in lower)
    return (
        (100.0 if record.get("positive") else 0.0)
        + (20.0 if record.get("evidence_status") in {"supported", "confirmed", "pass", "passed"} else 0.0)
        + min(12.0, hint_hits * 2.0)
        + _task_confidence(record) * 8.0
        + min(8.0, float(record.get("frame_count") or 0) * 0.5)
        - (30.0 if record.get("explicit_negative") else 0.0)
    )


def _item_task_records(
    item_id: str,
    session: Mapping[str, Any],
    image_index: Iterable[Path],
) -> list[dict[str, Any]]:
    """Load bounded, item-owned analysis task records for one source video."""
    cached = session.get("item_task_records")
    if isinstance(cached, Mapping) and item_id in cached:
        value = cached.get(item_id)
        return [record for record in value if isinstance(record, Mapping)] if isinstance(value, list) else []

    session_dir = Path(str(session.get("session_dir") or ""))
    roots = (session_dir / "artifacts" / "evidence_enrichment", session_dir / "intermediate")
    json_paths: list[Path] = []
    seen_paths: set[Path] = set()
    patterns = (
        "**/task_result.json",
        "**/task_results.json",
        "**/adapter_planner_context.json",
        "**/enriched_adapter_result.json",
        "**/effective_adapter_result.json",
        "**/*_adapter_result.json",
    )
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for pattern in patterns:
                for path in sorted(root.glob(pattern)):
                    if path in seen_paths or not path.is_file():
                        continue
                    seen_paths.add(path)
                    json_paths.append(path)
                    if len(json_paths) >= TASK_RESULT_SCAN_LIMIT:
                        break
                if len(json_paths) >= TASK_RESULT_SCAN_LIMIT:
                    break
        except OSError:
            continue
        if len(json_paths) >= TASK_RESULT_SCAN_LIMIT:
            break

    by_item: dict[str, list[dict[str, Any]]] = {
        str(definition["item_id"]): [] for definition in ITEM_DEFINITIONS
    }
    seen_records: set[tuple[str, str, str, str]] = set()
    for json_path in json_paths:
        try:
            decoded = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        values: list[Mapping[str, Any]] = []
        if isinstance(decoded, Mapping):
            if isinstance(decoded.get("findings"), (list, tuple)):
                values.extend(entry for entry in decoded["findings"] if isinstance(entry, Mapping))
            # A task_results.json file is commonly a list under ``results``.
            for key in ("results", "task_results", "tasks"):
                if isinstance(decoded.get(key), (list, tuple)):
                    values.extend(entry for entry in decoded[key] if isinstance(entry, Mapping))
            # If there are no child records, the mapping itself is a task.
            if not values and any(key in decoded for key in ("claim_ids", "criterion_ids", "supporting_frames", "supporting_artifacts", "status")):
                values.append(decoded)
        elif isinstance(decoded, (list, tuple)):
            values.extend(entry for entry in decoded if isinstance(entry, Mapping))

        for value in values:
            owned, claims_exact = _owned_task_items(value, json_path)
            if not owned or not claims_exact:
                continue
            # Parse each task once, then cache records for every exact item
            # claim.  The previous implementation filtered on the requested
            # item while populating ``by_item``; the first lookup consequently
            # cached empty lists for all other items and made later item
            # selection fall back to unrelated legacy keyframes.
            owned = owned & set(by_item)
            if not owned:
                continue
            entries = _task_image_entries(value, json_path, session_dir, image_index)
            if not entries:
                continue
            status = _normalised_status(value.get("status"))
            evidence_status = _normalised_status(value.get("evidence_status"))
            text = " ".join(str(value.get(key) or "") for key in ("reason", "description", "issues", "failure_modes"))
            lower_text = text.casefold()
            explicit_negative = (
                status in _TASK_NEGATIVE_STATUSES
                or evidence_status in _TASK_NEGATIVE_STATUSES
                or any(marker.casefold() in lower_text for marker in _ITEM_PROCESS_EXCLUSIONS)
            )
            positive = _task_positive(value)
            visual_candidate = _task_visual_candidate(value)
            if not positive and not visual_candidate and not _evidence_is_positive(value):
                # A row with an explicit positive signal is still useful even
                # when it predates the normalized task-result status fields.
                signal = _normalised_status(value.get("signal") or value.get("finding_signal"))
                if signal not in {"positive", "pass", "passed", "confirmed", "success", "complete"}:
                    continue
                positive = True
            task_name = str(value.get("task_name") or value.get("task_type") or value.get("task_id") or json_path.parent.name)
            for owned_item in sorted(owned):
                identity = (
                    owned_item,
                    task_name,
                    str(json_path),
                    "|".join(str(entry["path"]) for entry in entries[:4]),
                )
                if identity in seen_records:
                    continue
                seen_records.add(identity)
                record: dict[str, Any] = {
                    "item_id": owned_item,
                    "task_name": task_name,
                    "task_path": json_path,
                    "status": status,
                    "evidence_status": evidence_status,
                    "positive": bool(positive),
                    "visual_candidate": bool(visual_candidate),
                    "explicit_negative": bool(explicit_negative),
                    "confidence": _task_confidence(value),
                    "time_range": deepcopy(value.get("time_range") or value.get("analysis_time_range") or {}),
                    "images": entries,
                    "frame_count": sum(1 for entry in entries if entry.get("kind") == "representative_frame"),
                    "claims_exact": claims_exact,
                }
                record["quality"] = _task_record_quality(owned_item, record)
                by_item[owned_item].append(record)

    for records in by_item.values():
        records.sort(
            key=lambda record: (
                -float(record.get("quality") or 0.0),
                -float(record.get("confidence") or 0.0),
                str(record.get("task_path") or ""),
            )
        )
    try:
        session["item_task_records"] = by_item  # type: ignore[index]
    except (TypeError, AttributeError):
        pass
    value = by_item.get(item_id, [])
    return value


def _item_analysis_artifacts(
    item_id: str,
    session: Mapping[str, Any],
    image_index: Iterable[Path],
) -> list[Path]:
    """Find detector/motion artifacts explicitly claimed by one item.

    Rich Engine runs store masks, crops and bbox overlays beside a
    ``task_result.json`` whose ``claim_ids`` identify the scoring item.  Those
    files are stronger evidence than an arbitrary neighbouring keyframe.  The
    flat 29-video export has no such files, in which case this helper simply
    returns an empty list and row-bound keyframes remain the source.
    """
    cached = session.get("item_analysis_artifacts")
    if isinstance(cached, Mapping) and item_id in cached:
        value = cached.get(item_id)
        return [path for path in value if isinstance(path, Path)] if isinstance(value, list) else []
    records = _item_task_records(item_id, session, image_index)
    paths: list[Path] = []
    seen: set[Path] = set()
    # Keep the strongest task first.  This compatibility helper is used by a
    # few callers that only need paths; the main candidate builder preserves
    # per-image metadata through ``_item_task_records``.
    for record in records:
        for entry in record.get("images", []) or []:
            path = entry.get("path") if isinstance(entry, Mapping) else None
            if not isinstance(path, Path) or path in seen:
                continue
            seen.add(path)
            paths.append(path)
            if len(paths) >= TASK_RESULT_IMAGE_LIMIT:
                break
        if len(paths) >= TASK_RESULT_IMAGE_LIMIT:
            break
    try:
        session["item_analysis_artifacts"] = {item_id: paths}  # type: ignore[index]
    except (TypeError, AttributeError):
        pass
    return paths


def _item_anchor_seconds(
    session: Mapping[str, Any],
    *,
    exclude_item: str | None = None,
) -> list[tuple[str, float]]:
    """Return timestamp anchors for all scored items in one source video."""
    anchors: list[tuple[str, float]] = []
    rows_by_item = session.get("rows_by_item") or {}
    if not isinstance(rows_by_item, Mapping):
        return anchors
    for item_id, rows in rows_by_item.items():
        item_key = str(item_id)
        if exclude_item is not None and item_key == exclude_item:
            continue
        if not isinstance(rows, (list, tuple)):
            continue
        for row in rows:
            if not isinstance(row, tuple) or len(row) != 3 or not isinstance(row[1], Mapping):
                continue
            seconds = _timestamp_seconds(row[1])
            if seconds is not None:
                anchors.append((item_key, seconds))
    return anchors


def _belongs_to_item_window(
    seconds: float | None,
    item_id: str,
    anchor_seconds: float | None,
    other_anchors: Iterable[tuple[str, float]],
    *,
    allow_tie: bool = False,
) -> bool:
    """Reject a nearby frame when another item's anchor is closer.

    Flat exports place all stage frames in one directory.  Temporal proximity
    alone is therefore not enough: if an adjacent item has a closer anchor,
    its frame belongs to that item even when it falls inside the same ±window.
    Explicit row artifacts may opt into ``allow_tie`` when two items share an
    intentionally identical observation timestamp.
    """
    if seconds is None or anchor_seconds is None:
        return True
    target_distance = abs(seconds - anchor_seconds)
    for _other_item, other_seconds in other_anchors:
        other_distance = abs(seconds - other_seconds)
        if other_distance + 1e-6 < target_distance:
            return False
        if not allow_tie and abs(other_distance - target_distance) <= 1e-6:
            return False
    return True


def _visualization_candidates(
    session_dir: Path,
    image_index: Iterable[Path],
    anchor_seconds: float | None,
    *,
    item_id: str,
    other_anchors: Iterable[tuple[str, float]],
    limit: int = 2,
) -> list[Path]:
    """Pick timestamp-matched bbox/mask overlays from the same session."""
    if anchor_seconds is None:
        return []
    paths: list[tuple[float, str, Path]] = []
    for path in image_index:
        text = "/".join(part.casefold() for part in path.parts)
        if "visualization" not in text and not any(
            token in text for token in ("bbox", "mask", "overlay", "segmentation")
        ):
            continue
        seconds = _artifact_seconds(path)
        if seconds is None or abs(seconds - anchor_seconds) > VISUALIZATION_MATCH_TOLERANCE_SECONDS:
            continue
        if not _belongs_to_item_window(
            seconds,
            item_id,
            anchor_seconds,
            other_anchors,
            allow_tie=True,
        ):
            continue
        paths.append((abs(seconds - anchor_seconds), str(path), path))
    paths.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in paths[: max(0, limit)]]


def _sibling_process_images(
    path: Path,
    *,
    limit: int = 10,
    anchor_seconds: float | None = None,
    neighbourhood_seconds: float = PROCESS_FRAME_NEIGHBOURHOOD_SECONDS,
    prefer_after: bool = True,
) -> list[Path]:
    """Collect nearby frames from the same analyzer directory as ``path``.

    Flat artifact exports keep all item keyframes in one ``a1``/``a2``
    directory.  Restricting siblings to the temporal neighbourhood of the
    selected row prevents a neighbouring item's frame from being mistaken for
    this item's process evidence.
    """
    try:
        siblings = [candidate for candidate in path.parent.iterdir() if _path_is_image(candidate)]
    except OSError:
        return [path] if _path_is_image(path) else []
    if not siblings:
        return [path] if _path_is_image(path) else []
    siblings.sort(key=lambda candidate: (_artifact_seconds(candidate) is None, _artifact_seconds(candidate) or 0.0, candidate.name))
    target_seconds = anchor_seconds if anchor_seconds is not None else _artifact_seconds(path)
    if target_seconds is None:
        # Without a timestamp there is no defensible way to tell an adjacent
        # item apart.  The exact row artifact is still valid, but unrelated
        # undated siblings are not process evidence for this item.
        ordered = [path] if path in siblings else []
    else:
        def distance_key(candidate: Path) -> tuple[int, float, str]:
            seconds = _artifact_seconds(candidate)
            if seconds is None:
                return (2, float("inf"), candidate.name)
            # Once the exact row frame is bound, frames just after it are a
            # safer continuation than setup frames several seconds before it.
            # Earlier frames are retained only when the continuation is too
            # short to fill a sequence slot.
            direction = 0 if prefer_after and seconds >= target_seconds else 1
            return (direction, abs(seconds - target_seconds), candidate.name)

        ordered = sorted(siblings, key=distance_key)
        # Keep a compact local action window where timestamps are available.
        # If no neighbour falls in it, the anchor itself is still retained.
        local = [
            candidate
            for candidate in ordered
            if (
                candidate == path
                or (
                    _artifact_seconds(candidate) is not None
                    and abs(_artifact_seconds(candidate) - target_seconds)
                    <= max(0.0, neighbourhood_seconds)
                )
            )
        ]
        if local:
            ordered = local
        else:
            ordered = [path] if path in siblings else []
        if prefer_after and target_seconds is not None:
            # Keep the exact anchor first, then the nearest future frames.  If
            # there are fewer than two future frames, add the nearest earlier
            # frame as a final fallback.  This avoids presenting a table/setup
            # frame before an otherwise clear action anchor.
            exact = [candidate for candidate in ordered if _artifact_seconds(candidate) == target_seconds]
            future = [
                candidate
                for candidate in ordered
                if (_artifact_seconds(candidate) is not None and _artifact_seconds(candidate) > target_seconds)
            ]
            past = [
                candidate
                for candidate in ordered
                if (_artifact_seconds(candidate) is not None and _artifact_seconds(candidate) < target_seconds)
            ]
            reordered: list[Path] = []
            for candidate in exact + future + past:
                if candidate not in reordered:
                    reordered.append(candidate)
            ordered = reordered
    # Return chronological order so phases in the drawer follow the source
    # analysis rather than the filesystem's insertion order.
    chosen = ordered[: max(1, limit)]
    chosen.sort(key=lambda candidate: (_artifact_seconds(candidate) is None, _artifact_seconds(candidate) or 0.0, candidate.name))
    return chosen


def _candidate_kind(path: Path, default: str = "artifact_frame") -> str:
    text = "/".join(part.casefold() for part in path.parts)
    if any(token in text for token in ("mask", "bbox", "box", "overlay", "segmentation", "crop")):
        return "object_detection"
    if "frame_strip" in text or "sequence" in text:
        return "multi_frame_sequence"
    return default


def _round_label(item_id: str) -> str | None:
    if item_id == "item_5069":
        return "第二次预松"
    if item_id == "install_1st":
        return "第一次预紧"
    return None


def _session_image_candidates(
    item_id: str,
    session: Mapping[str, Any],
    *,
    limit: int = 36,
) -> list[dict[str, Any]]:
    """Build process-frame candidates for one item in one source video.

    Every returned path comes from an evidence row for ``item_id`` or from
    that row's analyzer directory.  This is the key boundary that the old
    implementation missed: it no longer merges the highest-confidence rows
    from several different videos.
    """
    session_dir = Path(str(session.get("session_dir") or ""))
    sample_id = str(session.get("sample_id") or session_dir.name)
    rows_by_item = session.get("rows_by_item") or {}
    rows = [row for row in rows_by_item.get(item_id, []) if isinstance(row, tuple) and len(row) == 3]
    # Positive row labels improve frame ordering, but the parent item's final
    # score remains the selection authority.  A number of legacy reports leave
    # the row status unset, so retain every item row when no positive label is
    # available.
    positive_rows = [row for row in rows if _row_is_positive(row)]
    # Rank by the item's own process language first, then retain at most a
    # short continuation around that anchor.  Chronological sorting of every
    # row was the source of the old mixed-operation thumbnails: the earliest
    # positive row often belonged to setup while the actual action appeared
    # later in the same analyzer stream.
    ordered_rows = _ordered_process_rows(item_id, rows)
    if not ordered_rows:
        ordered_rows = sorted(positive_rows or rows, key=lambda row: _timestamp_sort_key(row[1]))
    image_index = _session_index_for(session)
    other_anchors = _item_anchor_seconds(session, exclude_item=item_id)
    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    round_label = _round_label(item_id)

    def path_evidence(evidence: Mapping[str, Any], path: Path) -> Mapping[str, Any]:
        """Attach the timestamp encoded by a process artifact when present."""
        seconds = _artifact_seconds(path)
        row_seconds = _timestamp_seconds(evidence)
        if seconds is None or (row_seconds is not None and abs(seconds - row_seconds) <= 0.25):
            return evidence
        updated = dict(evidence)
        updated["timestamp_sec"] = seconds
        updated["timestamp"] = _format_timestamp(seconds)
        return updated

    def add(path: Path, evidence: Mapping[str, Any], *, default_kind: str) -> None:
        if len(candidates) >= limit or not _path_is_image(path):
            return
        try:
            path = path.resolve()
        except OSError:
            pass
        path_text = str(path)
        if path_text in seen_paths:
            return
        seen_paths.add(path_text)
        kind = _candidate_kind(path, default_kind)
        candidates.append(
            _candidate_record(
                item_id,
                sample_id,
                path_evidence(evidence, path),
                kind=kind,
                phase=None,
                source_path=path,
                evidence_id_suffix=path_text,
                order_index=len(candidates) + 1,
                round_label=round_label,
            )
        )

    # Rich 10-video runs expose the frames sampled by the actual item task in
    # ``task_result.json``.  Keep one strongest, item-owned task together as a
    # coherent source: its full process frames come first, followed by any
    # crop/mask/bbox artifacts.  This prevents a generic keyframe from a
    # neighbouring operation from replacing the analysis output that actually
    # evaluated this item.
    task_records = _item_task_records(item_id, session, image_index)
    task_record: Mapping[str, Any] | None = None
    if task_records:
        usable = [
            record
            for record in task_records
            if isinstance(record, Mapping)
            and isinstance(record.get("images"), list)
            and record.get("images")
            and not record.get("explicit_negative")
        ]
        # Records are already ranked by task quality.  A supported/positive
        # result is preferred; an image-bearing visual result is a safe
        # fallback when the richer score fields are absent.
        task_record = next((record for record in usable if record.get("positive")), None)
        if task_record is None:
            task_record = next((record for record in usable if record.get("visual_candidate")), None)
        if task_record is None:
            task_record = usable[0] if usable else None
    task_candidate_count = 0
    task_paths: set[str] = set()
    if task_record is not None:
        task_images = [entry for entry in task_record.get("images", []) if isinstance(entry, Mapping) and isinstance(entry.get("path"), Path)]
        # Full frames preserve the sampled sequence; detection artifacts are
        # still useful for object slots but should not become the first three
        # sequence thumbnails when both kinds are available.
        task_images.sort(
            key=lambda entry: (
                1 if str(entry.get("kind") or "").casefold() == "object_detection" else 0,
                1 if entry.get("timestamp_sec") is None else 0,
                float(entry.get("timestamp_sec") or _artifact_seconds(entry["path"]) or 0.0),
                str(entry.get("path")),
            )
        )
        task_status = str(task_record.get("status") or task_record.get("evidence_status") or "")
        task_confidence = task_record.get("confidence")
        task_name = str(task_record.get("task_name") or "item-analysis")
        for entry in task_images:
            path = entry["path"]
            seconds = entry.get("timestamp_sec")
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
                seconds = _artifact_seconds(path)
            task_evidence: dict[str, Any] = {
                "timestamp_sec": seconds,
                "timestamp": _format_timestamp(seconds),
                "confidence": task_confidence if isinstance(task_confidence, (int, float)) else 0.96,
                "signal": "positive" if task_record.get("positive") else "unclear",
                "source_type": "item_analysis_task",
            }
            before = len(candidates)
            add(path, task_evidence, default_kind=str(entry.get("kind") or "artifact_frame"))
            if len(candidates) > before:
                task_candidate_count += 1
                task_paths.add(str(path.resolve()))
                for candidate in candidates[before:]:
                    # Private fields are retained only until render_report's
                    # public projection and make the generation audit explicit.
                    candidate["analysis_task"] = task_name
                    candidate["analysis_status"] = task_status

    # A positive task with at least three process images is self-contained.
    # Do not append legacy rows or sibling frames in that case; doing so would
    # make the drawer visually jump between separate analysis operations.
    task_sequence_complete = bool(task_record is not None and task_candidate_count >= 3)

    # Flat exports (and rich runs whose task result was not materialised) keep
    # the actual sampler output in ``keyframe_map``.  A matching
    # ``merged_segments`` range is stronger than a neighbouring keyframe: it
    # proves that the image was part of this item's analysis window.  Restore
    # that compact sequence before consulting legacy summary rows.
    process_candidate_count = 0
    process_sequence_selected = False
    if not task_sequence_complete:
        process_records = _item_process_frame_records(item_id, session, image_index)
        for record in process_records:
            path = record.get("path") if isinstance(record, Mapping) else None
            if not isinstance(path, Path):
                continue
            segment_quality = record.get("segment_quality")
            if isinstance(segment_quality, (int, float)) and not isinstance(segment_quality, bool) and float(segment_quality) > 0:
                confidence = max(0.72, min(0.98, float(segment_quality)))
            else:
                # Older adapter exports omit ``avg_score`` while marking the
                # process result successful.  In that case the presence of a
                # tagged segment is stronger than the generic low-confidence
                # default; keep the visual evidence consistent with the
                # all-correct mock conclusion without inventing a model score.
                status = str(record.get("analysis_status") or "").casefold()
                confidence = 0.96 if status in {"success", "succeeded", "complete", "completed", "pass", "passed"} else 0.72
            process_evidence: dict[str, Any] = {
                "timestamp_sec": record.get("timestamp_sec"),
                "timestamp": _format_timestamp(record.get("timestamp_sec")),
                "confidence": confidence,
                "signal": "positive" if confidence >= 0.78 else "unclear",
                "source_type": "item_analysis_process",
            }
            before = len(candidates)
            add(path, process_evidence, default_kind=str(record.get("kind") or "representative_frame"))
            if len(candidates) > before:
                process_candidate_count += 1
                for candidate in candidates[before:]:
                    candidate["analysis_task"] = str(record.get("analysis_task") or "item-analysis-process")
                    candidate["analysis_status"] = str(record.get("analysis_status") or "process_frames")
        process_sequence_selected = process_candidate_count > 0

    # First bind exact row artifacts and timestamp-matched detector overlays.
    # These are the outputs of this item's analysis task, rather than a frame
    # borrowed from another item in the shared stage directory.
    direct_sources: list[tuple[Path, Mapping[str, Any], Path, str, float | None]] = []
    for _sample_id, evidence, row_session_dir in ([] if task_sequence_complete or process_sequence_selected else ordered_rows):
        current_dir = row_session_dir if isinstance(row_session_dir, Path) else session_dir
        anchor = _timestamp_seconds(evidence)
        keyframe_text = str(evidence.get("keyframe_path") or evidence.get("keyframe") or "")
        keyframe_path = _resolve_image_path(keyframe_text, current_dir, image_index) if keyframe_text else None
        visual_paths = _visualization_candidates(
            current_dir,
            image_index,
            anchor,
            item_id=item_id,
            other_anchors=other_anchors,
            limit=2,
        )
        direct_paths = _supporting_image_paths(evidence, current_dir, image_index)
        # Keep the row's representative frame first.  Mask/bbox/overlay
        # outputs follow it and are then available to the object-detection
        # evidence slot.
        direct_paths.sort(
            key=lambda path: (
                0 if keyframe_path is not None and path == keyframe_path else 1,
                0 if _candidate_kind(path) == "object_detection" else 1,
                str(path),
            )
        )
        ordered_paths: list[tuple[Path, str]] = []
        for path in ([keyframe_path] if keyframe_path is not None else []) + visual_paths + direct_paths:
            if path is None or any(path == existing for existing, _kind in ordered_paths):
                continue
            default_kind = "representative_frame" if keyframe_path is not None and path == keyframe_path else "object_detection" if _candidate_kind(path) == "object_detection" else "artifact_frame"
            ordered_paths.append((path, default_kind))
        for path, default_kind in ordered_paths:
            add(path, evidence, default_kind=default_kind)
            direct_sources.append((path, evidence, current_dir, default_kind, anchor))
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    # Rich runs may have a task result with additional masks/crops that were
    # not copied into the compact summary row.  Claim-bound artifacts are safe
    # to use because the task result names this item explicitly.
    if len(candidates) < limit and not task_sequence_complete and not process_sequence_selected:
        artifact_paths = _item_analysis_artifacts(item_id, session, image_index)
        fallback_evidence = ordered_rows[0][1] if ordered_rows else {}
        for path in artifact_paths:
            seconds = _artifact_seconds(path)
            if seconds is not None and not _belongs_to_item_window(
                seconds,
                item_id,
                _timestamp_seconds(fallback_evidence),
                other_anchors,
                allow_tie=True,
            ):
                continue
            add(path, fallback_evidence, default_kind="artifact_frame")
            if len(candidates) >= limit:
                break

    # Complete an action sequence only with nearby frames from the same
    # analyzer directory.  A frame is admitted when this item's anchor is at
    # least as close as every other item's anchor; ties are kept for explicit
    # row/overlay artifacts but not for generic neighbours.
    for path, evidence, _current_dir, default_kind, anchor in direct_sources:
        if len(candidates) >= limit:
            break
        # Do not infer an item's process sequence from neighbouring files in
        # a shared ``keyframes/a1`` or ``keyframes/a2`` directory.  Those
        # directories contain frames for several scoring items at adjacent
        # timestamps.  Siblings are admitted only when the artifact itself is
        # explicitly named as a frame strip/sequence output; ordinary legacy
        # rows remain bound to their exact item-analysis frame.
        path_text = str(path).casefold()
        if "frame_strip" not in path_text and "sequence" not in path_text:
            continue
        for sibling in _sibling_process_images(
            path,
            limit=6,
            anchor_seconds=anchor,
            neighbourhood_seconds=PROCESS_FRAME_NEIGHBOURHOOD_SECONDS,
            prefer_after=True,
        ):
            if sibling == path:
                continue
            sibling_seconds = _artifact_seconds(sibling)
            if not _belongs_to_item_window(
                sibling_seconds,
                item_id,
                anchor,
                other_anchors,
                # The selected row is already the item's highest-ranked
                # finding.  Equal-time frames in its analyzer directory are
                # retained as the adjacent action sequence; strict tie
                # rejection otherwise leaves only one thumbnail for many
                # legacy exports where every item shares a timestamp anchor.
                allow_tie=True,
            ):
                continue
            sibling_evidence: Mapping[str, Any] = evidence
            if sibling_seconds is not None:
                sibling_evidence = dict(evidence)
                sibling_evidence["timestamp_sec"] = sibling_seconds
                sibling_evidence["timestamp"] = _format_timestamp(sibling_seconds)
            add(sibling, sibling_evidence, default_kind=default_kind)
            if len(candidates) >= limit:
                break

    # A few legacy reports only record a score and omit the item evidence row.
    # If that happens, use the closest frame in the item's analysis stage; do
    # not use the first image in an unrelated stage merely because it exists.
    if not candidates:
        stage_names = ["a2"] if item_id in {"cylinder_head_bolt", "install_1st"} else ["a1"]
        stage_names.extend(["prep", "comp"])
        stage_dirs: list[Path] = []
        for stage in stage_names:
            direct = session_dir / "keyframes" / stage
            if direct.is_dir():
                stage_dirs.append(direct)
        anchor = _timestamp_seconds(ordered_rows[0][1]) if ordered_rows else None
        fallback_paths = [
            path
            for path in image_index
            if any(path.parent == stage_dir or stage_dir in path.parents for stage_dir in stage_dirs)
            and _belongs_to_item_window(
                _artifact_seconds(path), item_id, anchor, other_anchors, allow_tie=False
            )
        ]
        fallback_paths.sort(
            key=lambda path: (
                abs((_artifact_seconds(path) or anchor or 0.0) - (anchor or _artifact_seconds(path) or 0.0)),
                str(path),
            )
        )
        for path in fallback_paths[:limit]:
            add(path, ordered_rows[0][1] if ordered_rows else {}, default_kind="representative_frame")

    # Chronological order is used for start/action/completion labels.  Preserve
    # deterministic lexical order when an artifact has no embedded timestamp.
    candidates.sort(
        key=lambda candidate: (
            1 if candidate.get("timestamp_sec") is None else 0,
            float(candidate.get("timestamp_sec") or 0.0),
            str(candidate.get("source_path") or ""),
        )
    )
    for index, candidate in enumerate(candidates):
        candidate["phase"] = _phase_for_index(index)
        candidate["caption"] = _caption(str(candidate.get("kind") or "artifact_frame"), candidate["phase"])
        candidate["order_index"] = index + 1
    return candidates


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
    if seconds is None and path:
        seconds = _artifact_seconds(path)
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
    # A source row is used for ordering only after its parent item's final
    # score has been confirmed.  Do not reject a valid row merely because the
    # historical analyzer attached a conservative confidence value.
    return _evidence_is_positive(row[1])


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


def _row_process_quality(item_id: str, row: tuple[str, dict[str, Any], Path]) -> float:
    """Rank one legacy finding by how directly it describes this item.

    Flat exports often contain a single representative frame for every
    analyzer query, while the final score is stored separately.  Selecting by
    timestamp alone therefore puts setup or neighbouring operations in the
    drawer.  This private rank combines the finding signal with item-specific
    observable terms and strongly discounts explicit absence statements.
    """
    evidence = row[1]
    signal = _normalised_status(evidence.get("signal") or evidence.get("finding_signal"))
    status = _normalised_status(evidence.get("status") or evidence.get("judgment"))
    source_type = _normalised_status(evidence.get("source_type"))
    text = " ".join(
        str(evidence.get(key) or "")
        for key in ("description", "observation", "observations", "reason", "issues", "finding")
    ).casefold()
    hints = _ITEM_PROCESS_HINTS.get(item_id, ())
    hint_hits = sum(1 for hint in hints if str(hint).casefold() in text)
    negative_hits = sum(1 for marker in _ITEM_PROCESS_EXCLUSIONS if marker.casefold() in text)
    signal_score = {
        "positive": 30.0,
        "pass": 30.0,
        "passed": 30.0,
        "confirmed": 30.0,
        "success": 30.0,
        "complete": 30.0,
        "unclear": 0.0,
        "": 0.0,
        "negative": -28.0,
        "unrelated": -28.0,
        "not_found": -24.0,
    }.get(signal, 0.0)
    status_score = 8.0 if status in {"positive", "pass", "passed", "confirmed", "success", "complete"} else -8.0 if status in _TASK_NEGATIVE_STATUSES else 0.0
    confidence = evidence.get("confidence")
    confidence_score = float(confidence) * 5.0 if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else 0.0
    direct_score = 3.0 if _row_is_direct_positive(row) else 0.0
    memory_score = 1.5 if source_type == "memory" else 0.0
    return signal_score + status_score + min(30.0, hint_hits * 5.0) - min(36.0, negative_hits * 12.0) + confidence_score + direct_score + memory_score


def _ordered_process_rows(
    item_id: str,
    rows: list[tuple[str, dict[str, Any], Path]],
) -> list[tuple[str, dict[str, Any], Path]]:
    """Choose one coherent legacy finding, retaining a close continuation."""
    if not rows:
        return []
    ranked = sorted(
        rows,
        key=lambda row: (
            -_row_process_quality(item_id, row),
            _timestamp_sort_key(row[1]),
            str(row[1].get("keyframe_path") or row[1].get("keyframe") or ""),
        ),
    )
    anchor = ranked[0]
    anchor_seconds = _timestamp_seconds(anchor[1])
    chosen = [anchor]
    # A duplicate adapter row at the same timestamp can carry an overlay or a
    # second view.  Keep it, but never merge a distant finding from another
    # operation into this item's process chain.
    for row in ranked[1:]:
        seconds = _timestamp_seconds(row[1])
        if seconds is None or anchor_seconds is None:
            continue
        if abs(seconds - anchor_seconds) <= PROCESS_FRAME_NEIGHBOURHOOD_SECONDS and _row_process_quality(item_id, row) >= _row_process_quality(item_id, anchor) - 18.0:
            chosen.append(row)
        if len(chosen) >= 3:
            break
    return chosen


def _session_item_correctness_tier(item_id: str, session: Mapping[str, Any]) -> tuple[int, str]:
    """Return the strongest item-level correctness signal for one source.

    Rich runs may have a successful item task even when their compact summary
    was conservative (or did not include the item score).  Flat exports carry
    the human item labels in the optional manifest instead.  Both are valid
    ways to identify a correct source video; the final report score remains a
    fallback for legacy summaries that have neither signal.
    """
    try:
        image_index = _session_index_for(session)
    except (OSError, TypeError, ValueError):
        image_index = []
    records = _item_task_records(item_id, session, image_index)
    if any(
        record.get("positive")
        and not record.get("explicit_negative")
        and str(record.get("status") or record.get("evidence_status") or "").casefold()
        not in {"insufficient", "unsupported", "failed", "fail", "negative"}
        for record in records
        if isinstance(record, Mapping)
    ):
        return 3, "item_analysis_task"
    if item_id in (session.get("manifest_correct_items") or set()):
        return 2, "manifest_item_label"
    if _outcome_is_correct((session.get("outcomes") or {}).get(item_id)):
        return 1, "report_full_score"
    # A direct positive finding is useful for old reports whose item outcome
    # was omitted, but is intentionally lower priority than an explicit score.
    rows = (session.get("rows_by_item") or {}).get(item_id, [])
    if any(_row_is_direct_positive(row) for row in rows if isinstance(row, tuple) and len(row) == 3):
        return 0, "direct_item_finding"
    return -1, "none"


def _session_item_visual_quality(item_id: str, session: Mapping[str, Any]) -> float:
    """Score the usefulness of an item's private process artifacts.

    Correctness is a gate, not a reason to display a random setup frame.  The
    ranking therefore favors an item task with a supported result, then a
    finding whose text and signal describe this item's observable action.  It
    is only used to form a small random pool; it never changes the report
    score or any public evaluation.
    """
    try:
        image_index = _session_index_for(session)
    except (OSError, TypeError, ValueError):
        image_index = []
    records = _item_task_records(item_id, session, image_index)
    task_scores: list[float] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        base = float(record.get("quality") or 0.0)
        if record.get("positive") and not record.get("explicit_negative"):
            base += 120.0
        elif record.get("visual_candidate") and not record.get("explicit_negative"):
            base += 55.0
        task_scores.append(base)
    # Adapter segment/map records are the strongest indication that the
    # selected image belongs to the item's actual analysis window.  They are
    # kept separate from correctness: a low segment score must not turn a
    # human-correct video into an incorrect one, but a coherent multi-frame
    # sequence should win the visual-quality audit when several correct
    # sources are available.
    process_records = _item_process_frame_records(item_id, session, image_index)
    process_scores: list[float] = []
    for record in process_records:
        segment_quality = record.get("segment_quality")
        quality = (
            max(0.0, min(1.0, float(segment_quality)))
            if isinstance(segment_quality, (int, float)) and not isinstance(segment_quality, bool)
            else 0.0
        )
        process_scores.append(85.0 + quality * 100.0)
    if process_scores:
        # Count is capped so a long sampler output cannot overwhelm the
        # provenance distinction; this value is only an audit/ranking hint.
        process_scores.append(min(24.0, len(process_records) * 2.0) + max(process_scores))

    rows = [
        row
        for row in (session.get("rows_by_item") or {}).get(item_id, [])
        if isinstance(row, tuple) and len(row) == 3
    ]
    row_scores = [_row_process_quality(item_id, row) for row in rows]
    correctness_bonus = 0.0
    if item_id in (session.get("manifest_correct_items") or set()):
        correctness_bonus += 4.0
    if _outcome_is_correct((session.get("outcomes") or {}).get(item_id)):
        correctness_bonus += 2.0
    artifact_scores = task_scores + process_scores
    return max(artifact_scores or [-100.0]) + correctness_bonus if artifact_scores else max(row_scores or [-100.0]) + correctness_bonus


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
    session: Mapping[str, Any],
    path_owners: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one all-correct replay event from exactly one source session."""
    item_id = str(item["item_id"])
    sample_id = str(session.get("sample_id") or "")
    rows = [row for row in (session.get("rows_by_item") or {}).get(item_id, []) if isinstance(row, tuple)]
    image_candidates = _session_image_candidates(item_id, session)
    if not image_candidates:
        raise ValueError(f"{item_id}: 正确样本没有可用的分析过程图像")

    sequence_rows = sorted(rows, key=lambda row: _timestamp_sort_key(row[1]))
    timestamp_candidates: list[dict[str, Any]] = []
    for _row_sample, evidence, _session_dir in sequence_rows:
        seconds = _timestamp_seconds(evidence)
        if seconds is None:
            continue
        timestamp_candidates.append(
            _candidate_record(
                item_id,
                sample_id,
                evidence,
                kind="timestamp",
                source_path=Path(f"timestamp://{item_id}/{_digest(sample_id + str(seconds))}"),
                evidence_id_suffix=f"timestamp-{sample_id}-{seconds}",
            )
        )
    if not timestamp_candidates:
        first_seconds = image_candidates[0].get("timestamp_sec")
        if isinstance(first_seconds, (int, float)) and not isinstance(first_seconds, bool):
            timestamp_candidates.append(
                _candidate_record(
                    item_id,
                    sample_id,
                    {"timestamp_sec": first_seconds, "confidence": 0.96},
                    kind="timestamp",
                    source_path=Path(f"timestamp://{item_id}/{_digest(sample_id + str(first_seconds))}"),
                    evidence_id_suffix=f"timestamp-{sample_id}-{first_seconds}",
                )
            )

    # Required slots are always filled before optional slots.  This avoids a
    # crowded optional evidence list consuming the only process frame needed
    # to start the replay.
    slot_map: dict[str, list[dict[str, Any]]] = {}
    binding_evidence: list[dict[str, Any]] = []
    for slot in _all_slots(item):
        slot_id = str(slot.get("slot_id") or "")
        chosen = _slot_candidates(
            slot_id,
            image_candidates,
            timestamp_candidates,
            path_owners,
            item_id,
        )
        slot_map[slot_id] = chosen
        slot["status"] = "bound" if chosen else "empty"
        slot["evidence"] = chosen
        for evidence in chosen:
            if evidence not in binding_evidence:
                binding_evidence.append(evidence)

    definition = next(d for d in ITEM_DEFINITIONS if d["item_id"] == item_id)
    missing_required = [
        slot_id
        for slot_id in definition["required_slots"]
        if not slot_map.get(slot_id)
    ]
    if missing_required:
        raise ValueError(f"{item_id}: 正确样本缺少必需过程证据槽位：{','.join(missing_required)}")

    binding_evidence.sort(
        key=lambda evidence: (
            1 if evidence.get("kind") == "timestamp" else 0,
            evidence.get("order_index") is None,
            int(evidence.get("order_index") or 0),
            float(evidence.get("timestamp_sec") or 0.0),
            str(evidence.get("evidence_id") or ""),
        )
    )

    confidence_values = [
        float(row[1].get("confidence") or 0.55)
        for row in sequence_rows
        if isinstance(row[1].get("confidence"), (int, float))
        and not isinstance(row[1].get("confidence"), bool)
    ]
    source_confidence = statistics.mean(confidence_values) if confidence_values else 0.96
    # The mock represents the agreed correct onsite operation.  Source
    # confidence is retained as audit context, but cannot downgrade this
    # all-correct replay result.
    confidence = max(0.96, min(1.0, source_confidence))
    state = "已完成评分"
    sequence_seconds = [
        seconds
        for _sample_id, evidence, _session_dir in sequence_rows
        for seconds in [_timestamp_seconds(evidence)]
        if seconds is not None
    ]
    if not sequence_seconds:
        sequence_seconds = [
            float(candidate["timestamp_sec"])
            for candidate in image_candidates
            if isinstance(candidate.get("timestamp_sec"), (int, float))
        ]
    first_seconds = min(sequence_seconds) if sequence_seconds else None
    last_seconds = max(sequence_seconds) if sequence_seconds else first_seconds
    updated_at = _format_timestamp(first_seconds)
    end_seconds = (
        last_seconds
        if last_seconds is not None and first_seconds is not None and last_seconds > first_seconds
        else first_seconds + 3.0 if first_seconds is not None else None
    )
    prefilled = prefilled_result_for(
        item_id,
        slot_map,
        updated_at=updated_at,
        confidence=confidence,
    )

    completed_item = deepcopy(item)
    completed_item["live_binding"] = {
        "state": state,
        "revision": 1,
        "changed_slot_ids": [
            str(slot.get("slot_id"))
            for slot in _all_slots(completed_item)
            if slot.get("status") == "bound"
        ],
        "live_timestamp": _format_timestamp(first_seconds),
        "live_start_sec": first_seconds,
        "live_end_sec": end_seconds,
        "time_source": "mock_live_stream",
        "time_confidence": round(confidence, 3),
        "evidence_explanation": "对象、动作和时序画面已整理。",
        "evidence": binding_evidence,
    }
    completed_item["detail_evaluation"] = {
        "state": "unlocked",
        "updated_at": updated_at,
        "checks": prefilled["detail_evaluation"]["checks"],
        "unresolved_summary": "",
        "high_level_evaluation": prefilled["high_level_evaluation"],
    }
    completed_item["prefilled_result"] = prefilled
    completed_item["score"] = 1
    # Private generation metadata is ignored by the public projection.  It
    # makes it possible to audit that all frames in an event came from one
    # correct source video without placing the sample identity in the UI.
    completed_item["_mock_source"] = {
        "sample_id": sample_id,
        "summary_path": str(session.get("summary_path") or ""),
        "selection": "item_score_pass",
    }

    item["live_binding"] = _empty_binding()
    item["detail_evaluation"] = {
        "state": "locked",
        "updated_at": None,
        "checks": [],
        "unresolved_summary": "",
    }
    item["score"] = None
    _empty_slots(item)

    event = {
        "event_id": f"evt-{item_id}",
        "item_id": item_id,
        "delay_ms": 1150 + (900 if item.get("difficulty") == "difficult" else 250),
        "processing_ms": MOCK_ANALYSIS_DURATION_MS,
        "final_state": state,
        "evidence_ids": [str(evidence["evidence_id"]) for evidence in binding_evidence],
        "item_patch": completed_item,
        "_mock_source": {
            "sample_id": sample_id,
            "summary_path": str(session.get("summary_path") or ""),
            "correct_outcome": deepcopy(session.get("outcomes", {}).get(item_id, {})),
        },
    }
    return item, event


def _load_sessions(summary_paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Load a compact item/evidence index for each source video."""
    sessions: list[dict[str, Any]] = []
    active_ids = {str(definition["item_id"]) for definition in ITEM_DEFINITIONS}
    for summary_path in summary_paths:
        summary = _read_json(summary_path)
        session_dir = summary_path.parent.parent
        sample_id = session_dir.name
        rows_by_item: dict[str, list[tuple[str, dict[str, Any], Path]]] = {
            item_id: [] for item_id in active_ids
        }
        # Adapter findings are the process-level frames supplied to the visual
        # analyzers.  Put them first; the candidate sorter still falls back to
        # the compact summary row when an adapter artifact is absent.
        evidence_records = list(_iter_intermediate_evidence(session_dir))
        evidence_records.extend(_iter_summary_evidence(summary))
        seen_rows: set[tuple[str, str, str, str, str]] = set()
        for evidence in evidence_records:
            item_id = str(evidence.get("item") or "")
            if item_id in rows_by_item:
                signature = (
                    item_id,
                    str(evidence.get("timestamp_sec") or ""),
                    str(evidence.get("timestamp") or ""),
                    str(evidence.get("keyframe_path") or evidence.get("keyframe") or ""),
                    str(evidence.get("signal") or evidence.get("status") or evidence.get("judgment") or ""),
                )
                if signature in seen_rows:
                    continue
                seen_rows.add(signature)
                rows_by_item[item_id].append((sample_id, evidence, session_dir))
        sessions.append(
            {
                "summary_path": summary_path,
                "session_dir": session_dir,
                "sample_id": sample_id,
                "summary": summary,
                "outcomes": _item_outcomes(summary),
                "rows_by_item": rows_by_item,
            }
        )
    return sessions


def _annotate_manifest_labels(
    sessions: list[dict[str, Any]],
    manifest: Path | None,
) -> dict[str, set[str]]:
    """Attach optional full-score item hints from a video manifest.

    The manifest is used only to avoid selecting an obviously incorrect
    coarse-labeled video when a larger archive has uniformly passing summary
    scores.  Report outcomes and item-scoped artifact availability remain the
    required gates.  The returned map is kept private in ``_mock_audit``.
    """
    positive_by_key = _manifest_positive_items(manifest)
    if not positive_by_key:
        return {}
    for session in sessions:
        summary = session.get("summary")
        summary_key = _normalise_video_key(summary.get("video_path")) if isinstance(summary, Mapping) else ""
        sample_key = _normalise_video_key(session.get("sample_id"))
        matched = positive_by_key.get(summary_key) or positive_by_key.get(sample_key) or set()
        # Fuzzy matching handles a report that retained a mode/date suffix in
        # its video_path while the manifest contains the bare recording name.
        if not matched:
            for key, item_ids in positive_by_key.items():
                if (summary_key and (key in summary_key or summary_key in key)) or (
                    sample_key and (key in sample_key or sample_key in key)
                ):
                    matched = item_ids
                    break
        session["manifest_correct_items"] = set(matched)
        session["manifest_key"] = summary_key or sample_key
    return positive_by_key


def _apply_tool_profile(
    payload: dict[str, Any],
    source_run: Path,
    sample_count: int,
) -> None:
    """Apply a source profile when available, otherwise retain the template.

    The flat 29-video export contains the analysis keyframes but not the
    nested ``workflow_trace`` files used by ``workflow_tool_stats``.  That
    profile is presentation metadata rather than a scoring input, so keeping
    the checked-in profile is the correct mock-only fallback.
    """
    profile: Mapping[str, Any] = {}
    try:
        candidate = build_profile(source_run, expected_samples=sample_count)
        if isinstance(candidate, Mapping):
            profile = candidate
    except (OSError, ValueError, KeyError, TypeError):
        profile = {}
    profile_items = profile.get("items", {}) if isinstance(profile, Mapping) else {}
    fallback_items = load_tool_profile().get("items", {}) or {}
    for item in payload["items"]:
        item_id = str(item["item_id"])
        item_profile = profile_items.get(item_id) if isinstance(profile_items, Mapping) else None
        if not isinstance(item_profile, Mapping):
            item_profile = fallback_items.get(item_id) if isinstance(fallback_items, Mapping) else None
        if not isinstance(item_profile, Mapping):
            # A hand-authored minimal template may intentionally omit profile
            # metadata.  It remains valid; leave its existing fields intact.
            continue
        if item_profile.get("difficulty") in {"difficult", "medium", "easy"}:
            item["difficulty"] = item_profile["difficulty"]
        if item_profile.get("difficulty_label"):
            item["difficulty_label"] = item_profile["difficulty_label"]
        if isinstance(item_profile.get("analysis_profile"), Mapping):
            item["analysis_profile"] = deepcopy(item_profile["analysis_profile"])
        if isinstance(item_profile.get("analysis_tools"), list):
            item["analysis_tools"] = deepcopy(item_profile["analysis_tools"])


def build_mock(
    template: Mapping[str, Any],
    source_run: Path,
    *,
    seed: int | None = None,
    source_manifest: Path | None = None,
) -> dict[str, Any]:
    """Build a mock replay using one randomly selected correct video per item.

    ``seed`` is optional: omitting it gives a fresh random selection, while a
    fixed seed makes a checked-in fixture reproducible.  ``source_manifest``
    is useful when a large flat archive contains the named 29-video export;
    it selects one report per listed video before item-level sampling.
    """
    source_run = source_run.resolve()
    discovered = _discover_summary_paths(source_run)
    summaries = _select_manifest_summaries(discovered, manifest=source_manifest)
    if len(summaries) not in SUPPORTED_SOURCE_COUNTS:
        manifest_hint = "；可用 --video-manifest 指定 29 个视频" if source_manifest is None else ""
        raise ValueError(
            f"mock 源必须包含 10 或 29 个视频报告，实际找到 {len(summaries)} 个：{source_run}{manifest_hint}"
        )

    sessions = _load_sessions(summaries)
    if len(sessions) != len(summaries):
        raise ValueError(f"mock 源报告读取不完整：期望 {len(summaries)} 个，实际 {len(sessions)} 个")
    manifest_positive = _annotate_manifest_labels(sessions, source_manifest)

    payload = validated_copy(template)
    _apply_tool_profile(payload, source_run, len(summaries))
    payload["demo_mode"] = "mock_live_stream"
    payload["presentation"]["initial_state"] = "正在接入视频流"
    payload["_mock_audit"] = {
        "fixture_label": f"{len(summaries)}_video_fixture",
        "source_run": str(source_run),
        "source_manifest": str(source_manifest.resolve()) if source_manifest else None,
        "sample_count": len(summaries),
        "selection": "每个评分项从该项正确视频的分析过程产物中随机选一项",
        "analysis_window_ms": MOCK_ANALYSIS_DURATION_MS,
        "note": "仅供展示回放生成；不作为评分输入。",
    }
    if manifest_positive:
        payload["_mock_audit"]["manifest_positive_item_hint_count"] = sum(
            len(item_ids) for item_ids in manifest_positive.values()
        )

    # A physical frame may support several slots of one item, but never gets
    # borrowed by another item.  This ownership map also lets us retry a
    # random session when its only frame was already assigned elsewhere.
    path_owners: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    selected_audit: dict[str, Any] = {}
    random_source: Any = random.Random(seed) if seed is not None else random.SystemRandom()
    for item in payload["items"]:
        item_id = str(item["item_id"])
        # Build the candidate list once.  A rich 10-video run can expose a
        # successful item-level task even when its compact report score is
        # conservative; a flat 29-video export instead carries human item
        # labels in the manifest.  Correctness is a gate only: after choosing
        # the strongest available tier, source videos are shuffled uniformly.
        eligible: list[tuple[int, str, float, Mapping[str, Any], list[dict[str, Any]]]] = []
        for candidate_session in sessions:
            image_candidates = _session_image_candidates(item_id, candidate_session)
            if not image_candidates:
                continue
            tier, tier_source = _session_item_correctness_tier(item_id, candidate_session)
            if tier >= 0:
                quality = _session_item_visual_quality(item_id, candidate_session)
                eligible.append((tier, tier_source, quality, candidate_session, image_candidates))
        if not eligible:
            available = [
                str(session.get("sample_id") or "")
                for session in sessions
                if _outcome_is_correct((session.get("outcomes") or {}).get(item_id))
                or item_id in (session.get("manifest_correct_items") or set())
            ]
            detail = "；".join(available[:5]) if available else "没有完整得分样本"
            raise ValueError(f"{item_id}: 找不到带分析过程图像的正确视频（{detail}）")

        # Try correctness tiers from strongest to weakest.  Within one tier,
        # prefer entries with explicit task/segment provenance whenever such
        # entries exist, then shuffle uniformly.  This preserves random
        # item-level sampling while retaining compatibility with older exports
        # that only kept a row-level keyframe.
        by_tier: dict[int, list[tuple[int, str, float, Mapping[str, Any], list[dict[str, Any]]]]] = {}
        for entry in eligible:
            by_tier.setdefault(entry[0], []).append(entry)
        last_error: Exception | None = None
        built: tuple[dict[str, Any], dict[str, Any], dict[str, str], Mapping[str, Any]] | None = None
        for tier in sorted(by_tier, reverse=True):
            tier_entries = by_tier[tier]
            process_entries = [
                entry
                for entry in tier_entries
                if any(
                    isinstance(candidate, Mapping) and candidate.get("analysis_task")
                    for candidate in entry[4]
                )
            ]
            draw_entries = process_entries or tier_entries
            try:
                ordered = list(draw_entries)
                random_source.shuffle(ordered)
            except AttributeError:  # pragma: no cover - defensive custom RNG path
                ordered = list(draw_entries)
            for _tier, _tier_source, _quality, session, _candidates in ordered:
                trial_item = deepcopy(item)
                trial_owners = dict(path_owners)
                try:
                    empty_item, event = _build_item_event(trial_item, session, trial_owners)
                except ValueError as exc:
                    last_error = exc
                    continue
                built = (empty_item, event, trial_owners, session)
                break
            if built is not None:
                break
        if built is None:
            raise ValueError(f"{item_id}: 正确视频的过程证据无法绑定：{last_error}") from last_error
        _empty_item, event, path_owners, session = built
        events.append(event)
        outcome = (session.get("outcomes") or {}).get(item_id, {})
        selected_tier, selected_tier_source = _session_item_correctness_tier(item_id, session)
        selected_quality = _session_item_visual_quality(item_id, session)
        task_records = _item_task_records(item_id, session, _session_index_for(session))
        selected_task = next(
            (
                record
                for record in task_records
                if isinstance(record, Mapping)
                and record.get("positive")
                and isinstance(record.get("images"), list)
                and record.get("images")
            ),
            None,
        )
        selected_audit[item_id] = {
            "sample_id": str(session.get("sample_id") or ""),
            "summary_path": str(session.get("summary_path") or ""),
            "score": outcome.get("score") if isinstance(outcome, Mapping) else None,
            "max_score": outcome.get("max_score") if isinstance(outcome, Mapping) else None,
            "correctness_source": selected_tier_source,
            "correctness_tier": selected_tier,
            "selected_quality": round(selected_quality, 3),
            "analysis_task": str(selected_task.get("task_name") or "") if selected_task else None,
            "analysis_task_status": str(selected_task.get("status") or selected_task.get("evidence_status") or "") if selected_task else None,
            "visual_row_preferred": any(
                _row_is_direct_positive(row)
                for row in (session.get("rows_by_item") or {}).get(item_id, [])
                if isinstance(row, tuple) and len(row) == 3
            ),
        }
    payload["_mock_audit"]["selected_items"] = selected_audit
    payload["events"] = events
    return validated_copy(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 10 或 29 个 Engine artifacts 构建模拟实时报告 JSON")
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument(
        "--video-manifest",
        type=Path,
        help="大型平铺 artifacts 中的 10/29 个视频清单（JSON 或每行一个路径）",
    )
    parser.add_argument("--seed", type=int, help="固定随机种子；省略时每次随机抽取")
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_mock(
        _read_json(args.template),
        args.source_run,
        seed=args.seed,
        source_manifest=args.video_manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 mock JSON：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
