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
    # stage-run prefix (``15/keyframes/...``).  Match only a suffix within the
    # selected session, never a sibling video directory.
    candidates = list(image_index or ())
    if not candidates:
        candidates = _session_image_index(session_dir)
    wanted = _path_parts(value)
    if not wanted:
        return None
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

    def visit(value: Any) -> None:
        if isinstance(value, str):
            path = _resolve_image_path(value, session_dir, image_index)
            if path is not None and path not in seen:
                seen.add(path)
                paths.append(path)
            return
        if isinstance(value, Mapping):
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                visit(nested)

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
        visit(evidence.get(key))

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
            visit(value)
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
    session_dir = Path(str(session.get("session_dir") or ""))
    index = list(image_index)
    by_item: dict[str, list[Path]] = {
        str(definition["item_id"]): [] for definition in ITEM_DEFINITIONS
    }
    seen: dict[str, set[Path]] = {key: set() for key in by_item}
    json_paths: list[Path] = []
    # Keep the scan bounded and focused on analysis outputs.  ``intermediate``
    # is included for older runs whose task result was promoted there.
    for root in (session_dir / "artifacts" / "evidence_enrichment", session_dir / "intermediate"):
        if not root.is_dir():
            continue
        try:
            for path in sorted(root.glob("**/task_result.json")):
                json_paths.append(path)
                if len(json_paths) >= 300:
                    break
            if len(json_paths) < 300:
                for path in sorted(root.glob("**/*result*.json")):
                    if path not in json_paths:
                        json_paths.append(path)
                        if len(json_paths) >= 300:
                            break
        except OSError:
            continue
        if len(json_paths) >= 300:
            break

    active_ids = set(by_item)
    for json_path in json_paths:
        try:
            value = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping):
            continue
        claims: set[str] = set()
        for key in ("claim_ids", "items", "item_ids", "criterion_ids"):
            raw = value.get(key)
            if isinstance(raw, str):
                claims.add(raw)
            elif isinstance(raw, (list, tuple, set)):
                claims.update(str(entry) for entry in raw)
        claims.add(str(value.get("item_id") or ""))
        claims_text = " ".join(claims).casefold()
        owned = [candidate for candidate in active_ids if candidate.casefold() in claims_text]
        if not owned:
            continue
        # Only inspect fields that can carry file references.  This avoids
        # treating a prose description containing ``.jpg`` as an artifact.
        paths = _supporting_image_paths(value, session_dir, index)
        if not paths:
            # Some task results list labels but place the sampled frames next
            # to the result file.  They are still part of this claimed task,
            # so use a small deterministic local set.
            try:
                paths = [
                    path
                    for path in sorted(json_path.parent.glob("*"))
                    if _path_is_image(path)
                ][:24]
            except OSError:
                paths = []
        for owner in owned:
            for path in paths:
                resolved = path.resolve() if path.exists() else path
                if resolved in seen[owner]:
                    continue
                seen[owner].add(resolved)
                by_item[owner].append(resolved)

    # Keep the cache attached to the session so 13 item probes do not repeat a
    # recursive JSON scan.
    try:
        session["item_analysis_artifacts"] = by_item  # type: ignore[index]
    except (TypeError, AttributeError):
        pass
    return by_item.get(item_id, [])


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

    # First bind exact row artifacts and timestamp-matched detector overlays.
    # These are the outputs of this item's analysis task, rather than a frame
    # borrowed from another item in the shared stage directory.
    direct_sources: list[tuple[Path, Mapping[str, Any], Path, str, float | None]] = []
    for _sample_id, evidence, row_session_dir in ordered_rows:
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
    if len(candidates) < limit:
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
                allow_tie=False,
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
        report_correct_sessions = [
            session
            for session in sessions
            if _outcome_is_correct((session.get("outcomes") or {}).get(item_id))
            and _session_image_candidates(item_id, session)
        ]
        manifest_candidates = [
            session
            for session in sessions
            if item_id in (session.get("manifest_correct_items") or set())
            and _session_image_candidates(item_id, session)
        ]
        # A positive full-score annotation is a useful guard against the
        # legacy flat reports' over-eager item score normalization.  Prefer its
        # intersection with a complete report outcome; if that intersection is
        # empty, the annotated visual candidate is still the best available
        # source for this mock-only, all-correct replay.  With no positive
        # annotation for an item, retain the report outcome gate.
        if manifest_candidates:
            intersected = [session for session in manifest_candidates if session in report_correct_sessions]
            correct_sessions = intersected or manifest_candidates
            correctness_source = "manifest_full_score" if intersected else "manifest_visual_full_score"
        else:
            correct_sessions = report_correct_sessions
            correctness_source = "report_full_score"
        if not correct_sessions:
            available = [
                str(session.get("sample_id") or "")
                for session in sessions
                if _outcome_is_correct((session.get("outcomes") or {}).get(item_id))
            ]
            detail = "；".join(available[:5]) if available else "没有完整得分样本"
            raise ValueError(f"{item_id}: 找不到带分析过程图像的正确视频（{detail}）")

        # Shuffle the correct sessions once per item.  The first session that
        # can satisfy all required slots without cross-item frame reuse wins;
        # this preserves random item-level selection while remaining robust to
        # a source video whose single frame is already owned by another item.
        try:
            ordered = list(correct_sessions)
            random_source.shuffle(ordered)
        except AttributeError:  # pragma: no cover - defensive custom RNG path
            ordered = list(correct_sessions)
        last_error: Exception | None = None
        built: tuple[dict[str, Any], dict[str, Any], dict[str, str], Mapping[str, Any]] | None = None
        for session in ordered:
            trial_item = deepcopy(item)
            trial_owners = dict(path_owners)
            try:
                empty_item, event = _build_item_event(trial_item, session, trial_owners)
            except ValueError as exc:
                last_error = exc
                continue
            built = (empty_item, event, trial_owners, session)
            break
        if built is None:
            raise ValueError(f"{item_id}: 正确视频的过程证据无法绑定：{last_error}") from last_error
        _empty_item, event, path_owners, session = built
        events.append(event)
        outcome = (session.get("outcomes") or {}).get(item_id, {})
        selected_audit[item_id] = {
            "sample_id": str(session.get("sample_id") or ""),
            "summary_path": str(session.get("summary_path") or ""),
            "score": outcome.get("score") if isinstance(outcome, Mapping) else None,
            "max_score": outcome.get("max_score") if isinstance(outcome, Mapping) else None,
            "correctness_source": correctness_source,
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
