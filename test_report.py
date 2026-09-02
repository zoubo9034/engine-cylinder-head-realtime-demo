#!/usr/bin/env python3
"""Small deterministic checks for the isolated demo deliverables."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from report_schema import ITEM_DEFINITIONS, template_payload, validate_report
from render_report import FORBIDDEN_HTML_MARKERS, public_projection, render_html
from serve_demo import DemoState


class ReportContractTest(unittest.TestCase):
    def test_template_has_exact_live_scope(self) -> None:
        payload = template_payload()
        self.assertEqual(payload["schema"], "realtime-evidence-report/v1")
        self.assertEqual([(x["item_number"], x["item_id"]) for x in payload["items"]], [(x["item_number"], x["item_id"]) for x in ITEM_DEFINITIONS])
        self.assertFalse(payload["presentation"]["show_scores"])
        self.assertFalse(payload["presentation"]["show_source_provenance"])
        self.assertNotIn("autoplay", payload["presentation"])
        self.assertEqual(validate_report(payload), [])

    def test_first_tightening_wording_is_a_clear_process_criterion(self) -> None:
        item = next(item for item in template_payload()["items"] if item["item_number"] == 20)
        self.assertIn("1—10号气缸盖螺栓", item["prefilled_standard_text"])
        self.assertIn("25 N·m", item["prefilled_standard_text"])
        self.assertNotIn("识别扭力扳手按1–10顺序", item["prefilled_standard_text"])

    def test_public_html_has_no_internal_markers(self) -> None:
        html = render_html(template_payload())
        for marker in FORBIDDEN_HTML_MARKERS:
            self.assertNotIn(marker, html)
        self.assertNotIn("26/26", html)
        self.assertEqual(len(re.findall(r'class="item-card', html)), 1)
        self.assertIn("视频流已连接", html)
        self.assertIn("证据生成中", html)
        self.assertIn("启动评测", html)
        self.assertNotIn("1—10号气缸盖螺栓", html)
        self.assertNotIn("25 N·m", html)
        self.assertNotIn("下一项", html)
        self.assertNotIn("已完成展示", html)

    def test_evaluation_text_is_disclosed_only_after_completion(self) -> None:
        payload = template_payload()
        item = next(item for item in payload["items"] if item["item_number"] == 20)
        item["live_binding"]["state"] = "已完成评分"
        html = render_html(payload)
        self.assertIn("1—10号气缸盖螺栓", html)
        self.assertIn("评分结论", html)

    def test_public_projection_drops_rubric_snapshot(self) -> None:
        projection = public_projection(template_payload())
        self.assertNotIn("source", projection)
        self.assertNotIn("display_score", projection["scope"])
        self.assertNotIn("full_rubric_score", projection["scope"])
        self.assertTrue(all("prefilled_score" not in item for item in projection["items"]))
        self.assertTrue(all("realtime_target" not in item for item in projection["items"]))

    def test_source_path_cannot_be_reused(self) -> None:
        payload = template_payload()
        path = "/tmp/example.jpg"
        for item in payload["items"][:2]:
            item["live_binding"]["evidence"] = [{"source_path": path}]
        self.assertTrue(any("reused across items" in e for e in validate_report(payload)))

    def test_mock_fixture_is_empty_until_event_patch(self) -> None:
        path = Path(__file__).with_name("展示标准报告_8-20_mock.json")
        if not path.exists():
            self.skipTest("mock fixture has not been generated")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(validate_report(payload), [])
        self.assertEqual(len(payload.get("events", [])), len(ITEM_DEFINITIONS))
        self.assertTrue(all(not item["live_binding"]["evidence"] for item in payload["items"]))
        self.assertTrue(all(event.get("item_patch") for event in payload["events"]))
        self.assertTrue(all(event["item_patch"]["live_binding"]["state"] in {"已完成评分", "证据生成中", "待人工确认"} for event in payload["events"]))

    def test_reset_restores_empty_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            payload = template_payload()
            payload["items"][0]["live_binding"]["state"] = "证据生成中"
            payload["items"][0]["live_binding"]["revision"] = 4
            report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            state = DemoState(root, report_path)
            reset = state.reset()
            self.assertEqual(validate_report(reset), [])
            disk = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(disk["items"][0]["live_binding"]["state"], "待开始")
            self.assertEqual(disk["items"][0]["live_binding"]["revision"], 0)

    def test_mock_reset_keeps_replay_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "mock.json"
            payload = template_payload()
            payload["demo_mode"] = "mock_live_stream"
            payload["events"] = [{"event_id": "evt-test", "item_id": "item_5069", "item_patch": payload["items"][0]}]
            report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            state = DemoState(root, report_path)
            reset = state.reset()
            self.assertEqual(len(reset["events"]), 1)
            self.assertEqual(reset["items"][0]["live_binding"]["state"], "待开始")


if __name__ == "__main__":
    unittest.main()
