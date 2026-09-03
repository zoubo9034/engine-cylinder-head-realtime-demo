#!/usr/bin/env python3
"""Serve the isolated report and expose a tiny live-report API.

The browser never reads the JSON file directly.  ``/api/report`` returns the
same public projection used by the static renderer, while ``/api/reset``
atomically restores the configured report to the empty template.  An external
recogniser can use ``/api/update`` to write one item binding at a time.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import tempfile
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from detail_rules import prefilled_result_for
from render_report import public_projection
from report_schema import TERMINAL_LIVE_STATES, template_payload, validated_copy


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


ANALYSIS_DURATION_MIN_MS = 8_000
ANALYSIS_DURATION_MAX_MS = 20_000
TERMINAL_SCORE_BY_STATE = {"证据已绑定": 1, "已完成评分": 1, "待人工确认": 0}


class DemoState:
    """Own the mutable live report and its short analysis jobs.

    A completed set of required slots is deliberately not promoted directly
    to a score.  It first enters ``证据生成中`` and receives an in-memory,
    8–20-second analysis deadline.  Only when that deadline expires are the
    hidden standard conclusions copied into the active detail result.  The
    clock and random source are injectable so the lifecycle can be tested
    without sleeping.
    """

    def __init__(
        self,
        root: Path,
        report_path: Path,
        *,
        clock: Callable[[], float] | None = None,
        rng: Any | None = None,
    ) -> None:
        self.root = root.resolve()
        self.report_path = report_path.resolve()
        self.lock = threading.RLock()
        self._clock = clock or time.monotonic
        self._rng = rng or random.SystemRandom()
        self._analysis_jobs: dict[str, dict[str, Any]] = {}
        if self.root not in self.report_path.parents:
            raise ValueError("报告文件必须位于演示目录内")
        self.report_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_locked(self) -> dict[str, Any]:
        value = json.loads(self.report_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("报告 JSON 顶层必须是对象")
        return validated_copy(value)

    def _write_locked(self, payload: Mapping[str, Any]) -> None:
        checked = validated_copy(payload)
        try:
            target_mode = self.report_path.stat().st_mode & 0o777
        except FileNotFoundError:
            target_mode = 0o664
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.report_path.name}.",
            suffix=".tmp",
            dir=self.report_path.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(checked, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, target_mode)
            os.replace(temp_name, self.report_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def read(self) -> dict[str, Any]:
        with self.lock:
            payload = self._load_locked()
            if self._advance_jobs_locked(payload):
                self._write_locked(payload)
            return validated_copy(payload)

    def write(self, payload: Mapping[str, Any]) -> None:
        with self.lock:
            self._write_locked(payload)

    @staticmethod
    def _all_slots(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [
            slot
            for slot in list(item.get("required_evidence_slots", []) or [])
            + list(item.get("enhanced_evidence_slots", []) or [])
            if isinstance(slot, Mapping)
        ]

    @staticmethod
    def _record_key(record: Mapping[str, Any]) -> str:
        return str(record.get("evidence_id") or record.get("source_path") or "")

    def _slot_evidence_map(self, item: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
        """Return slot-owned records, accepting both slot and binding forms."""
        binding = item.get("live_binding", {}) or {}
        binding_records = list(binding.get("evidence", []) or []) if isinstance(binding, Mapping) else []
        by_slot: dict[str, list[Mapping[str, Any]]] = {}
        for slot in self._all_slots(item):
            slot_id = str(slot.get("slot_id") or "")
            records = [
                record
                for record in list(slot.get("evidence", []) or [])
                if isinstance(record, Mapping)
            ]
            # Some recognisers attach a slot_id/slot_ids marker only to the
            # binding-level record.  Accept that explicit ownership without
            # guessing from captions or image names.
            if not records:
                for record in binding_records:
                    if not isinstance(record, Mapping):
                        continue
                    declared = record.get("slot_ids", record.get("slot_id", []))
                    if isinstance(declared, str):
                        declared_ids = {declared}
                    elif isinstance(declared, (list, tuple, set)):
                        declared_ids = {str(value) for value in declared}
                    else:
                        declared_ids = set()
                    if slot_id in declared_ids:
                        records.append(record)
            by_slot[slot_id] = records
        return by_slot

    def _required_evidence_complete(self, item: Mapping[str, Any]) -> bool:
        """Check only explicit required-slot content; never infer a result."""
        required_ids = [
            str(value)
            for value in (item.get("completion_condition", {}) or {}).get("required_slot_ids", []) or []
        ]
        if not required_ids:
            required_ids = [
                str(slot.get("slot_id") or "")
                for slot in self._all_slots(item)
                if slot.get("required") is True
            ]
        slot_map = self._slot_evidence_map(item)
        binding = item.get("live_binding", {}) or {}
        binding_timestamp = binding.get("live_timestamp") if isinstance(binding, Mapping) else None
        for slot_id in required_ids:
            records = slot_map.get(slot_id, [])
            # A timestamp is allowed to be carried in the binding scalar, as
            # the live API has historically sent it separately from slots.
            if slot_id == "live_timestamp" and str(binding_timestamp or "").strip():
                continue
            if not records:
                return False
            if not any(self._record_key(record) or slot_id == "live_timestamp" for record in records):
                return False
        return bool(required_ids)

    @staticmethod
    def _has_bound_slot(item: Mapping[str, Any]) -> bool:
        for slot in DemoState._all_slots(item):
            if str(slot.get("status") or "") == "bound" and list(slot.get("evidence", []) or []):
                return True
        binding = item.get("live_binding", {}) or {}
        return isinstance(binding, Mapping) and bool(binding.get("evidence"))

    def _duration_ms(self) -> int:
        rng = self._rng
        try:
            value = rng.randint(ANALYSIS_DURATION_MIN_MS, ANALYSIS_DURATION_MAX_MS)
        except AttributeError:
            try:
                value = rng.randrange(ANALYSIS_DURATION_MIN_MS, ANALYSIS_DURATION_MAX_MS + 1)
            except AttributeError:
                value = random.SystemRandom().randint(ANALYSIS_DURATION_MIN_MS, ANALYSIS_DURATION_MAX_MS)
        try:
            return max(ANALYSIS_DURATION_MIN_MS, min(ANALYSIS_DURATION_MAX_MS, int(value)))
        except (TypeError, ValueError):
            return ANALYSIS_DURATION_MIN_MS

    def _schedule_analysis_locked(self, item: Mapping[str, Any], *, restart: bool = True) -> dict[str, Any]:
        item_id = str(item.get("item_id") or "")
        binding = item.get("live_binding", {}) or {}
        revision = int(binding.get("revision") or 0) if isinstance(binding, Mapping) else 0
        previous = self._analysis_jobs.get(item_id)
        if previous and not restart:
            previous["revision"] = revision
            return previous
        duration_ms = self._duration_ms()
        started_at = float(self._clock())
        job = {
            "revision": revision,
            "started_at": started_at,
            "duration_ms": duration_ms,
            "due_at": started_at + duration_ms / 1000.0,
        }
        self._analysis_jobs[item_id] = job
        return job

    @staticmethod
    def _wall_timestamp() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _set_locked_detail(self, item: dict[str, Any]) -> None:
        item["detail_evaluation"] = {
            "state": "locked",
            "updated_at": None,
            "checks": [],
            "unresolved_summary": "",
        }

    def _set_analyzing_detail(self, item: dict[str, Any]) -> None:
        item["detail_evaluation"] = {
            "state": "analyzing",
            "updated_at": None,
            "checks": [],
            "unresolved_summary": "",
        }

    def _materialize_prefilled_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        binding = item.get("live_binding", {}) or {}
        slot_map = self._slot_evidence_map(item)
        confidence_values: list[float] = []
        for records in slot_map.values():
            for record in records:
                value = record.get("confidence")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    confidence_values.append(max(0.0, min(1.0, float(value))))
        if isinstance(binding, Mapping):
            value = binding.get("time_confidence")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                confidence_values.append(max(0.0, min(1.0, float(value))))
        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.96
        updated_at = None
        if isinstance(binding, Mapping):
            timestamp = binding.get("live_timestamp")
            if isinstance(timestamp, str) and timestamp.strip():
                updated_at = timestamp.strip()
        updated_at = updated_at or self._wall_timestamp()
        result = prefilled_result_for(
            str(item.get("item_id") or ""),
            slot_map,
            updated_at=updated_at,
            confidence=confidence,
        )
        detail = result["detail_evaluation"]
        # Keep the hidden result's shape synchronized with the evidence-bound
        # variant when a recogniser supplied a newer preset in a hand-authored
        # payload, while retaining the canonical positive wording.
        item["detail_evaluation"] = detail
        return detail

    def _advance_jobs_locked(self, payload: dict[str, Any]) -> bool:
        """Promote due jobs and return whether the JSON snapshot changed."""
        changed = False
        # A process restart loses only the in-memory deadline.  Recreate a
        # fresh bounded window for an item that was persisted mid-analysis.
        for item in payload.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or "")
            binding = item.get("live_binding", {}) or {}
            state = str(binding.get("state") or "") if isinstance(binding, Mapping) else ""
            if state == "证据生成中" and self._required_evidence_complete(item):
                if item_id not in self._analysis_jobs:
                    self._schedule_analysis_locked(item)
            elif item_id in self._analysis_jobs:
                self._analysis_jobs.pop(item_id, None)

        now = float(self._clock())
        for item_id, job in list(self._analysis_jobs.items()):
            item = next(
                (entry for entry in payload.get("items", []) or [] if isinstance(entry, dict) and str(entry.get("item_id") or "") == item_id),
                None,
            )
            if item is None:
                self._analysis_jobs.pop(item_id, None)
                continue
            binding = item.get("live_binding", {}) or {}
            revision = int(binding.get("revision") or 0) if isinstance(binding, Mapping) else 0
            if (
                not isinstance(binding, Mapping)
                or binding.get("state") != "证据生成中"
                or revision != int(job.get("revision") or 0)
            ):
                self._analysis_jobs.pop(item_id, None)
                continue
            if now < float(job.get("due_at") or 0.0):
                continue
            if not self._required_evidence_complete(item):
                self._analysis_jobs.pop(item_id, None)
                binding["state"] = "已定位"
                item["score"] = None
                self._set_locked_detail(item)
                changed = True
                continue
            binding["state"] = "已完成评分"
            binding["evidence_explanation"] = "对象、动作、时序和完成状态均已确认。"
            item["score"] = 1
            self._materialize_prefilled_detail(item)
            self._analysis_jobs.pop(item_id, None)
            changed = True
        return changed

    def _normalise_changed_slots(self, item: Mapping[str, Any], supplied: Any) -> list[str]:
        if isinstance(supplied, list):
            values = [str(value) for value in supplied if str(value)]
            if values:
                return list(dict.fromkeys(values))
        return [
            str(slot.get("slot_id") or "")
            for slot in self._all_slots(item)
            if str(slot.get("status") or "") == "bound" and list(slot.get("evidence", []) or [])
        ]

    def update(self, body: Mapping[str, Any]) -> dict[str, Any]:
        with self.lock:
            payload = self._load_locked()
            # Finish a deadline that elapsed between requests before applying
            # the next patch.  This keeps revision and state transitions
            # serialized under the same lock.
            self._advance_jobs_locked(payload)
            incoming_patch = body.get("item_patch")
            item_id = str(
                body.get("item_id")
                or (incoming_patch.get("item_id") if isinstance(incoming_patch, Mapping) else "")
                or ""
            )
            if not item_id:
                raise ValueError("缺少 item_id")
            item = next((entry for entry in payload["items"] if entry.get("item_id") == item_id), None)
            if item is None:
                raise ValueError(f"未知评分项：{item_id}")

            previous_binding = item.get("live_binding", {}) or {}
            old_state = str(previous_binding.get("state") or "待开始") if isinstance(previous_binding, Mapping) else "待开始"
            previous_revision = int(previous_binding.get("revision") or 0) if isinstance(previous_binding, Mapping) else 0
            old_evidence_signature = json.dumps(
                self._evidence_snapshot(item), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            explicit_state: str | None = None
            detail_supplied = False

            def apply_patch(patch: Mapping[str, Any]) -> None:
                nonlocal explicit_state, detail_supplied
                if str(patch.get("item_id") or item_id) != item_id:
                    raise ValueError("item_patch.item_id 与 item_id 不一致")
                binding_patch = patch.get("live_binding")
                if isinstance(binding_patch, Mapping):
                    if "state" in binding_patch:
                        explicit_state = str(binding_patch.get("state") or "")
                    binding = item.setdefault("live_binding", {})
                    if not isinstance(binding, dict):
                        binding = {}
                        item["live_binding"] = binding
                    binding.update(dict(binding_patch))
                for key in ("required_evidence_slots", "enhanced_evidence_slots"):
                    if key in patch and isinstance(patch.get(key), list):
                        item[key] = patch[key]
                if "detail_evaluation" in patch and isinstance(patch.get("detail_evaluation"), Mapping):
                    item["detail_evaluation"] = dict(patch["detail_evaluation"])
                    detail_supplied = True

            if isinstance(incoming_patch, Mapping):
                apply_patch(incoming_patch)
            else:
                binding_patch = body.get("live_binding")
                if isinstance(binding_patch, Mapping):
                    apply_patch({"item_id": item_id, "live_binding": binding_patch})
                for key in ("required_evidence_slots", "enhanced_evidence_slots"):
                    if key in body and isinstance(body.get(key), list):
                        item[key] = body[key]
                if isinstance(body.get("detail_evaluation"), Mapping):
                    item["detail_evaluation"] = dict(body["detail_evaluation"])
                    detail_supplied = True

            binding = item.setdefault("live_binding", {})
            if not isinstance(binding, dict):
                binding = {}
                item["live_binding"] = binding
            requested_revision = int(binding.get("revision") or 0)
            binding["revision"] = max(previous_revision + 1, requested_revision)
            supplied_changed_slots = body.get("changed_slot_ids")
            if not isinstance(supplied_changed_slots, list):
                supplied_changed_slots = binding.get("changed_slot_ids")
            binding["changed_slot_ids"] = self._normalise_changed_slots(item, supplied_changed_slots)

            new_evidence_signature = json.dumps(
                self._evidence_snapshot(item), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            evidence_changed = new_evidence_signature != old_evidence_signature
            complete = self._required_evidence_complete(item)

            # A complete required set always starts a fresh analysis cycle
            # unless the caller explicitly supplied a terminal/manual result.
            if explicit_state == "待人工确认":
                final_state = "待人工确认"
                self._analysis_jobs.pop(item_id, None)
            elif explicit_state in {"已完成评分", "证据已绑定"}:
                final_state = explicit_state
                self._analysis_jobs.pop(item_id, None)
            elif explicit_state in {"已定位", "待开始"}:
                # A recogniser may label the envelope as located while the
                # final required slot is arriving in the same patch.  The
                # complete-evidence contract takes precedence and starts the
                # bounded analysis phase immediately.
                if complete:
                    final_state = "证据生成中"
                else:
                    final_state = explicit_state
                    self._analysis_jobs.pop(item_id, None)
            elif explicit_state == "证据生成中":
                final_state = "证据生成中"
            elif complete:
                final_state = "证据生成中"
            elif old_state == "证据生成中":
                # Progressive slot updates can arrive before the final
                # required slot; keep the visible analysis phase intact.
                final_state = "证据生成中"
            elif self._has_bound_slot(item):
                final_state = "已定位"
                self._analysis_jobs.pop(item_id, None)
            else:
                final_state = "待开始"
                self._analysis_jobs.pop(item_id, None)

            binding["state"] = final_state
            if final_state == "证据生成中":
                self._set_analyzing_detail(item)
                if complete:
                    existing = self._analysis_jobs.get(item_id)
                    # Updating metadata while analysis is in progress keeps
                    # its original bounded deadline; replacing evidence
                    # starts a new 8–20-second window.
                    self._schedule_analysis_locked(
                        item,
                        restart=not (existing and old_state == "证据生成中" and not evidence_changed),
                    )
                else:
                    self._analysis_jobs.pop(item_id, None)
                item["score"] = None
            elif final_state in TERMINAL_LIVE_STATES:
                if explicit_state in {"已完成评分", "证据已绑定"} and complete and not detail_supplied:
                    self._materialize_prefilled_detail(item)
                elif detail_supplied:
                    # Keep supplied observations for a real/manual terminal
                    # result; the renderer applies its own public gating.
                    pass
                else:
                    detail = item.get("detail_evaluation")
                    if not isinstance(detail, Mapping) or not (detail.get("checks") or []):
                        item["detail_evaluation"] = {
                            "state": "unavailable",
                            "updated_at": None,
                            "checks": [],
                            "unresolved_summary": "仍有核验项待确认。" if final_state == "待人工确认" else "详细结果待提供。",
                        }
                item["score"] = TERMINAL_SCORE_BY_STATE[final_state]
                self._analysis_jobs.pop(item_id, None)
            else:
                self._set_locked_detail(item)
                item["score"] = None

            self._write_locked(payload)
            return validated_copy(payload)

    @staticmethod
    def _evidence_snapshot(item: Mapping[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        binding = item.get("live_binding", {}) or {}
        candidates: list[Any] = []
        if isinstance(binding, Mapping):
            candidates.extend(binding.get("evidence", []) or [])
        candidates.extend(
            record
            for slot in DemoState._all_slots(item)
            for record in list(slot.get("evidence", []) or [])
        )
        for record in candidates:
            if not isinstance(record, Mapping):
                continue
            key = str(record.get("evidence_id") or record.get("source_path") or repr(sorted(record.items())))
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "evidence_id": str(record.get("evidence_id") or ""),
                "source_path": str(record.get("source_path") or ""),
                "timestamp": str(record.get("timestamp") or ""),
                "timestamp_sec": record.get("timestamp_sec"),
            })
        return records

    def reset(self) -> dict[str, Any]:
        with self.lock:
            previous = self._load_locked()
            self._analysis_jobs.clear()
            payload = template_payload()
            # Keep only the replay schedule when the configured file is a mock
            # fixture.  The fresh template clears all bindings and details.
            if previous.get("demo_mode") == "mock_live_stream" or previous.get("events"):
                payload["demo_mode"] = previous.get("demo_mode", "mock_live_stream")
                payload["events"] = list(previous.get("events") or [])
            self.write(payload)
            return payload

def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _live_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the small polling payload; replay events stay embedded in HTML."""
    view = public_projection(payload)
    view["events"] = []
    return view


