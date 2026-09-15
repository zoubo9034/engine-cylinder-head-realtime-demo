from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping


VERSION_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = VERSION_ROOT.parent
ASSET_ROOT = VERSION_ROOT / "assets"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from render_report import FORBIDDEN_HTML_MARKERS, public_projection  # noqa: E402


HTML_SHELL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0056D2">
<title>发动机气缸盖拆装智能实训分析</title>
<style>
@import url("https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&family=Source+Sans+3:wght@400;500;600;700&display=swap");
__STYLES__
</style>
</head>
<body>
<div class="canvas-host" id="canvas-host">
<div class="page-canvas" id="page-canvas">
<section class="course-banner" aria-label="课程信息">
  <div class="banner-art" aria-hidden="true">
    <i class="banner-ring"></i>
    <i class="banner-disc"></i>
    <i class="banner-slash"></i>
  </div>
  <div class="banner-inner">
    <div class="banner-top">
      <img class="banner-logo" src="logo.png" alt="上海人工智能实验室 华东师范大学">
      <div class="crumbs">智能实训 <span>/</span> 发动机拆装 <span>/</span> 实时评测</div>
      <div class="live-pill"><i class="pulse"></i><span id="connection">等待视频接入</span></div>
    </div>
    <div class="banner-body">
      <h1 id="title">发动机气缸盖拆装智能实训分析</h1>
      <p class="banner-lead">从视频流识别操作过程，按项目 8—20 整理证据并完成评分。低置信度结果会保留待人工确认。</p>
      <div class="banner-meta">
        <span>13 项操作</span>
        <span>项目 8 — 20</span>
        <span id="bannerProgress">完成度 0 / 13</span>
      </div>
    </div>
  </div>
</section>

<div class="shell">
  <section class="metrics">
    <div class="metric"><div class="metric-label">当前分析阶段</div><div class="metric-value" id="phase">正接入视频流</div></div>
    <div class="metric"><div class="metric-label">已处理项目</div><div class="metric-value" id="done">0/13</div></div>
    <div class="metric"><div class="metric-label">实时总分</div><div class="metric-value" id="score">0/13</div></div>
    <div class="metric"><div class="metric-label">证据绑定</div><div class="metric-value" id="bound">0</div></div>
    <div class="metric"><div class="metric-label">系统提示</div><div class="metric-value" id="quality">等待有效画面</div></div>
  </section>
  <div class="live-layout awaiting-start">
    <div class="course-column">
      <aside class="timeline">
        <div class="timeline-head">
          <div class="timeline-title">课程内容</div>
          <button class="timeline-toggle" id="timelineToggle" type="button" aria-expanded="true" aria-controls="timeline" title="收起课程内容" aria-label="收起课程内容">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M10.2 2.7 5.9 8l4.3 5.3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>
        <div class="timeline-list" id="timeline"></div>
      </aside>
    </div>
    <section class="stage-column">
      <section class="stage-panel video-panel">
        <div class="panel-head">实时视频</div>
        <div class="video-slot" id="video-slot" aria-label="实时视频窗口">
          <video id="live-video" data-video-slot="realtime" playsinline muted aria-label="实时视频画面"></video>
          <div class="video-placeholder">
            <div class="video-placeholder-inner">
              <div class="video-placeholder-mark">LIVE</div>
              <div class="video-placeholder-title">等待接入</div>
              <div class="video-placeholder-note">评测启动后在此显示实时画面</div>
            </div>
          </div>
        </div>
        <div class="live-controls">
          <button class="control primary" id="start" type="button">启动评测</button>
          <button class="control" id="reset" type="button">重置</button>
        </div>
      </section>
      <section class="workflow-strip" id="workflow-strip" aria-label="模型工作状态">
        <div class="workflow-heading"><i></i><span>模型工作状态</span></div>
        <div class="workflow-track" id="workflow-track" role="list">
          <div class="workflow-stage pending" data-workflow-stage="ingest" role="listitem"><i class="workflow-light"></i><span>接入画面</span></div>
          <div class="workflow-stage pending" data-workflow-stage="planning" role="listitem"><i class="workflow-light"></i><span>任务规划</span></div>
          <div class="workflow-stage pending" data-workflow-stage="orchestration" role="listitem"><i class="workflow-light"></i><span>工具编排</span></div>
          <div class="workflow-stage pending" data-workflow-stage="visual_analysis" role="listitem"><i class="workflow-light"></i><span>视觉分析</span></div>
          <div class="workflow-stage pending" data-workflow-stage="evidence" role="listitem"><i class="workflow-light"></i><span>证据整理</span></div>
          <div class="workflow-stage pending" data-workflow-stage="decision" role="listitem"><i class="workflow-light"></i><span>结果判定</span></div>
        </div>
        <span class="workflow-state" id="workflow-state" role="status" aria-live="polite">等待分析</span>
      </section>
      <div class="toolbar stage-toolbar">
        <div class="filters" id="difficultyFilters">
          <button class="chip" data-filter="all" type="button">全部</button>
          <button class="chip" data-filter="difficult" type="button">困难</button>
          <button class="chip" data-filter="medium" type="button">中等</button>
          <button class="chip" data-filter="easy" type="button">简单</button>
        </div>
      </div>
      <section class="hero" aria-label="实时识别卡片">
        <div class="panel-head">实时识别卡片</div>
        <div class="hero-copy">
          <div class="hero-kicker" id="heroKicker">实时识别中 · 当前项目</div>
          <h2 id="heroTitle">等待当前操作</h2>
          <p id="heroText">系统正在从视频流中定位操作对象，证据生成后会自动整理到对应项目。</p>
        </div>
        <div class="hero-side">
          <div class="stage-label">实时完成度</div>
          <div class="stage-value" id="heroProgress">0 / 13</div>
          <div class="progress"><i id="progressBar" style="width:0%"></i></div>
        </div>
      </section>
    </section>
    <section class="results-column">
      <button class="follow-chip" id="follow-chip" type="button" hidden>项目已完成 · 查看</button>
      <section class="cards" id="cards" tabindex="0" aria-label="评分项目列表"></section>
    </section>
  </div>
  <button class="course-fab" id="courseFab" type="button" hidden aria-label="展开课程内容" title="展开课程内容">
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M5.8 2.7 10.1 8 5.8 13.3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <span>课程</span>
  </button>
  <footer class="footer" id="footer">本页面展示当前视频流的实时分析过程。证据生成后显示对应核验信息，低置信度结果保留待人工确认。</footer>
