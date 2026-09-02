#!/usr/bin/env python3
"""Render a live-only, self-contained HTML report from a JSON payload.

The renderer creates a public projection rather than embedding the input JSON
verbatim.  Provenance, raw artifact paths and any internal audit fields are
therefore not visible in the generated page.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Mapping

from report_schema import load_tool_profile, template_payload, validated_copy


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入 JSON 顶层必须是对象")
    return value


def _data_uri(path_text: str) -> str:
    if not path_text or path_text.startswith("timestamp://"):
        return ""
    path = Path(path_text).expanduser()
    if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _public_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    # Only fields needed for display are retained.  In particular, no source
    # path, sample name, run id or scorer output reaches the HTML.
    return {
        "evidence_id": str(evidence.get("evidence_id") or "evidence"),
        "kind": str(evidence.get("kind") or "image"),
        "phase": evidence.get("phase"),
        "timestamp": evidence.get("timestamp"),
        "timestamp_sec": evidence.get("timestamp_sec"),
        "confidence": evidence.get("confidence"),
        "caption": str(evidence.get("caption") or "当前视频流中的相关证据。"),
        "src": _data_uri(str(evidence.get("source_path") or "")),
    }


def _score_for_state(state: str) -> int | None:
    return {"证据已绑定": 1, "已完成评分": 1, "待人工确认": 0}.get(state)


def _is_terminal(state: str) -> bool:
    return state in {"证据已绑定", "已完成评分", "待人工确认"}


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return one item without provenance, scorer output or raw paths."""
    binding = item.get("live_binding", {}) or {}
    binding_state = str(binding.get("state") or "待开始")
    required_slots = [
        {
            "slot_id": slot.get("slot_id"),
            "label": slot.get("label"),
            "required": bool(slot.get("required", True)),
            "bound": str(slot.get("status") or "") == "bound",
        }
        for slot in item.get("required_evidence_slots", []) or []
    ]
    public_item = {
        "item_number": item.get("item_number"),
        "item_id": item.get("item_id"),
        "display_name": item.get("display_name"),
        "required_slots": required_slots,
        "binding": {
            "state": binding_state,
            "revision": binding.get("revision", 0),
            "changed_slot_ids": list(binding.get("changed_slot_ids") or []),
            "live_timestamp": binding.get("live_timestamp"),
            "live_start_sec": binding.get("live_start_sec"),
            "live_end_sec": binding.get("live_end_sec"),
            "time_confidence": binding.get("time_confidence"),
            "evidence_explanation": binding.get("evidence_explanation"),
            "evidence": [_public_evidence(e) for e in binding.get("evidence", []) or []],
        },
    }
    # Difficulty, tool-chain details and scores are post-analysis disclosures.
    # They are omitted from the initial projection so a live viewer sees only
    # the evidence currently being generated.
    if _is_terminal(binding_state):
        public_item["difficulty"] = item.get("difficulty")
        public_item["difficulty_label"] = item.get("difficulty_label")
        public_item["analysis_tools"] = [
            {
                "tool_id": str(tool.get("tool_id") or ""),
                "label": str(tool.get("label") or tool.get("tool_id") or "分析工具"),
            }
            for tool in item.get("analysis_tools", []) or []
            if isinstance(tool, Mapping)
        ]
        profile = item.get("analysis_profile", {}) or {}
        public_item["analysis_profile"] = {
            "distinct_tool_count": profile.get("distinct_tool_count", len(public_item["analysis_tools"])),
            "average_tool_plan_count": profile.get("average_tool_plan_count"),
            "average_tool_task_count": profile.get("average_tool_task_count"),
            "required_slot_count": profile.get("required_slot_count"),
            "enhanced_slot_count": profile.get("enhanced_slot_count"),
            "complexity_features": list(profile.get("complexity_features") or []),
        }
        public_item["score"] = _score_for_state(binding_state)
        public_item["score_max"] = 1
    # A rubric conclusion is a post-analysis disclosure.  Keeping it out of
    # the initial public projection prevents a prefilled criterion from being
    # visible before this item reaches the completed state.  Manual review is
    # deliberately excluded because it is not a completed standard conclusion.
    if binding_state in {"证据已绑定", "已完成评分"}:
        public_item["realtime_target"] = item.get("realtime_target")
        public_item["evidence_hint"] = item.get("evidence_hint")
    return public_item