def make_handler(state: DemoState, root: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[demo] {self.address_string()} - {format % args}")

        def _send_json(self, status: int, value: Any) -> None:
            data = _json_bytes(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as exc:
                raise ValueError("无效 Content-Length") from exc
            if length <= 0 or length > 4 * 1024 * 1024:
                raise ValueError("请求体为空或超过 4 MiB")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("请求 JSON 顶层必须是对象")
            return value

        def do_GET(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            try:
                if route == "/api/health":
                    self._send_json(200, {"ok": True, "service": "engine-cylinder-head-realtime-demo"})
                elif route == "/api/report":
                    self._send_json(200, _live_projection(state.read()))
                else:
                    super().do_GET()
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            try:
                if route == "/api/reset":
                    self._send_json(200, _live_projection(state.reset()))
                elif route == "/api/update":
                    self._send_json(200, _live_projection(state.update(self._read_body())))
                else:
                    self._send_json(404, {"ok": False, "error": "unknown API route"})
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="启动发动机气缸盖实时报告本地演示服务")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--report", type=Path, default=Path("展示标准报告_8-20.json"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    root = args.root.resolve()
    report_path = args.report if args.report.is_absolute() else root / args.report
    state = DemoState(root, report_path)
    server = ReusableThreadingHTTPServer((args.host, args.port), make_handler(state, root))
    print(f"实时报告服务已启动：http://{args.host}:{args.port}/展示标准报告_8-20.html")
    print(f"报告数据文件：{state.report_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n实时报告服务已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