</div>
</div>
</div>
<div class="toast" id="toast"></div>
<div class="drawer-backdrop" id="drawer-backdrop" hidden>
  <aside class="detail-drawer" id="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
    <div class="drawer-head">
      <div>
        <div class="drawer-kicker" id="drawer-kicker">详细核验</div>
        <h2 class="drawer-title" id="drawer-title">详细表单</h2>
        <div class="drawer-meta" id="drawer-meta"></div>
      </div>
      <button class="drawer-close" id="drawer-close" type="button" aria-label="关闭详细表单">×</button>
    </div>
    <div class="drawer-body" id="drawer-body"></div>
  </aside>
</div>
<div class="hover-preview" id="hover-preview" hidden>
  <div id="hover-media"></div>
  <div class="preview-meta"><span id="hover-phase">证据</span><span id="hover-confidence"></span></div>
</div>
<div class="lightbox" id="lightbox" hidden role="dialog" aria-modal="true" aria-label="证据查看器">
  <div class="lightbox-panel">
    <button class="lightbox-close" id="lightbox-close" type="button" aria-label="关闭证据查看器">×</button>
    <div id="lightbox-media" style="display:flex;flex:1;min-height:0"></div>
    <div class="lightbox-meta" id="lightbox-meta"></div>
  </div>
