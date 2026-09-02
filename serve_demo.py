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
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from render_report import public_projection
from report_schema import template_payload, validated_copy


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class DemoState:
    def __init__(self, root: Path, report_path: Path) -> None:
        self.root = root.resolve()
        self.report_path = report_path.resolve()
        self.lock = threading.RLock()
        if self.root not in self.report_path.parents:
            raise ValueError("报告文件必须位于演示目录内")
        self.report_path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any]:
        with self.lock:
            value = json.loads(self.report_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("报告 JSON 顶层必须是对象")
            return validated_copy(value)

    def write(self, payload: Mapping[str, Any]) -> None:
        with self.lock:
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

    def reset(self) -> dict[str, Any]:
        with self.lock:
            previous = self.read()
            payload = template_payload()
            # Keep the replay schedule when the configured file is the generated
            # mock fixture.  Reset clears every live binding but must not destroy
            # the artifact-backed event stream that makes the fixture replayable.
            if previous.get("demo_mode") == "mock_live_stream" or previous.get("events"):
                payload["demo_mode"] = previous.get("demo_mode", "mock_live_stream")
                if isinstance(previous.get("_mock_audit"), Mapping):
                    payload["_mock_audit"] = dict(previous["_mock_audit"])
                payload["events"] = list(previous.get("events") or [])
            self.write(payload)
            return payload

    def update(self, body: Mapping[str, Any]) -> dict[str, Any]:
        with self.lock:
            item_id = str(body.get("item_id") or "")
            if not item_id:
                raise ValueError("缺少 item_id")
            payload = self.read()
            item = next((entry for entry in payload["items"] if entry.get("item_id") == item_id), None)
            if item is None:
                raise ValueError(f"未知评分项：{item_id}")

            incoming = body.get("item_patch")
            if isinstance(incoming, Mapping):
                # Accept a complete item patch from an evaluator, but keep the
                # immutable item identity and order owned by the template.
                if str(incoming.get("item_id") or item_id) != item_id:
                    raise ValueError("item_patch.item_id 与 item_id 不一致")
                for key in ("live_binding", "required_evidence_slots", "enhanced_evidence_slots"):
                    if key in incoming:
                        item[key] = incoming[key]
            else:
                binding_patch = body.get("live_binding")
                if isinstance(binding_patch, Mapping):
                    binding = item.setdefault("live_binding", {})
                    binding.update(dict(binding_patch))
                slots_patch = body.get("required_evidence_slots")
                if isinstance(slots_patch, list):
                    item["required_evidence_slots"] = slots_patch

            binding = item.setdefault("live_binding", {})
            old_revision = int(binding.get("revision") or 0)
            requested_revision = int(binding.get("revision") or 0)
            binding["revision"] = max(old_revision + 1, requested_revision)
            changed = body.get("changed_slot_ids")
            if isinstance(changed, list):
                binding["changed_slot_ids"] = [str(value) for value in changed]
            elif not binding.get("changed_slot_ids"):
                binding["changed_slot_ids"] = [
                    str(slot.get("slot_id")) for slot in item.get("required_evidence_slots", [])
                    if slot.get("status") == "bound"
                ]
            state = str(binding.get("state") or "待开始")
            item["score"] = {"证据已绑定": 1, "已完成评分": 1, "待人工确认": 0}.get(state)
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
