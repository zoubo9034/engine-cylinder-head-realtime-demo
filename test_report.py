#!/usr/bin/env python3
"""Small deterministic checks for the isolated demo deliverables."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from report_schema import DIFFICULTY_LABELS, ITEM_DEFINITIONS, template_payload, validate_report
from render_report import FORBIDDEN_HTML_MARKERS, public_projection, render_html
from serve_demo import DemoState


class ReportContractTest(unittest.TestCase):
    def test_template_has_exact_live_scope(self) -> None:
        payload = template_payload()
        self.assertEqual(payload["schema"], "realtime-evidence-report/v1")
        self.assertEqual([(x["item_number"], x["item_id"]) for x in payload["items"]], [(x["item_number"], x["item_id"]) for x in ITEM_DEFINITIONS])
        self.assertTrue(payload["presentation"]["show_scores"])
        self.assertEqual(payload["presentation"]["score_reveal"], "terminal_only")
        self.assertFalse(payload["presentation"]["show_source_provenance"])
        self.assertNotIn("autoplay", payload["presentation"])
        self.assertEqual(validate_report(payload), [])

    def test_first_tightening_wording_is_a_clear_process_criterion(self) -> None:
        item = next(item for item in template_payload()["items"] if item["item_number"] == 20)
        self.assertIn("1—10号气缸盖螺栓", item["prefilled_standard_text"])
        self.assertIn("25 N·m", item["prefilled_standard_text"])
        self.assertNotIn("识别扭力扳手按1–10顺序", item["prefilled_standard_text"])

    def test_difficulty_and_tool_profiles_are_prefilled(self) -> None:
        payload = template_payload()
        self.assertEqual({item["difficulty_label"] for item in payload["items"]}, {"困难", "中等", "简单"})
        self.assertTrue(all(item["difficulty"] in DIFFICULTY_LABELS for item in payload["items"]))
        self.assertTrue(all(item["analysis_tools"] for item in payload["items"]))
        self.assertEqual(payload["items"][0]["analysis_profile"]["sample_count"], 10)
        self.assertEqual(payload["items"][0]["analysis_profile"]["workflow_trace_count"], 10)
        for item in payload["items"]:
            profile = item["analysis_profile"]
            self.assertIsInstance(profile["actual_analysis_task_count"], int)
            self.assertLessEqual(
                abs(profile["actual_analysis_task_count"] - profile["average_tool_task_count"]),
                3,
            )

    def test_public_html_has_no_internal_markers(self) -> None:
        html = render_html(template_payload())
        for marker in FORBIDDEN_HTML_MARKERS:
            self.assertNotIn(marker, html)
        self.assertNotIn("26/26", html)
        self.assertEqual(len(re.findall(r'class="item-card', html)), 1)
        self.assertIn("视频流已连接", html)
        self.assertIn("证据生成中", html)
        self.assertIn("启动评测", html)
        self.assertIn("困难", html)
        self.assertIn("简单", html)
        self.assertNotIn("高风险", html)
        self.assertNotIn("稳定", html)
        self.assertNotIn("平均", html)
        self.assertNotIn("必填证据", html)
        self.assertNotIn("1—10号气缸盖螺栓", html)
        self.assertNotIn("25 N·m", html)
        self.assertNotIn("下一项", html)
        self.assertNotIn("已完成展示", html)

    def test_evaluation_text_is_disclosed_only_after_completion(self) -> None:
        payload = template_payload()
        item = next(item for item in payload["items"] if item["item_number"] == 20)
        item["live_binding"]["state"] = "已完成评分"
        item["score"] = 1
        html = render_html(payload)
        self.assertIn("1—10号气缸盖螺栓", html)
        self.assertIn("评分结论", html)

    def test_public_projection_drops_rubric_snapshot(self) -> None:
        projection = public_projection(template_payload())
        self.assertNotIn("source", projection)
        self.assertEqual(projection["score_summary"], {"current_score": 0, "max_score": 13, "resolved_item_count": 0, "manual_review_count": 0})
        self.assertTrue(all("prefilled_score" not in item for item in projection["items"]))
        self.assertTrue(all("realtime_target" not in item for item in projection["items"]))
        self.assertTrue(all("difficulty_label" not in item for item in projection["items"]))
        self.assertTrue(all("analysis_tools" not in item for item in projection["items"]))
        self.assertTrue(all("score" not in item for item in projection["items"]))

    def test_terminal_projection_reveals_score_difficulty_and_tools(self) -> None:
        payload = template_payload()
        item = payload["items"][0]
        item["live_binding"]["state"] = "已完成评分"
        item["score"] = 1
        projection = public_projection(payload)
        public_item = projection["items"][0]
        self.assertEqual(public_item["difficulty_label"], "困难")
        self.assertTrue(public_item["analysis_tools"])
        self.assertEqual(public_item["score"], 1)
        self.assertIn("actual_analysis_task_count", public_item["analysis_profile"])
        self.assertNotIn("average_tool_task_count", public_item["analysis_profile"])
        self.assertNotIn("required_slot_count", public_item["analysis_profile"])
        self.assertEqual(projection["score_summary"]["current_score"], 1)

    def test_manual_review_scores_zero_without_evaluation_text(self) -> None:
        payload = template_payload()
        item = payload["items"][0]
        item["live_binding"]["state"] = "待人工确认"
        item["score"] = 0
        projection = public_projection(payload)
        public_item = projection["items"][0]
        self.assertEqual(public_item["score"], 0)
        self.assertEqual(public_item["difficulty_label"], "困难")
        self.assertNotIn("realtime_target", public_item)
        self.assertEqual(projection["score_summary"]["manual_review_count"], 1)

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
        self.assertTrue(all(
            event["item_patch"].get("score") == ({"已完成评分": 1, "待人工确认": 0}.get(event["item_patch"]["live_binding"]["state"]))
            for event in payload["events"]
        ))

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

    def test_update_normalizes_score_from_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            report_path.write_text(json.dumps(template_payload(), ensure_ascii=False), encoding="utf-8")
            state = DemoState(root, report_path)

            state.update({
                "item_id": "item_5069",
                "live_binding": {"state": "待人工确认"},
                "score": 1,
            })
            review = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(review["items"][0]["score"], 0)

            state.update({
                "item_id": "item_5069",
                "live_binding": {"state": "已完成评分"},
                "score": 0,
            })
            completed = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(completed["items"][0]["score"], 1)

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