</div>
<script>
const DATA = __REPORT_DATA__;
__APP_SCRIPT__
</script>
</body>
</html>
"""


VIDEO_HTML_SHELL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#073b78">
<title>发动机气缸盖拆装智能实训分析</title>
<style>
@import url("https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&family=Source+Sans+3:wght@400;500;600;700&display=swap");
__STYLES__
</style>
</head>
<body class="mode-video">
<div class="arena-shell">
  <header class="arena-header">
    <div class="banner-art arena-header-art" aria-hidden="true">
      <i class="banner-ring"></i>
      <i class="banner-disc"></i>
      <i class="banner-slash"></i>
    </div>
    <div class="arena-brand">
      <img class="arena-logo" src="logo.png" alt="上海人工智能实验室 华东师范大学">
      <div class="arena-title"><span>WORLD SKILLS · LIVE JUDGING</span><h1 id="title">发动机气缸盖拆装智能实训分析</h1></div>
    </div>
    <div class="arena-status-grid" aria-label="实时评测状态">
      <div class="arena-status"><span>阶段</span><strong id="phase">正接入视频流</strong></div>
      <div class="arena-status"><span>进度</span><strong id="done">0/13</strong></div>
      <div class="arena-status"><span>总分</span><strong id="score">0/13</strong></div>
      <div class="arena-status"><span>证据</span><strong id="bound">0</strong></div>
      <div class="arena-status wide"><span>系统</span><strong id="quality">等待有效画面</strong></div>
    </div>
    <div class="arena-actions">
      <div class="arena-connection"><i class="pulse"></i><span id="connection">等待视频接入</span></div>
      <button class="control primary" id="start" type="button">启动评测</button>
      <button class="control" id="reset" type="button">重置</button>
    </div>
  </header>

  <main class="arena-main">
    <aside class="course-column arena-rail">
      <div class="arena-panel-title"><span>评分流程</span><small>项目 8—20</small></div>
      <div class="timeline-list" id="timeline"></div>
    </aside>

    <section class="stage-column arena-live">
      <div class="arena-panel-title"><span>实时操作画面</span><small>720 × 1080 · 2:3</small></div>
      <div class="video-slot" id="video-slot" aria-label="实时视频窗口">
        <video id="live-video" data-video-slot="realtime" playsinline muted aria-label="实时视频画面"></video>
        <div class="video-placeholder"><div class="video-placeholder-inner"><div class="video-placeholder-mark">LIVE</div><div class="video-placeholder-title">等待视频接入</div><div class="video-placeholder-note">竖屏实时操作画面</div></div></div>
      </div>
      <section class="workflow-strip" id="workflow-strip" aria-label="模型工作状态">
        <div class="workflow-heading"><i></i><span>模型状态</span></div>
        <div class="workflow-track" id="workflow-track" role="list">
          <div class="workflow-stage pending" data-workflow-stage="ingest" role="listitem"><i class="workflow-light"></i><span>接入</span></div>
          <div class="workflow-stage pending" data-workflow-stage="planning" role="listitem"><i class="workflow-light"></i><span>规划</span></div>
          <div class="workflow-stage pending" data-workflow-stage="orchestration" role="listitem"><i class="workflow-light"></i><span>编排</span></div>
          <div class="workflow-stage pending" data-workflow-stage="visual_analysis" role="listitem"><i class="workflow-light"></i><span>分析</span></div>
          <div class="workflow-stage pending" data-workflow-stage="evidence" role="listitem"><i class="workflow-light"></i><span>证据</span></div>
          <div class="workflow-stage pending" data-workflow-stage="decision" role="listitem"><i class="workflow-light"></i><span>判定</span></div>
        </div>
        <span class="workflow-state" id="workflow-state" role="status" aria-live="polite">等待分析</span>
      </section>
      <section class="hero" aria-label="当前评分项目">
        <div class="hero-copy"><div class="hero-kicker" id="heroKicker">实时识别中 · 当前项目</div><h2 id="heroTitle">等待当前操作</h2><p id="heroText">系统正在从视频流中定位操作对象。</p></div>
        <div class="hero-side"><div class="stage-label">实时完成度</div><div class="stage-value" id="heroProgress">0 / 13</div><div class="progress"><i id="progressBar" style="width:0%"></i></div></div>
      </section>
      <div class="toolbar stage-toolbar"><div class="filters" id="difficultyFilters"><button class="chip" data-filter="all" type="button">全部</button><button class="chip" data-filter="difficult" type="button">困难</button><button class="chip" data-filter="medium" type="button">中等</button><button class="chip" data-filter="easy" type="button">简单</button></div></div>
    </section>

    <section class="results-column arena-results">
      <div class="arena-panel-title"><span>评分结果</span><small>完整卡片流</small></div>
      <button class="follow-chip" id="follow-chip" type="button" hidden>项目已完成 · 查看</button>
      <section class="cards" id="cards" tabindex="0" aria-label="评分项目列表"></section>
    </section>
  </main>
  <footer class="footer" id="footer">本页面展示当前视频流的实时分析过程。</footer>
</div>
<div class="toast" id="toast"></div>
<div class="drawer-backdrop" id="drawer-backdrop" hidden><aside class="detail-drawer" id="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title"><div class="drawer-head"><div><div class="drawer-kicker" id="drawer-kicker">详细核验</div><h2 class="drawer-title" id="drawer-title">详细表单</h2><div class="drawer-meta" id="drawer-meta"></div></div><button class="drawer-close" id="drawer-close" type="button" aria-label="关闭详细表单">×</button></div><div class="drawer-body" id="drawer-body"></div></aside></div>
<div class="hover-preview" id="hover-preview" hidden><div id="hover-media"></div><div class="preview-meta"><span id="hover-phase">证据</span><span id="hover-confidence"></span></div></div>
<div class="lightbox" id="lightbox" hidden role="dialog" aria-modal="true" aria-label="证据查看器"><div class="lightbox-panel"><button class="lightbox-close" id="lightbox-close" type="button" aria-label="关闭证据查看器">×</button><div id="lightbox-media"></div><div class="lightbox-meta" id="lightbox-meta"></div></div></div>
<script>
const DATA = __REPORT_DATA__;
__APP_SCRIPT__
</script>
</body>
</html>
"""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _embedded_evidence_sources() -> dict[str, str]:
    candidates = [
        PROJECT_ROOT / "展示标准报告_8-20_mock.html",
        PROJECT_ROOT / "worldskills-redesign-v1" / "展示标准报告_8-20_mock_v2.html",
        PROJECT_ROOT / "worldskills-redesign-v3" / "展示标准报告_8-20_mock_v3.html",
    ]
    for legacy_html in candidates:
        sources = _collect_sources_from_html(legacy_html)
        if sources:
            return sources
    return {}