def public_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strip non-display fields and inline only selected visual evidence."""
    validated_copy(payload)
    presentation = payload.get("presentation", {}) or {}
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        items.append(_public_item(item))
    events = []
    for event in payload.get("events", []) or []:
        public_event = {
            "event_id": event.get("event_id"),
            "item_id": event.get("item_id"),
            "delay_ms": int(event.get("delay_ms") or 900),
            "processing_ms": int(event.get("processing_ms") or 1200),
            "final_state": event.get("final_state", "证据生成中"),
            "evidence_ids": list(event.get("evidence_ids") or []),
        }
        if isinstance(event.get("item_patch"), Mapping):
            public_event["item_patch"] = _public_item(event["item_patch"])
        events.append(public_event)
    resolved = 0
    manual_review = 0
    current_score = 0
    for item in payload.get("items", []) or []:
        state = str((item.get("live_binding", {}) or {}).get("state") or "待开始")
        score = _score_for_state(state)
        if score is not None:
            resolved += 1
            current_score += score
            manual_review += int(state == "待人工确认")
    return {
        "title": str(payload.get("title") or "实时智能实训分析"),
        "presentation": {
            "audience_mode": "live_only",
            "initial_state": str(presentation.get("initial_state") or "正在接入视频流"),
            "poll_interval_ms": int(presentation.get("poll_interval_ms") or 650),
            "footer": str(presentation.get("footer") or "本页面展示当前视频流的实时分析过程。"),
        },
        "scope": {"active_item_count": len(items), "max_score": len(items)},
        "score_summary": {
            "current_score": current_score,
            "max_score": len(items),
            "resolved_item_count": resolved,
            "manual_review_count": manual_review,
        },
        "items": items,
        "events": events,
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>实时智能实训分析</title>
<style>
:root{--bg:#08111f;--panel:#101d30;--panel2:#14263d;--line:#26415d;--text:#e9f2ff;--muted:#8da5c0;--cyan:#46d9ff;--green:#62e6a8;--amber:#ffc66d;--red:#ff8098;--violet:#9b8cff;--shadow:0 22px 60px rgba(0,0,0,.32)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 75% -20%,#183d59 0,#08111f 46%);color:var(--text);font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh}button{font:inherit;color:inherit} .shell{max-width:1580px;margin:0 auto;padding:22px 26px 36px}
.topbar{display:flex;gap:18px;align-items:center;justify-content:space-between;margin-bottom:20px}.brand{display:flex;align-items:center;gap:14px}.brand-mark{width:42px;height:42px;border-radius:13px;display:grid;place-items:center;background:linear-gradient(145deg,#4ce3ff,#6579ff);box-shadow:0 8px 25px #329bd655;font-weight:800}.eyebrow{color:var(--cyan);font-size:12px;letter-spacing:.16em;text-transform:uppercase}.brand h1{font-size:24px;letter-spacing:.02em;margin:2px 0 0}.live-pill{display:flex;align-items:center;gap:9px;border:1px solid #3bcf9d77;background:#0d302b;border-radius:999px;padding:9px 14px;color:#a7f8d5;font-size:13px}.pulse{width:9px;height:9px;border-radius:50%;background:var(--green);box-shadow:0 0 0 0 #62e6a899;animation:pulse 1.7s infinite}@keyframes pulse{70%{box-shadow:0 0 0 9px transparent}}
.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px}.metric{background:linear-gradient(150deg,#12263b,#0d192b);border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:var(--shadow)}.metric-label{color:var(--muted);font-size:12px}.metric-value{font-size:20px;font-weight:750;margin-top:5px}.metric-value.cyan{color:var(--cyan)}.metric-value.green{color:var(--green)}.metric-value.amber{color:var(--amber)}
.workspace{display:grid;grid-template-columns:248px minmax(0,1fr);gap:18px}.timeline,.content-card{background:rgba(13,27,45,.86);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow)}.timeline{padding:17px 13px;align-self:start;position:sticky;top:15px;max-height:calc(100vh - 30px);overflow:auto}.timeline-title{display:flex;align-items:center;justify-content:space-between;margin:0 8px 14px;font-weight:700}.timeline-title span{font-size:12px;color:var(--muted);font-weight:500}.timeline-list{display:flex;flex-direction:column;gap:6px}.timeline-btn{display:grid;grid-template-columns:31px 1fr auto;gap:9px;align-items:center;width:100%;text-align:left;background:transparent;border:1px solid transparent;border-radius:12px;padding:8px;color:var(--muted);cursor:pointer}.timeline-btn:hover,.timeline-btn.active{background:#17304a;border-color:#2d6383;color:var(--text)}.timeline-no{width:27px;height:27px;display:grid;place-items:center;border-radius:9px;background:#17283d;color:#9eb4cb;font-size:12px;font-weight:700}.timeline-btn.active .timeline-no{background:var(--cyan);color:#062032}.timeline-name{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.timeline-state{font-size:10px;color:var(--muted)}.mini-dot{width:7px;height:7px;border-radius:50%;background:#46617a}.mini-dot.done{background:var(--green)}.mini-dot.busy{background:var(--cyan);animation:pulse 1.5s infinite}.mini-dot.review{background:var(--amber)}
.main{min-width:0}.hero{display:flex;justify-content:space-between;gap:22px;align-items:flex-end;padding:24px 25px;background:linear-gradient(135deg,#152c44,#112238 60%,#172d4b);border:1px solid #326286;border-radius:20px;box-shadow:var(--shadow);margin-bottom:15px}.hero-kicker{color:var(--cyan);font-size:12px}.hero h2{font-size:30px;margin:5px 0 8px}.hero p{margin:0;color:#b7c9dd;max-width:730px;line-height:1.7;font-size:14px}.hero-side{text-align:right;min-width:190px}.stage-label{color:var(--muted);font-size:12px}.stage-value{font-size:18px;font-weight:700;margin-top:6px}.progress{height:6px;background:#20354b;border-radius:5px;overflow:hidden;margin-top:14px}.progress i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--violet));border-radius:inherit;transition:width .35s ease}
.toolbar{display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;align-items:center;margin-bottom:14px}.filters,.controls{display:flex;gap:7px;flex-wrap:wrap}.difficulty-hint{color:var(--muted);font-size:12px;padding:8px 2px}.chip,.control{border:1px solid var(--line);background:#102237;border-radius:10px;padding:8px 11px;color:var(--muted);font-size:12px;cursor:pointer}.chip.active,.chip:hover,.control:hover{color:var(--text);border-color:#4b88a6;background:#183550}.control.primary{color:#041b26;background:var(--cyan);border-color:var(--cyan);font-weight:700}.control.primary:hover{background:#9aefff}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.item-card{background:linear-gradient(160deg,#112238,#0e1a2b);border:1px solid var(--line);border-radius:18px;padding:17px;min-width:0;transition:transform .2s,border-color .2s,box-shadow .2s}.item-card.current{border-color:#52d9ff;box-shadow:0 0 0 1px #52d9ff33,0 18px 45px #0005;transform:translateY(-2px)}.item-card.review{border-color:#725a36}.card-head{display:flex;gap:11px;align-items:flex-start;justify-content:space-between}.card-actions{display:flex;align-items:center;gap:7px;flex-wrap:wrap;justify-content:flex-end}.item-id{display:flex;align-items:center;gap:10px}.item-no{font-size:13px;font-weight:800;color:#061b2b;background:var(--cyan);padding:6px 8px;border-radius:8px}.item-title{font-weight:750;font-size:16px}.difficulty{display:inline-block;margin-top:5px;font-size:11px;padding:4px 8px;border-radius:999px;background:#1c3046;color:#a9c0d4}.difficulty.difficult{color:#ffb5c3;background:#3d2636}.difficulty.medium{color:#ffd692;background:#3b3022}.difficulty.easy{color:#a2f1d0;background:#19392f}.status-badge{white-space:nowrap;font-size:11px;padding:5px 9px;border-radius:999px;background:#223247;color:var(--muted)}.status-badge.done{background:#164635;color:#8af3c1}.status-badge.busy{background:#123d51;color:#78e6ff}.status-badge.review{background:#4a3620;color:#ffd28a}.status-badge.located{background:#2c2c58;color:#c3bcff}.score-badge{white-space:nowrap;font-size:12px;font-weight:800;padding:5px 9px;border-radius:8px;background:#173b36;color:#9cf5ce}.score-badge.zero{background:#4a3620;color:#ffd28a}.target{margin:14px 0;color:#b8c9db;line-height:1.6;font-size:13px}.binding-line{display:flex;align-items:center;gap:9px;color:var(--muted);font-size:11px;border-top:1px solid #20364d;padding-top:11px}.binding-line strong{color:var(--text);font-size:13px}.evidence-box{margin-top:13px;background:#0a1626;border:1px solid #1f354b;border-radius:13px;padding:11px}.evidence-box.busy{min-height:75px;display:grid;place-items:center;color:var(--muted);font-size:12px}.slot-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}.slot{font-size:10px;padding:4px 7px;border-radius:7px;background:#15283c;color:#7793ad}.slot.bound{color:#a6f6d1;background:#163d34}.slot.empty{color:#9caec0}.evidence-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}.evidence-tile{min-width:0;position:relative;border-radius:9px;overflow:hidden;border:1px solid #29465e;background:#13263b;aspect-ratio:4/3}.evidence-tile img{width:100%;height:100%;display:block;object-fit:cover}.evidence-tile .tile-meta{position:absolute;left:0;right:0;bottom:0;background:linear-gradient(transparent,#071321e8);padding:19px 6px 6px;font-size:10px;color:#d5e4f4}.evidence-tile .confidence{float:right;color:var(--cyan)}.sequence{display:flex;gap:5px;align-items:center;margin-top:10px}.sequence span{flex:1;text-align:center;border-radius:7px;padding:6px 2px;background:#182d43;color:#83a0b7;font-size:10px}.sequence span.on{background:#1b5262;color:#a5f3ff}.analysis-box{margin-top:12px;border-top:1px solid #20364d;padding-top:11px}.analysis-title{color:#9eb9cf;font-size:11px;margin-bottom:8px}.tool-list{display:flex;flex-wrap:wrap;gap:6px}.tool-chip{font-size:10px;padding:5px 8px;border-radius:7px;background:#182d43;color:#b9d9e9}.profile-line{color:#7995ad;font-size:10px;line-height:1.5;margin-top:8px}.explain{color:#92aac1;font-size:11px;line-height:1.6;margin-top:10px}.empty-note{color:#657e96;font-size:11px;padding:12px 0;text-align:center}
.footer{margin-top:18px;padding:14px 4px;color:#6e879f;font-size:11px;text-align:center}.target-label{display:block;color:var(--cyan);font-size:11px;letter-spacing:.08em;margin-bottom:5px}.toast{position:fixed;right:23px;bottom:22px;background:#17324b;border:1px solid #3a6b86;border-radius:12px;padding:11px 14px;font-size:12px;opacity:0;transform:translateY(10px);transition:.25s;pointer-events:none}.toast.show{opacity:1;transform:none}
@media(max-width:1050px){.workspace{grid-template-columns:1fr}.timeline{position:static;max-height:none}.timeline-list{display:grid;grid-template-columns:repeat(3,1fr)}.hero{align-items:flex-start;flex-direction:column}.hero-side{text-align:left}.cards{grid-template-columns:1fr}}@media(max-width:800px){.metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.shell{padding:14px}.topbar{align-items:flex-start;flex-direction:column}.metrics{grid-template-columns:repeat(2,1fr)}.timeline-list{grid-template-columns:repeat(2,1fr)}.hero h2{font-size:23px}.evidence-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
</head>
<body>
<div class="shell">
  <header class="topbar"><div class="brand"><div class="brand-mark">AI</div><div><div class="eyebrow">LIVE ANALYSIS CONSOLE</div><h1 id="title">实时智能实训分析</h1></div></div><div class="live-pill"><i class="pulse"></i><span id="connection">视频流已连接</span></div></header>
  <section class="metrics"><div class="metric"><div class="metric-label">当前分析阶段</div><div class="metric-value cyan" id="phase">正在接入视频流</div></div><div class="metric"><div class="metric-label">已处理项目</div><div class="metric-value green" id="done">0/13</div></div><div class="metric"><div class="metric-label">实时总分</div><div class="metric-value amber" id="score">0/13</div></div><div class="metric"><div class="metric-label">证据绑定</div><div class="metric-value" id="bound">0</div></div><div class="metric"><div class="metric-label">系统提示</div><div class="metric-value" id="quality">等待有效画面</div></div></section>
  <div class="workspace"><aside class="timeline"><div class="timeline-title">操作流程 <span>8 → 20</span></div><div class="timeline-list" id="timeline"></div></aside><main class="main"><section class="hero"><div><div class="hero-kicker" id="heroKicker">实时识别中 · 当前项目</div><h2 id="heroTitle">等待当前操作</h2><p id="heroText">系统正在从视频流中定位操作对象，证据生成后会自动绑定到对应项目。</p></div><div class="hero-side"><div class="stage-label">实时完成度</div><div class="stage-value" id="heroProgress">0 / 13</div><div class="progress"><i id="progressBar" style="width:0%"></i></div></div></section><div class="toolbar"><div class="filters" id="difficultyFilters" hidden><button class="chip active" data-filter="all">全部</button><button class="chip" data-filter="difficult">困难</button><button class="chip" data-filter="medium">中等</button><button class="chip" data-filter="easy">简单</button></div><span class="difficulty-hint" id="difficultyHint">完成评分后显示评价难度</span><div class="controls"><button class="control primary" id="start">▶ 启动评测</button><button class="control" id="reset">重置</button></div></div><section class="cards" id="cards"></section></main></div>
  <footer class="footer" id="footer">本页面展示当前视频流的实时分析过程。证据未完成时不输出结论，低置信度结果保留人工确认状态。</footer>
</div><div class="toast" id="toast"></div>
<script>
const DATA = __REPORT_DATA__;
const statuses = {"待开始":"pending","已定位":"located","证据生成中":"busy","证据已绑定":"done","已完成评分":"done","待人工确认":"review"};
const labels = {difficult:"困难",medium:"中等",easy:"简单"};
const terminalStates = new Set(["证据已绑定","已完成评分","待人工确认"]);
let state = DATA.items.map((item, index) => ({...item, binding:{...item.binding, evidence:[...(item.binding.evidence||[])]}, status:"待开始", index}));
let current = 0, evaluationActive = false, processing = false, pollTimer = null, filter = "all", pending = [], pendingKeys = new Set();
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>\"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
function cls(status){return statuses[status]||"pending"}
function displayState(status){return status==="证据已绑定"?"已完成评分":status}
function isTerminal(item){return terminalStates.has(item.status)}
function scoreFor(item){return item.status==="待人工确认"?0:(item.status==="证据已绑定"||item.status==="已完成评分"?1:null)}
function doneCount(){return state.filter(isTerminal).length}
function renderTimeline(){ $("timeline").innerHTML=state.map((item,i)=>`<button class="timeline-btn ${i===current?"active":""}" data-index="${i}"><span class="timeline-no">${item.item_number}</span><span class="timeline-name">${esc(item.display_name)}<small class="timeline-state">${esc(item.status)}</small></span><i class="mini-dot ${cls(item.status)}"></i></button>`).join(""); document.querySelectorAll(".timeline-btn").forEach(btn=>btn.onclick=()=>{current=Number(btn.dataset.index);render()}) }
function evidenceMarkup(item){
  if(["待开始","已定位"].includes(item.status)) return `<div class="evidence-box busy">${item.status==="待开始"?"等待当前操作进入识别区域":"已定位对象，准备提取证据"}</div>`;
  if(item.status==="证据生成中") return `<div class="evidence-box busy"><span class="pulse"></span>&nbsp;正在生成当前项目证据…</div>`;
  const evidence=item.binding.evidence||[];
  const ids=new Set(evidence.map(e=>e.kind));
  const slots=(item.required_slots||[]).map(slot=>`<span class="slot ${slot.bound?"bound":"empty"}">${esc(slot.label)}</span>`).join("");
  const tiles=evidence.slice(0,6).map(e=>`<div class="evidence-tile">${e.src?`<img src="${e.src}" alt="当前证据">`:`<div class="empty-note">证据图生成中</div>`}<div class="tile-meta">${esc(e.phase||e.kind||"证据")} ${e.timestamp?`· ${esc(e.timestamp)}`:""}<span class="confidence">${e.confidence!=null?Math.round(Number(e.confidence)*100)+"%":""}</span></div></div>`).join("");
  const seq=evidence.filter(e=>["multi_frame_sequence","temporal_order","sequence_order"].includes(e.kind));
  const explanation=item.binding.evidence_explanation||(item.status==="已完成评分"?item.evidence_hint:"当前证据已提取，等待人工确认。");
  return `<div class="evidence-box"><div class="slot-row">${slots}</div>${tiles?`<div class="evidence-grid">${tiles}</div>`:`<div class="empty-note">当前项目仍在等待可用证据图</div>`}${seq.length?`<div class="sequence">${["开始","动作中","完成"].map((p,i)=>`<span class="${seq[i]?"on":""}">${p}</span>`).join("")}</div>`:""}<div class="explain">${esc(explanation)}</div></div>`;
}
function analysisMarkup(item){
  if(!isTerminal(item))return "";
  const profile=item.analysis_profile||{};
  const tools=(item.analysis_tools||[]).map(tool=>`<span class="tool-chip">${esc(tool.label||tool.tool_id)}</span>`).join("");
  const features=(profile.complexity_features||[]).map(feature=>esc(feature)).join(" · ");
  const metrics=[profile.distinct_tool_count!=null?`${profile.distinct_tool_count} 个工具`:"",profile.average_tool_task_count!=null?`平均 ${profile.average_tool_task_count} 个分析任务`:"",profile.required_slot_count!=null?`${profile.required_slot_count} 类必填证据`:""].filter(Boolean).join(" · ");
  return `<div class="analysis-box"><div class="analysis-title">分析链 · ${esc(item.difficulty_label||labels[item.difficulty]||"")}</div><div class="tool-list">${tools||`<span class="tool-chip">工具链整理中</span>`}</div><div class="profile-line">${esc(metrics)}${features?`<br>证据链：${features}`:""}</div></div>`;
}
function cardMarkup(item,i){
  const terminal=isTerminal(item);
  const evaluation=item.status==="已完成评分"?`<div class="target"><span class="target-label">评分结论</span>${esc(item.realtime_target||"当前评分项已符合证据要求。")}</div>`:"";
  const difficulty=terminal?`<span class="difficulty ${item.difficulty}">${esc(item.difficulty_label||labels[item.difficulty]||"")}</span>`:"";
  const score=terminal?`<span class="score-badge ${scoreFor(item)===0?"zero":""}">${scoreFor(item)} / 1 分</span>`:"";
  return `<article class="item-card ${i===current?"current":""} ${item.status==="待人工确认"?"review":""}" data-difficulty="${terminal?item.difficulty:""}"><div class="card-head"><div class="item-id"><span class="item-no">${item.item_number}</span><div><div class="item-title">${esc(item.display_name)}</div>${difficulty}</div></div><div class="card-actions">${score}<span class="status-badge ${cls(item.status)}">${esc(item.status)}</span></div></div>${evaluation}<div class="binding-line"><span>现场时间</span><strong>${esc(item.binding.live_timestamp||"等待")}</strong><span>·</span><span>置信度</span><strong>${item.binding.time_confidence!=null?Math.round(Number(item.binding.time_confidence)*100)+"%":"—"}</strong></div>${evidenceMarkup(item)}${analysisMarkup(item)}</article>`;
}
function renderCards(){document.querySelectorAll(".item-card").forEach(x=>x.remove()); const visible=state.map((item,i)=>({item,i})).filter(x=>filter==="all"||(isTerminal(x.item)&&x.item.difficulty===filter)); $("cards").innerHTML=visible.map(x=>cardMarkup(x.item,x.i)).join("");}
function render(){
  const item=state[current], complete=doneCount(), bound=state.reduce((n,x)=>n+(x.binding.evidence||[]).length,0), score=state.reduce((n,x)=>n+(scoreFor(x)||0),0), hasDifficulty=state.some(isTerminal);
  $("title").textContent=DATA.title; $("footer").textContent=DATA.presentation.footer; $("done").textContent=`${complete}/${state.length}`; $("score").textContent=`${score}/${state.length}`; $("bound").textContent=String(bound); $("heroProgress").textContent=`${complete} / ${state.length}`; $("progressBar").style.width=`${Math.round(complete/state.length*100)}%`; $("difficultyFilters").hidden=!hasDifficulty; $("difficultyHint").hidden=hasDifficulty;
  $("phase").textContent=item.status==="待开始"?DATA.presentation.initial_state:item.status; $("quality").textContent=item.status==="待人工确认"?"需要人工确认":item.status==="证据生成中"?"证据生成中":complete?"证据链已收敛":"等待有效画面"; $("heroKicker").textContent=`实时识别中 · 项目 ${item.item_number}`; $("heroTitle").textContent=item.display_name; const liveExplanation=item.binding.evidence_explanation&&item.binding.evidence_explanation!=="等待当前视频流中的有效证据。"?item.binding.evidence_explanation:"系统正在从视频流中定位操作对象，证据生成后会自动绑定到对应项目。"; $("heroText").textContent=item.status==="已完成评分"?(item.binding.evidence_explanation||item.realtime_target||"当前评分项已完成。"):liveExplanation;
  renderTimeline(); renderCards();
}
function toast(message){$("toast").textContent=message;$("toast").classList.add("show");setTimeout(()=>$("toast").classList.remove("show"),1600)}
function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
function mergeView(view){
  if(!view||!Array.isArray(view.items))return;
  view.items.forEach(incoming=>{
    const index=state.findIndex(item=>item.item_id===incoming.item_id); if(index<0)return;
    const currentItem=state[index], incomingBinding=incoming.binding||{}, currentBinding=currentItem.binding||{};
    const incomingRevision=Number(incomingBinding.revision||0), currentRevision=Number(currentBinding.revision||0);
    const incomingEvidence=(incomingBinding.evidence||[]).length, currentEvidence=(currentBinding.evidence||[]).length;
    const incomingState=incomingBinding.state||"待开始", currentState=currentBinding.state||"待开始";
    if(pendingKeys.has(incoming.item_id)||(incomingRevision<=currentRevision&&incomingEvidence<=currentEvidence&&incomingState===currentState))return;
    const hasEvidence=(incoming.binding&&incoming.binding.evidence||[]).length>0;
    if(incomingRevision>currentRevision||incomingEvidence>currentEvidence||incomingState!==currentState||hasEvidence){pending.push({index,incoming,finalState:displayState(incomingState),processingMs:Number(view.processing_ms||0)});pendingKeys.add(incoming.item_id)}
  });
  drainQueue();
}
async function present(update){
  const item=state[update.index]; if(!item)return;
  current=update.index; item.status="已定位"; render(); toast(`已定位：${item.display_name}`); await delay(430);
  if(!evaluationActive){pendingKeys.delete(update.incoming.item_id);return;}
  item.status="证据生成中"; render(); await delay(update.processingMs||950);
  if(!evaluationActive){pendingKeys.delete(update.incoming.item_id);return;}
  state[update.index]={...update.incoming,status:update.finalState,index:update.index,binding:{...update.incoming.binding,evidence:[...(update.incoming.binding.evidence||[])]}};
  render(); toast(`${item.display_name}：${update.finalState}`);
}
async function drainQueue(){if(processing||!evaluationActive)return;processing=true;while(pending.length&&evaluationActive){const update=pending.shift();await present(update);pendingKeys.delete(update.incoming.item_id)}processing=false}
async function pollReport(){
  if(!evaluationActive||location.protocol==="file:")return;
  try{const response=await fetch(`/api/report?ts=${Date.now()}`,{cache:"no-store"});if(response.ok)mergeView(await response.json())}
  catch(error){$("connection").textContent="等待分析服务";$("quality").textContent="连接重试中"}
}
async function runMockEvents(){
  for(const event of DATA.events||[]){if(!evaluationActive)break;await delay(Number(event.delay_ms||900));if(!evaluationActive)break;const patch=event.item_patch;if(patch)mergeView({items:[patch],processing_ms:event.processing_ms});while(processing&&evaluationActive)await delay(120);}
}
async function startEvaluation(){
  if(evaluationActive){evaluationActive=false;if(pollTimer)clearInterval(pollTimer);$("start").textContent="▶ 启动评测";$("connection").textContent="评测已暂停";toast("实时评测已暂停");return}
  const fileMode=location.protocol==="file:";
  if(fileMode&&!(DATA.events||[]).length){toast("请通过本地演示服务启动评测");return}
  evaluationActive=true;$("start").textContent="Ⅱ 暂停评测";$("connection").textContent="实时分析中";toast("已启动实时评测");
  if(!fileMode){await pollReport(); pollTimer=setInterval(pollReport,Number(DATA.presentation.poll_interval_ms||650));}
  if((DATA.events||[]).length)runMockEvents();
}
async function resetReport(){
  if(location.protocol==="file:"){toast("请通过本地演示服务执行重置");return}
  evaluationActive=false;if(pollTimer)clearInterval(pollTimer);pending=[];pendingKeys.clear();processing=false;
  try{const response=await fetch("/api/reset",{method:"POST"});if(!response.ok)throw new Error("reset failed");const view=await response.json();state=view.items.map((item,index)=>({...item,status:"待开始",index,binding:{...item.binding,evidence:[...(item.binding.evidence||[])]},score:null}));current=0;filter="all";document.querySelectorAll(".chip").forEach(x=>x.classList.toggle("active",x.dataset.filter==="all"));$("start").textContent="▶ 启动评测";$("connection").textContent="视频流已连接";render();toast("已恢复初始评测状态")}
  catch(error){toast("重置失败，请检查本地演示服务")}
}
$("start").onclick=startEvaluation;
$("reset").onclick=resetReport;
document.querySelectorAll(".chip").forEach(btn=>btn.onclick=()=>{document.querySelectorAll(".chip").forEach(x=>x.classList.remove("active"));btn.classList.add("active");filter=btn.dataset.filter;renderCards()});
render();
</script>
</body></html>'''


FORBIDDEN_HTML_MARKERS = (
    "26/26", "50/50", "标准结果", "预置", "离线", "offline_run_id",
    "scoring_report_summary", "source_path", "/mnt/shared-storage-user/",
    "已听到操作员口述", "mock_live_stream", "c475-nested-10-r1",
    "自动播放", "下一项", "已完成展示", "高风险", "稳定", "prefilled_score",
    "runNext", "runOne",
)


def render_html(payload: Mapping[str, Any]) -> str:
    public = public_projection(payload)
    data = json.dumps(public, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    output = HTML_TEMPLATE.replace("__REPORT_DATA__", data)
    for marker in FORBIDDEN_HTML_MARKERS:
        if marker in output:
            raise ValueError(f"HTML projection contains forbidden marker: {marker}")
    return output


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成实时证据展示报告")
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser("template", help="写出 8-20 项实时报告模板 JSON")
    template.add_argument("--output", required=True, type=Path)
    template.add_argument("--profiles", type=Path, help="workflow tool profile JSON")
    render = sub.add_parser("render", help="读取 JSON 生成自包含 HTML")
    render.add_argument("--input", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "template":
        _write_json(args.output, template_payload(load_tool_profile(args.profiles) if args.profiles else None))
        print(f"已生成模板 JSON：{args.output}")
        return 0
    payload = _read_json(args.input)
    html_content = render_html(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_content, encoding="utf-8")
    print(f"已生成 HTML：{args.output}（{len(html_content):,} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
