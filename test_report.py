#!/usr/bin/env python3
"""Small deterministic checks for the isolated demo deliverables."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from detail_rules import DETAIL_CHECK_STATUSES, DETAIL_EVALUATION_STATES, DETAIL_RULES, DETAIL_RULES_BY_ITEM
from report_schema import DIFFICULTY_LABELS, ITEM_DEFINITIONS, template_payload, validate_report
from render_report import FORBIDDEN_HTML_MARKERS, _clean_text, public_projection, render_html
from build_mock_report import (
    MOCK_ANALYSIS_DURATION_MS,
    _item_process_frame_records,
    _session_image_candidates,
)
from serve_demo import (
    ANALYSIS_DURATION_MAX_MS,
    ANALYSIS_DURATION_MIN_MS,
    DemoState,
)
from workflow_tool_stats import _is_visual_tool


class ReportContractTest(unittest.TestCase):
    def test_thirteen_visual_detail_rules_are_unique_and_owned(self) -> None:
        self.assertEqual(len(DETAIL_RULES), 13)
        self.assertEqual(set(DETAIL_RULES_BY_ITEM), {item["item_id"] for item in ITEM_DEFINITIONS})
        criterion_ids: set[str] = set()
        for rule, definition in zip(DETAIL_RULES, ITEM_DEFINITIONS):
            self.assertEqual(rule["item_id"], definition["item_id"])
            slots = set(definition["required_slots"]) | set(definition["enhanced_slots"])
            criteria = rule["detail_form"]["criteria"]
            self.assertTrue(criteria)
            for criterion in criteria:
                self.assertTrue(set(criterion["evidence_slot_ids"]).issubset(slots))
                self.assertNotIn(criterion["criterion_id"], criterion_ids)
                criterion_ids.add(criterion["criterion_id"])

    def test_detail_template_is_locked_and_has_no_speech_policy(self) -> None:
        payload = template_payload()
        self.assertEqual(
            {item["detail_evaluation"]["state"] for item in payload["items"]},
            {"locked"},
        )
        self.assertTrue(all(item["detail_evaluation"]["checks"] == [] for item in payload["items"]))
        self.assertTrue(all("oral_policy" not in item for item in payload["items"]))
        self.assertNotIn("oral_gate", payload["demo_policy"])
        self.assertNotIn("oral_replacement", payload["demo_policy"])
        for item in payload["items"]:
            preset = item["prefilled_result"]
            self.assertEqual(item["prefilled_score"], 1)
            self.assertEqual(preset["score"], 1)
            self.assertEqual(preset["detail_evaluation"]["state"], "unlocked")
            self.assertTrue(preset["detail_evaluation"]["high_level_evaluation"])
            self.assertTrue(preset["detail_evaluation"]["checks"])
            self.assertTrue(all(check["status"] == "confirmed" for check in preset["detail_evaluation"]["checks"]))
            self.assertTrue(all(check["evidence_ids"] == [] for check in preset["detail_evaluation"]["checks"]))

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

    def test_visual_tool_filter_keeps_temporal_analyzer(self) -> None:
        # ``temporal`` contains the letters ``oral``; filtering by arbitrary
        # substrings would silently remove this visual tool from the analysis
        # chain along with speech-dependent tools.
        self.assertTrue(_is_visual_tool("temporal_sequence_analyzer"))
        self.assertTrue(_is_visual_tool("object_motion_inspector"))
        self.assertFalse(_is_visual_tool("oral_evidence_analyzer"))
        self.assertFalse(_is_visual_tool("video_mme_subtitle_analyzer"))

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

    def test_public_html_uses_duanyan_design_tokens(self) -> None:
        html = render_html(template_payload())
        for token in (
            "--paper:#fdfcf8",
            "--paper-warm:#f7f4ec",
            "--ink:#1a2332",
            "--rule:#e8e4d9",
            "--vermilion:#1661ab",
            "--gold:#a8864b",
            'font-family:"Cormorant Garamond"',
            'font-family:"JetBrains Mono"',
        ):
            self.assertIn(token, html)
        self.assertNotIn("--bg:#08111f", html)
        self.assertNotIn("linear-gradient(145deg,#4ce3ff", html)

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
        self.assertTrue(all("prefilled_result" not in item for item in projection["items"]))
        self.assertTrue(all("realtime_target" not in item for item in projection["items"]))
        self.assertTrue(all("difficulty_label" not in item for item in projection["items"]))
        self.assertTrue(all("analysis_tools" not in item for item in projection["items"]))
        self.assertTrue(all("score" not in item for item in projection["items"]))
        self.assertTrue(all(set(item["detail"]) >= {"state", "sections", "criterion_count", "button"} for item in projection["items"]))
        self.assertTrue(all("criteria" not in item["detail"] for item in projection["items"]))

    def test_public_text_keeps_visual_slash_phrases_but_hides_absolute_paths(self) -> None:
        phrase = "画面能辨认气缸盖下方结合面，并出现燃烧室/气门侧特征。"
        self.assertEqual(_clean_text(phrase, "回退文案"), phrase)
        self.assertEqual(_clean_text("/tmp/local-frame.jpg", "回退文案"), "回退文案")

    def test_terminal_detail_projection_reveals_checks_only_at_terminal(self) -> None:
        payload = template_payload()
        item = payload["items"][0]
        evidence = {
            "evidence_id": "ev-local",
            "item_id": item["item_id"],
            "kind": "representative_frame",
            "source_path": "/tmp/local-frame.jpg",
            "caption": "相关对象画面",
        }
        item["live_binding"]["state"] = "已完成评分"
        item["live_binding"]["evidence"] = [evidence]
        item["score"] = 1
        item["detail_evaluation"] = {
            "state": "unlocked",
            "updated_at": "00:10",
            "checks": [{
                "criterion_id": "wrench_bolt_identity",
                "status": "confirmed",
                "confidence": 0.91,
                "evidence_ids": ["ev-local"],
                "observation": "对象在画面中清晰可辨。",
                "reason": "",
            }],
            "unresolved_summary": "",
            "high_level_evaluation": "关键对象关系已完成核验。",
        }
        public_item = public_projection(payload)["items"][0]
        self.assertIn("criteria", public_item["detail"])
        self.assertIn("checks", public_item["detail"])
        self.assertEqual(public_item["detail"]["checks"][0]["evidence_ids"], ["ev-local"])
        self.assertIn("high_level_evaluation", public_item["detail"])

    def test_manual_detail_projection_keeps_unresolved_reason_without_success_copy(self) -> None:
        payload = template_payload()
        item = payload["items"][0]
        item["live_binding"]["state"] = "待人工确认"
        item["score"] = 0
        item["detail_evaluation"] = {
            "state": "unlocked",
            "updated_at": "00:10",
            "checks": [],
            "unresolved_summary": "仍有核验项缺少完整画面，建议人工复核后确认。",
            "high_level_evaluation": "不应在人工确认状态显示。",
        }
        public_item = public_projection(payload)["items"][0]
        self.assertEqual(public_item["score"], 0)
        self.assertIn("unresolved_summary", public_item["detail"])
        self.assertNotIn("high_level_evaluation", public_item["detail"])

    def test_manual_check_copy_is_neutralized_when_it_contains_success_wording(self) -> None:
        payload = template_payload()
        item = payload["items"][0]
        item["live_binding"]["state"] = "待人工确认"
        item["score"] = 0
        item["detail_evaluation"] = {
            "state": "unlocked",
            "updated_at": "00:10",
            "checks": [{
                "criterion_id": "wrench_bolt_identity",
                "status": "manual_review",
                "confidence": 0.4,
                "evidence_ids": [],
                "observation": "动作正确，但画面还需要确认。",
                "reason": "结果符合标准。",
            }],
            "unresolved_summary": "仍有核验项待确认。",
        }
        public_check = public_projection(payload)["items"][0]["detail"]["checks"][0]
        self.assertEqual(public_check["observation"], "当前画面仍需确认。")
        self.assertEqual(public_check["reason"], "仍有核验项待确认。")


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

    def test_detail_evidence_reference_cannot_cross_items(self) -> None:
        payload = template_payload()
        first, second = payload["items"][:2]
        first["live_binding"]["state"] = "待人工确认"
        first["score"] = 0
        first["live_binding"]["evidence"] = [{"evidence_id": "ev-first", "item_id": first["item_id"]}]
        second["live_binding"]["state"] = "待人工确认"
        second["score"] = 0
        second["live_binding"]["evidence"] = [{"evidence_id": "ev-second", "item_id": second["item_id"]}]
        second["detail_evaluation"] = {
            "state": "unlocked",
            "updated_at": None,
            "checks": [{
                "criterion_id": "two_hand_support",
                "status": "manual_review",
                "confidence": 0.2,
                "evidence_ids": ["ev-first"],
                "observation": "待确认。",
                "reason": "",
            }],
            "unresolved_summary": "需要补充相关画面后再确认。",
        }
        self.assertTrue(any("not owned by item" in error or "reused across items" in error for error in validate_report(payload)))

    def test_compact_event_cannot_reference_another_item_evidence(self) -> None:
        payload = template_payload()
        first, second = payload["items"][:2]
        first["live_binding"]["evidence"] = [{"evidence_id": "ev-first", "item_id": first["item_id"]}]
        second["live_binding"]["evidence"] = [{"evidence_id": "ev-second", "item_id": second["item_id"]}]
        payload["events"] = [{
            "event_id": "evt-compact",
            "item_id": second["item_id"],
            "final_state": "证据生成中",
            "evidence_ids": ["ev-first"],
        }]
        self.assertTrue(any("not owned by event item" in error for error in validate_report(payload)))

    def test_detail_event_may_reuse_same_item_snapshot_evidence(self) -> None:
        payload = template_payload()
        item = payload["items"][0]
        evidence = {"evidence_id": "ev-first", "item_id": item["item_id"]}
        item["live_binding"]["evidence"] = [evidence]
        item["live_binding"]["state"] = "待人工确认"
        item["score"] = 0
        item["detail_evaluation"] = {
            "state": "unlocked",
            "updated_at": None,
            "checks": [{
                "criterion_id": "wrench_bolt_identity",
                "status": "manual_review",
                "confidence": 0.4,
                "evidence_ids": ["ev-first"],
                "observation": "当前画面仍需确认。",
                "reason": "需要补充相关画面。",
            }],
            "unresolved_summary": "仍有核验项待确认。",
        }
        payload["events"] = [{
            "event_id": "evt-detail",
            "item_id": item["item_id"],
            "final_state": "待人工确认",
            "evidence_ids": ["ev-first"],
            "item_patch": {
                "item_id": item["item_id"],
                "live_binding": {"state": "待人工确认"},
                "detail_evaluation": item["detail_evaluation"],
            },
        }]
        self.assertEqual(validate_report(payload), [])

    def test_detail_status_and_special_fields_are_present(self) -> None:
        payload = template_payload()
        by_number = {item["item_number"]: item for item in payload["items"]}
        self.assertTrue(any("第二次轮次" in c["label"] for c in by_number[8]["detail_form"]["criteria"]))
        self.assertIn("front_back_labels", [slot["slot_id"] for slot in by_number[15]["enhanced_evidence_slots"]])
        self.assertIn("hole_pin_relation", [slot["slot_id"] for slot in by_number[18]["enhanced_evidence_slots"]])
        self.assertIn("sequence_order", [slot["slot_id"] for slot in by_number[20]["required_evidence_slots"]])
        self.assertIn("1—10号气缸盖螺栓", by_number[20]["prefilled_standard_text"])
        self.assertIn("25 N·m", by_number[20]["prefilled_standard_text"])

    def test_visual_detail_rules_retain_initial_process_specifics(self) -> None:
        by_number = {
            rule["item_number"]: rule["detail_form"]
            for rule in DETAIL_RULES
        }
        text = lambda number: " ".join(
            f"{criterion['label']} {criterion['basis']} {criterion['boundary']}"
            for criterion in by_number[number]["criteria"]
        )
        expected_fragments = {
            8: ("第二次轮次", "180°", "两次 90°", "开始", "动作中", "完成"),
            9: ("双手", "垫块", "工作台", "抬起", "下降", "落位"),
            10: ("旧气缸垫", "薄片边缘", "完全脱离", "待检区域"),
            11: ("孔位", "外缘", "内缘", "变形、缺失或破损", "正面", "反面"),
            12: ("两枚", "定位销 1", "定位销 2", "分别", "损伤"),
            13: ("燃烧室/气门侧", "白色无纺布", "密封区域", "气缸体", "活塞", "气缸孔"),
            14: ("气缸体", "白色无纺布", "气缸孔周边", "连续画面", "擦拭变化"),
            15: ("正面", "翻面", "反面", "安装之前", "接触画面"),
            16: ("定位销1→定位销2", "定位销 1", "定位销 2", "两枚"),
            17: ("视觉节点", "待用", "安装前", "三个阶段"),
            18: ("穿孔", "正反面", "螺栓孔", "外轮廓", "定位销被垫片遮挡", "对准", "落座"),
            19: ("新螺栓", "待安装区域", "流程节点", "插入或紧固", "三个阶段"),
            20: ("扭力扳手", "可见旋转", "第一次", "1→10", "25 N·m", "角度轮次", "手动补拧"),
        }
        forbidden = ("口述", "口头", "音频", "字幕", "替代", "仅凭", "可追溯", "同图", "倒推")
        for number, fragments in expected_fragments.items():
            value = text(number)
            for fragment in fragments:
                self.assertIn(fragment, value, f"item {number} lost {fragment}")
            self.assertFalse(any(marker in value for marker in forbidden), f"item {number} leaked internal wording")


    def test_mock_fixture_is_empty_until_event_patch(self) -> None:
        path = Path(__file__).with_name("展示标准报告_8-20_mock.json")
        if not path.exists():
            self.skipTest("mock fixture has not been generated")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(validate_report(payload), [])
        self.assertEqual(len(payload.get("events", [])), len(ITEM_DEFINITIONS))
        self.assertTrue(all(not item["live_binding"]["evidence"] for item in payload["items"]))
        self.assertTrue(all(event.get("item_patch") for event in payload["events"]))
        self.assertTrue(all(event["item_patch"]["live_binding"]["state"] == "已完成评分" for event in payload["events"]))
        self.assertTrue(all(event["processing_ms"] == MOCK_ANALYSIS_DURATION_MS for event in payload["events"]))
        self.assertTrue(all(
            event["item_patch"].get("score") == 1
            for event in payload["events"]
        ))
        self.assertTrue(all(
            all(check["status"] == "confirmed" for check in event["item_patch"]["detail_evaluation"]["checks"])
            for event in payload["events"]
        ))

    def test_mock_fixture_frames_stay_in_the_selected_item_process(self) -> None:
        """Checked-in mock evidence must come from one item's process output."""
        path = Path(__file__).with_name("展示标准报告_8-20_mock.json")
        if not path.exists():
            self.skipTest("mock fixture has not been generated")
        payload = json.loads(path.read_text(encoding="utf-8"))
        audit = payload.get("_mock_audit", {}).get("selected_items", {})
        self.assertEqual(set(audit), {item["item_id"] for item in ITEM_DEFINITIONS})
        for event in payload.get("events", []):
            item_id = str(event["item_id"])
            selected = audit[item_id]
            sample_id = str(selected["sample_id"])
            self.assertTrue(sample_id)
            self.assertEqual(event["processing_ms"], MOCK_ANALYSIS_DURATION_MS)
            for evidence in event["item_patch"]["live_binding"]["evidence"]:
                source_path = str(evidence.get("source_path") or "")
                if source_path.startswith("timestamp:"):
                    continue
                # The private audit fields are intentionally retained in the
                # JSON fixture, so this check can verify that no neighbouring
                # video's frame was spliced into the selected item.
                self.assertIn(sample_id, source_path)
                self.assertTrue(
                    evidence.get("analysis_task")
                    or evidence.get("analysis_status")
                    or "keyframes" in source_path.casefold()
                    or any(token in source_path.casefold() for token in ("mask", "bbox", "overlay", "crop"))
                )

    def test_adapter_segment_cache_keeps_all_item_tags(self) -> None:
        """A first item lookup must not erase later item's process frames."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_dir = root / "video-a"
            intermediate = session_dir / "intermediate"
            frames = session_dir / "keyframes" / "a1"
            intermediate.mkdir(parents=True)
            frames.mkdir(parents=True)
            for seconds in (1, 2, 9):
                (frames / f"keyframe_{seconds:03d}s.jpg").write_bytes(b"frame")
            (intermediate / "adapter_adapter_result.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "keyframe_map": {
                            "1": "keyframes/a1/keyframe_001s.jpg",
                            "2": "keyframes/a1/keyframe_002s.jpg",
                            "9": "keyframes/a1/keyframe_009s.jpg",
                        },
                        "merged_segments": [
                            {"start": 1, "end": 2, "tags": ["item_5069"]},
                            {"start": 9, "end": 9, "tags": ["clean_pins"]},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session = {"session_dir": session_dir, "sample_id": "video-a", "rows_by_item": {}}
            first = _item_process_frame_records("item_5069", session, [])
            second = _item_process_frame_records("clean_pins", session, [])
            self.assertEqual([entry["timestamp_sec"] for entry in first], [1.0, 2.0])
            self.assertEqual([entry["timestamp_sec"] for entry in second], [9.0])
            self.assertIn("clean_pins", session["item_process_frame_records"])

    def test_item_candidates_prefer_tagged_process_frames_over_row_neighbor(self) -> None:
        """Tagged segment frames win over an unrelated compact row frame."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_dir = root / "video-b"
            intermediate = session_dir / "intermediate"
            frames = session_dir / "keyframes" / "a1"
            intermediate.mkdir(parents=True)
            frames.mkdir(parents=True)
            for seconds in (10, 11, 12, 50):
                (frames / f"keyframe_{seconds:03d}s.jpg").write_bytes(b"frame")
            (intermediate / "adapter_adapter_result.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "keyframe_map": {
                            str(seconds): f"keyframes/a1/keyframe_{seconds:03d}s.jpg"
                            for seconds in (10, 11, 12, 50)
                        },
                        "merged_segments": [
                            {"start": 10, "end": 12, "tags": ["clean_gasket"]},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session = {
                "session_dir": session_dir,
                "sample_id": "video-b",
                "rows_by_item": {
                    "clean_gasket": [
                        (
                            "video-b",
                            {
                                "item": "clean_gasket",
                                "timestamp_sec": 50,
                                "status": "pass",
                                "keyframe_path": "keyframes/a1/keyframe_050s.jpg",
                            },
                            session_dir,
                        )
                    ]
                },
            }
            candidates = _session_image_candidates("clean_gasket", session)
            process_times = [candidate.get("timestamp_sec") for candidate in candidates]
            self.assertTrue(process_times)
            self.assertTrue(set(process_times).issubset({10.0, 11.0, 12.0}))
            self.assertTrue(all(candidate.get("analysis_task") for candidate in candidates))

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

    def test_complete_required_evidence_runs_bounded_analysis_before_completion(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self.value = 100.0

            def __call__(self) -> float:
                return self.value

        class FixedRandom:
            def randint(self, lower: int, upper: int) -> int:
                self.args = (lower, upper)
                return 12_000

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            report_path.write_text(json.dumps(template_payload(), ensure_ascii=False), encoding="utf-8")
            clock = FakeClock()
            random_source = FixedRandom()
            state = DemoState(root, report_path, clock=clock, rng=random_source)
            item = deepcopy(state.read()["items"][0])
            evidence = []
            for index, slot in enumerate(item["required_evidence_slots"]):
                slot_id = slot["slot_id"]
                record = {
                    "evidence_id": f"ev-live-{index}",
                    "item_id": item["item_id"],
                    "kind": "timestamp" if slot_id == "live_timestamp" else "representative_frame",
                    "source_path": f"/tmp/{item['item_id']}-{index}.jpg",
                    "confidence": 0.94,
                }
                slot["status"] = "bound"
                slot["evidence"] = [record]
                evidence.append(record)
            item["live_binding"].update({
                "state": "已定位",
                "live_timestamp": "00:10",
                "time_confidence": 0.94,
                "evidence": evidence,
            })
            analysing = state.update({"item_id": item["item_id"], "item_patch": item})
            live_item = analysing["items"][0]
            self.assertEqual(live_item["live_binding"]["state"], "证据生成中")
            self.assertIsNone(live_item["score"])
            self.assertEqual(live_item["detail_evaluation"]["state"], "analyzing")
            self.assertEqual(random_source.args, (ANALYSIS_DURATION_MIN_MS, ANALYSIS_DURATION_MAX_MS))
            self.assertEqual(state._analysis_jobs[item["item_id"]]["duration_ms"], 12_000)

            clock.value = 111.999
            self.assertEqual(state.read()["items"][0]["live_binding"]["state"], "证据生成中")
            clock.value = 112.0
            completed = state.read()["items"][0]
            self.assertEqual(completed["live_binding"]["state"], "已完成评分")
            self.assertEqual(completed["score"], 1)
            self.assertEqual(completed["detail_evaluation"]["state"], "unlocked")
            self.assertTrue(completed["detail_evaluation"]["checks"])
            self.assertTrue(all(check["status"] == "confirmed" for check in completed["detail_evaluation"]["checks"]))
            self.assertTrue(all(
                ref in {record["evidence_id"] for record in evidence}
                for check in completed["detail_evaluation"]["checks"]
                for ref in check["evidence_ids"]
            ))

    def test_update_accepts_detail_evaluation_without_changing_score_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            payload = template_payload()
            report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            state = DemoState(root, report_path)
            state.update({
                "item_id": "item_5069",
                "live_binding": {"state": "待人工确认"},
                "detail_evaluation": {
                    "state": "unlocked",
                    "updated_at": None,
                    "checks": [],
                    "unresolved_summary": "需要补充相关画面后再确认。",
                },
                "score": 1,
            })
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["items"][0]["detail_evaluation"]["state"], "unlocked")
            self.assertEqual(saved["items"][0]["score"], 0)

    def test_reset_clears_detail_results_and_keeps_only_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "mock.json"
            payload = template_payload()
            payload["demo_mode"] = "mock_live_stream"
            payload["_mock_audit"] = {"source_run": "/private/source"}
            item = payload["items"][0]
            item["live_binding"]["state"] = "待人工确认"
            item["score"] = 0
            item["detail_evaluation"] = {"state": "unlocked", "updated_at": "00:01", "checks": [], "unresolved_summary": "待确认"}
            payload["events"] = [{"event_id": "evt", "item_id": item["item_id"], "item_patch": deepcopy(item)}]
            report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            reset = DemoState(root, report_path).reset()
            self.assertEqual(reset["items"][0]["detail_evaluation"]["state"], "locked")
            self.assertEqual(reset["items"][0]["detail_evaluation"]["checks"], [])
            self.assertEqual(len(reset["events"]), 1)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertNotIn("_mock_audit", saved)

    def test_mock_events_cover_detail_lifecycle_and_visual_fields(self) -> None:
        path = Path(__file__).with_name("展示标准报告_8-20_mock.json")
        if not path.exists():
            self.skipTest("mock fixture has not been generated")
        payload = json.loads(path.read_text(encoding="utf-8"))
        event_states = {event["item_patch"]["live_binding"]["state"] for event in payload["events"]}
        self.assertEqual(event_states, {"已完成评分"})
        patches = {event["item_id"]: event["item_patch"] for event in payload["events"]}
        self.assertIn("第二次预松", {entry.get("round") for entry in patches["item_5069"]["live_binding"]["evidence"]})
        self.assertTrue(any(entry.get("phase") == "动作中" for entry in patches["clean_gasket"]["live_binding"]["evidence"]))
        self.assertTrue(any(slot["slot_id"] == "hole_pin_relation" for slot in patches["install_gasket"]["enhanced_evidence_slots"]))
        self.assertIn("sequence_order", [slot["slot_id"] for slot in patches["install_1st"]["required_evidence_slots"]])

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

    def test_html_contains_detail_drawer_and_image_viewer_interactions(self) -> None:
        html = render_html(json.loads(Path("展示标准报告_8-20_mock.json").read_text(encoding="utf-8")))
        for marker in ("detail-drawer", "drawer-backdrop", "hover-preview", "lightbox", "展开详细表单", "aria-controls", "aria-expanded", "object-fit:contain", "prefers-reduced-motion"):
            self.assertIn(marker, html)
        for marker in ("口述", "音频", "字幕", "演示模式", "可追溯", "同图", "仅凭", "倒推", "内部", "/mnt/shared-storage-user/", "source_path"):
            self.assertNotIn(marker, html)


if __name__ == "__main__":
    unittest.main()