def _collect_sources_from_html(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    html = path.read_text(encoding="utf-8")
    start_marker = "const DATA = "
    start = html.find(start_marker)
    if start < 0:
        return {}
    start += len(start_marker)
    end = -1
    for marker in (";\nconst statuses", ";\nconst ICONS", ";\nlet viewedIndex"):
        end = html.find(marker, start)
        if end >= 0:
            break
    if end < 0:
        return {}
    try:
        embedded = json.loads(html[start:end])
    except json.JSONDecodeError:
        return {}

    sources: dict[str, str] = {}

    def collect(item: Mapping[str, Any]) -> None:
        binding = item.get("binding", {}) or {}
        if not isinstance(binding, Mapping):
            return
        for evidence in binding.get("evidence", []) or []:
            if not isinstance(evidence, Mapping):
                continue
            evidence_id = str(evidence.get("evidence_id") or "")
            src = str(evidence.get("src") or "")
            if evidence_id and src.startswith("data:image/"):
                sources[evidence_id] = src

    for item in embedded.get("items", []) or []:
        if isinstance(item, Mapping):
            collect(item)
    for event in embedded.get("events", []) or []:
        if isinstance(event, Mapping) and isinstance(event.get("item_patch"), Mapping):
            collect(event["item_patch"])
    return sources


def _restore_embedded_evidence(public: dict[str, Any]) -> None:
    sources = _embedded_evidence_sources()
    if not sources:
        return

    def restore(item: Mapping[str, Any]) -> None:
        binding = item.get("binding", {}) or {}
        if not isinstance(binding, Mapping):
            return
        for evidence in binding.get("evidence", []) or []:
            if not isinstance(evidence, dict) or evidence.get("src"):
                continue
            src = sources.get(str(evidence.get("evidence_id") or ""))
            if src:
                evidence["src"] = src

    for item in public.get("items", []) or []:
        if isinstance(item, Mapping):
            restore(item)
    for event in public.get("events", []) or []:
        if isinstance(event, Mapping) and isinstance(event.get("item_patch"), Mapping):
            restore(event["item_patch"])


def render_html(payload: Mapping[str, Any]) -> str:
    public = public_projection(payload)
    _restore_embedded_evidence(public)
    presentation = public.get("presentation")
    if isinstance(presentation, dict) and presentation.get("initial_state") == "正在接入视频流":
        presentation["initial_state"] = "正接入视频流"
    data = json.dumps(public, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    styles = (ASSET_ROOT / "styles.css").read_text(encoding="utf-8")
    script = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
    media_mode = str((presentation or {}).get("evidence_media_mode") or "image") if isinstance(presentation, Mapping) else "image"
    shell = VIDEO_HTML_SHELL if media_mode == "video" else HTML_SHELL
    output = (
        shell
        .replace("__STYLES__", styles)
        .replace("__REPORT_DATA__", data)
        .replace("__APP_SCRIPT__", script)
    )
    for marker in FORBIDDEN_HTML_MARKERS:
        if marker in output:
            raise ValueError(f"V4 HTML 包含禁止公开的标记: {marker}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Coursera 风格实时评测页面")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    input_path = args.input if args.input.is_absolute() else (Path.cwd() / args.input)
    output_path = args.output if args.output.is_absolute() else (Path.cwd() / args.output)
    payload = _read_json(input_path.resolve())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(payload), encoding="utf-8")
    print(f"已生成 V4 页面: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
