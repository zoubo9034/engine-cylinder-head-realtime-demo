from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


VERSION_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = VERSION_ROOT.parent
RENDERER_PATH = VERSION_ROOT / "render_report_v4.py"

spec = importlib.util.spec_from_file_location("render_report_v4", RENDERER_PATH)
assert spec and spec.loader
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class RedesignV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.standard_payload = json.loads(
            (PROJECT_ROOT / "展示标准报告_8-20.json").read_text(encoding="utf-8")
        )
        cls.mock_payload = json.loads(
            (PROJECT_ROOT / "展示标准报告_8-20_mock.json").read_text(encoding="utf-8")
        )
        cls.standard_html = renderer.render_html(cls.standard_payload)
        cls.mock_html = renderer.render_html(cls.mock_payload)
        cls.video_mock_payload = json.loads(
            (PROJECT_ROOT / "展示标准报告_8-20_mock_video.json").read_text(encoding="utf-8")
        )
        cls.video_mock_html = renderer.render_html(cls.video_mock_payload)

    def test_coursera_banner_and_original_layers(self) -> None:
        for marker in (
            "course-banner",
            "发动机气缸盖拆装智能实训分析",
            "timeline",
            "heroTitle",
            "cards",
            "detail-drawer",
            "lightbox",
            "--blue: #0056d2",
            "banner-art",
            "--grad-blue",
            "backdrop-filter",
            "id=\"connection\"",
        ):
            self.assertIn(marker, self.standard_html)
        self.assertNotIn("nav-mark", self.standard_html)
        self.assertNotIn("site-nav", self.standard_html)
        self.assertNotIn("nav-brand", self.standard_html)
        self.assertNotIn("margin-bottom: -64px", self.standard_html)
        self.assertNotIn("margin: -56px", self.standard_html)
        self.assertNotIn("8 → 20", self.standard_html)
        self.assertIn("timelineToggle", self.standard_html)
        self.assertIn("courseFab", self.standard_html)
        self.assertIn("正接入视频流", self.standard_html)
        self.assertNotIn("正在接入视频流", self.standard_html)

    def test_live_layout_surfaces(self) -> None:
        for marker in (
            "live-layout",
            "course-column",
            "stage-column",
            "results-column",
            "live-controls",
            "实时视频",
            "实时识别卡片",
            'id="live-video"',
            'data-video-slot="realtime"',
            'id="workflow-strip"',
            "接入画面",
            "任务规划",
            "工具编排",
            "视觉分析",
            "证据整理",
            "结果判定",
            "等待识别",
            "wait-orb",
        ):
            self.assertIn(marker, self.standard_html)
        self.assertNotIn('class="workspace"', self.standard_html)
        self.assertNotIn("banner-actions", self.standard_html)
        self.assertNotIn('class="video-column"', self.standard_html)
        self.assertEqual(self.standard_html.count('id="start"'), 1)
        self.assertEqual(self.standard_html.count('id="reset"'), 1)
        self.assertLess(
            self.standard_html.index('class="course-column"'),
            self.standard_html.index('class="stage-column"'),
        )
        self.assertLess(
            self.standard_html.index('class="stage-column"'),
            self.standard_html.index('class="results-column"'),
        )
        self.assertIn('class="live-layout awaiting-start"', self.standard_html)
        self.assertIn('classList.remove("awaiting-start")', self.standard_html)
        self.assertIn("--live-row:", self.standard_html)
        self.assertIn(
            "--live-row: clamp(780px, calc(100dvh - 220px), 960px)",
            self.standard_html,
        )
        self.assertIn("grid-template-rows: var(--live-row)", self.standard_html)
        self.assertIn("align-items: stretch", self.standard_html)
        self.assertIn("function beginLiveSession(", self.standard_html)
        self.assertNotIn("请通过本地演示服务启动评测", self.standard_html)
        start_fn = self.standard_html.split("async function startEvaluation()")[1].split("async function resetReport()")[0]
        self.assertLess(
            start_fn.find("beginLiveSession()"),
            start_fn.find("fileMode"),
        )
        self.assertLess(
            self.standard_html.index('class="toolbar stage-toolbar"'),
            self.standard_html.index('aria-label="实时识别卡片"'),
        )
        self.assertGreater(
            self.standard_html.index('class="toolbar stage-toolbar"'),
            self.standard_html.index('id="workflow-strip"'),
        )

    def test_pending_items_are_filtered_and_focus_follows(self) -> None:
        self.assertIn('x.item.status!=="待开始"', self.standard_html)
        self.assertIn("function workflowStart(", self.standard_html)
        self.assertIn("function focusLatestCompleted(", self.standard_html)
        self.assertIn("pauseAutoFocus", self.standard_html)
        self.assertIn("followLatest", self.standard_html)
        self.assertIn("follow-chip", self.standard_html)
        self.assertIn("jumpToLatestCompleted", self.standard_html)

    def test_original_interaction_contract(self) -> None:
        for marker in (
            "function renderTimeline()",
            "function cardMarkup(",
            "展开详细表单",
            "启动评测",
            "runMockEvents",
            "current=update.index",
        ):
            self.assertIn(marker, self.standard_html)

    def test_difficulty_stays_terminal_only(self) -> None:
        self.assertIn('id="difficultyFilters"', self.standard_html)
        self.assertNotIn('id="difficultyFilters" hidden', self.standard_html)
        self.assertNotIn('class="chip active"', self.standard_html)
        self.assertIn("filters.classList.toggle(\"ready\"", self.standard_html)
        self.assertIn("terminal?`<span class=\"difficulty", self.standard_html)
        self.assertIn("wait-orb", self.standard_html)
        self.assertIn("等待识别", self.standard_html)

    def test_mock_page_keeps_replay_events(self) -> None:
        self.assertIn('"events":[{', self.mock_html)
        self.assertIn("runMockEvents", self.mock_html)

    def test_mock_replay_does_not_mix_polling_or_regress_item_state(self) -> None:
        self.assertIn(
            "const mockReplay = Array.isArray(DATA.events)&&DATA.events.length>0",
            self.mock_html,
        )
        self.assertIn("if(mockReplay){runMockEvents()}else if(!fileMode)", self.mock_html)
        self.assertIn("function stateRank(status)", self.mock_html)
        self.assertIn(
            "stateRank(incomingState)<stateRank(currentItem.status)",
            self.mock_html,
        )

    def test_outputs_remain_sanitized(self) -> None:
        from render_report import FORBIDDEN_HTML_MARKERS

        for html in (self.standard_html, self.mock_html, self.video_mock_html):
            for marker in FORBIDDEN_HTML_MARKERS:
                self.assertNotIn(marker, html)

    def test_video_mode_uses_one_coordinated_player_path(self) -> None:
        for marker in (
            'const evidenceMediaMode = DATA.presentation&&DATA.presentation.evidence_media_mode==="video"',
            'data-evidence-video="true"',
            "videoEvidenceMarkup",
            "pauseEvidencePlayers",
            "syncEvidencePlayers",
            "videoServiceReady",
            "pendingAutoplayIndex",
            "videoPlaybackPositions",
            "rememberEvidencePosition",
            "restoreEvidencePosition",
            "finishCardFocus",
            "renderSignature",
            "isNativeControlClick",
            "userPausedEvidence",
            "programmaticScrollUntil",
            "settleCardFocusFromScroll",
            "lightbox-video",
        ):
            self.assertIn(marker, self.video_mock_html)
        present_source = self.video_mock_html.split("async function present(update)", 1)[1].split("async function drainQueue", 1)[0]
        self.assertNotIn("if(newlyCompleted)pendingAutoplayIndex=update.index", present_source)
        focus_source = self.video_mock_html.split("function finishCardFocus(", 1)[1].split("function focusLatestCompleted", 1)[0]
        self.assertLess(focus_source.index("delta<=8"), focus_source.index("pendingAutoplayIndex=index"))
        manual_source = self.video_mock_html.split("function pauseAutoFocus(", 1)[1].split("function requestLatestFocus", 1)[0]
        self.assertIn("followLatest=false", manual_source)
        scroll_source = self.video_mock_html.split("function settleCardFocusFromScroll(", 1)[1].split("function bindCardScrollInteractions", 1)[0]
        self.assertLess(scroll_source.index("scrollTop>=maxScroll-24"), scroll_source.index('evidenceMediaMode!=="video"'))
        self.assertNotIn("queuedFocusIndex", self.video_mock_html)
        self.assertNotIn("autoFocusPausedUntil", self.video_mock_html)
        self.assertIn("/mock-video-evidence/08-item_5069.mp4", self.video_mock_html)

    def test_video_cards_and_dialogs_use_worldskills_components(self) -> None:
        for marker in (
            'class="binding-meta"',
            'class="evidence-expand evidence-ref"',
            'class="follow-chip-icon"',
            'class="follow-chip-copy"',
            'class="follow-chip-action"',
            "展开详细表单",
            ".item-card::before",
            ".drawer-backdrop { z-index: 100; }",
            ".lightbox { z-index: 120; }",
            "height: 100dvh",
            "#lightbox-media",
        ):
            self.assertIn(marker, self.video_mock_html)
        styles = (VERSION_ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn(".evidence-video-wrap {\n.follow-chip", styles)
        self.assertEqual(styles.count("\n.evidence-video-wrap {"), 1)


if __name__ == "__main__":
    unittest.main()
