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
        cls.video_payload = json.loads(
            (PROJECT_ROOT / "展示标准报告_8-20_video.json").read_text(encoding="utf-8")
        )
        cls.video_html = renderer.render_html(cls.video_payload)
        cls.video_mock_html = renderer.render_html(cls.video_mock_payload)

    @staticmethod
    def _without_report_data(html: str) -> str:
        prefix, remainder = html.split("const DATA = ", 1)
        _, suffix = remainder.split(";\nconst statuses", 1)
        return prefix + "const DATA = <REPORT>;\nconst statuses" + suffix

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
        self.assertIn("--canvas-width: 1920px", self.standard_html)
        self.assertIn("--canvas-height: 1080px", self.standard_html)
        self.assertIn("--live-row: 768px", self.standard_html)
        self.assertIn("grid-template-columns: 220px 400px 1188px", self.standard_html)
        self.assertIn("grid-template-rows: var(--live-row)", self.standard_html)
        self.assertIn("align-items: stretch", self.standard_html)
        self.assertIn("DESIGN_CANVAS = {width:1920,height:1080}", self.standard_html)
        self.assertIn("function syncCanvasScale()", self.standard_html)
        self.assertIn('id="canvas-host"', self.standard_html)
        self.assertIn('id="page-canvas"', self.standard_html)
        self.assertNotIn("100dvh - 220px", self.standard_html)
        self.assertNotIn("@media (max-width:", self.standard_html)
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
        self.assertIn("activeAnalysisIndex", self.standard_html)
        self.assertIn("if(latestCompletedIndex()===null)current=update.index", self.standard_html)
        self.assertIn("current=index;render();focusLatestCompleted(index)", self.standard_html)
        self.assertIn("padding: 90px 14px", self.video_mock_html)

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
        self.assertIn('if(mockReplay){if(evidenceMediaMode==="video")', self.mock_html)
        self.assertIn("runMockEvents()}else if(!fileMode)", self.mock_html)
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
            '$("follow-chip").onclick=jumpToLatestCompleted',
            "lightbox-video",
            "window.realtimeVideoInput",
            "acceptWebRTCOffer",
            "addWebRTCIceCandidate",
            "startLocalWebRTCSource",
            "RTCPeerConnection",
            "video.srcObject=stream",
            "captureStream",
            "URL.createObjectURL",
            "URL.revokeObjectURL",
            "realtime-video-icecandidate",
            "data:video/webm;base64,",
            'new URL("../../api/reset",document.baseURI)',
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
        self.assertIn("../../mock-video-evidence/08-item_5069.mp4", self.video_mock_html)
        self.assertIn("if(mockReplay){window.location.reload();return}", self.video_mock_html)

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

    def test_video_uses_judging_console_not_course_shell(self) -> None:
        for html in (self.video_html, self.video_mock_html):
            for marker in (
                'class="mode-video"',
                'class="arena-shell"',
                'class="arena-header"',
                'class="banner-art arena-header-art"',
                'class="arena-logo"',
                'class="arena-status-grid"',
                'class="arena-main"',
                'class="course-column arena-rail"',
                'class="stage-column arena-live"',
                'class="results-column arena-results"',
                "WORLD SKILLS · LIVE JUDGING",
            ):
                self.assertIn(marker, html)
            for marker in (
                'class="course-banner"',
                'class="metrics"',
                'id="timelineToggle"',
                'id="courseFab"',
                'class="canvas-host"',
                'class="page-canvas"',
            ):
                self.assertNotIn(marker, html)
        self.assertIn('class="course-banner"', self.standard_html)
        self.assertIn('id="timelineToggle"', self.standard_html)

    def test_video_standard_and_event_pages_share_identical_ui_shell(self) -> None:
        self.assertEqual(self.video_payload["presentation"], self.video_mock_payload["presentation"])
        self.assertEqual(self.video_payload["scope"], self.video_mock_payload["scope"])
        self.assertEqual(self.video_payload["demo_policy"], self.video_mock_payload["demo_policy"])
        self.assertEqual(self.video_payload["demo_context"], self.video_mock_payload["demo_context"])
        self.assertEqual(self.video_payload["items"], self.video_mock_payload["items"])
        self.assertEqual(
            self._without_report_data(self.video_html),
            self._without_report_data(self.video_mock_html),
        )
        checked_standard = (VERSION_ROOT / "video" / "展示标准报告_8-20_video_v4.html").read_text(encoding="utf-8")
        checked_event = (VERSION_ROOT / "video" / "展示标准报告_8-20_mock_video_v4.html").read_text(encoding="utf-8")
        self.assertEqual(checked_standard, self.video_html)
        self.assertEqual(checked_event, self.video_mock_html)
        self.assertEqual(
            self._without_report_data(checked_standard),
            self._without_report_data(checked_event),
        )
        for marker in (
            'src="../logo.png"',
            'class="arena-status-grid"',
            'class="analysis-box analysis-dashboard"',
            "window.realtimeVideoInput",
            "startLocalWebRTCSource",
        ):
            self.assertIn(marker, self.video_html)
            self.assertIn(marker, self.video_mock_html)

    def test_video_score_cards_keep_intrinsic_height(self) -> None:
        for marker in (
            "flex-direction: column",
            "align-items: stretch",
            "flex: 0 0 auto",
            "height: auto",
            "grid-template-columns: 216px 456px 1040px",
            "grid-template-rows: 940px",
            "width: 408px",
            "height: 612px",
            "grid-template-columns: minmax(0,1fr) 286px",
            'class="card-summary"',
            'class="card-evidence"',
            "aspect-ratio: 9 / 16",
            "width: 250px",
            "height: 444px",
            "width: 290px",
            "height: 120px",
            "flex: 1 1 auto",
            "margin-top: 8px",
            'class="analysis-columns"',
            'class="analysis-steps"',
            'class="analysis-stat-grid"',
            'class="analysis-feature-list"',
        ):
            self.assertIn(marker, self.video_mock_html)
        cards_styles = self.video_mock_html.split(".cards {", 1)[1].split(".cards::-webkit-scrollbar", 1)[0]
        self.assertIn("display: flex", cards_styles)
        self.assertNotIn("grid-template-rows: minmax(0, 1fr)", cards_styles)
        for marker in (
            "100dvh - 220px",
            "max-height: 62vh",
            "@media (max-width:",
        ):
            self.assertNotIn(marker, self.video_mock_html)

    def test_video_backdrop_canvas_has_bounded_lifecycle(self) -> None:
        for marker in (
            'class="evidence-video-backdrop"',
            'class="evidence-video-shade"',
            "videoBackdropJobs",
            "drawVideoBackdrop",
            "startVideoBackdrop",
            "stopVideoBackdrop",
            "requestVideoFrameCallback",
            "cancelVideoFrameCallback",
            "setTimeout(()=>paint(performance.now()),100)",
            'canvas.dataset.state="fallback"',
            'background:\n    radial-gradient(circle at 50% 42%, #193d65 0%, transparent 58%)',
            'document.addEventListener("visibilitychange"',
        ):
            self.assertIn(marker, self.video_mock_html)

    def test_last_workflow_stage_has_visible_dwell_and_ui_hides_replay_origin(self) -> None:
        for html in (self.video_html, self.video_mock_html):
            workflow_source = html.split("function workflowStart(", 1)[1].split("function cls(", 1)[0]
            self.assertIn("reduced?80:wait", workflow_source)
            self.assertNotIn("reduced?80:0", workflow_source)
            self.assertNotIn("Mock 回放中", html)
            self.assertNotIn("视频 Mock 报告", html)

    def test_follow_chip_restores_visibility_before_focus(self) -> None:
        source = self.video_mock_html.split("function jumpToLatestCompleted(", 1)[1].split("function clearAutoFocusPause", 1)[0]
        for marker in ("showAllItems()", "current=index", "render()", "focusLatestCompleted(index)"):
            self.assertIn(marker, source)
        self.assertLess(source.index("showAllItems()"), source.index("focusLatestCompleted(index)"))


if __name__ == "__main__":
    unittest.main()
