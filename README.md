# 发动机气缸盖实时智能实训展示

本目录是一个独立的实时展示原型，只读用已有 Engine artifacts 生成 mock 回放，
不修改上级目录中的 core、engine 或 Video-MME 代码。标准现场假定操作按规范完成：
评分结论和详细依据随模板预置，现场只回填证据图像、时间和实时分析状态。页面不会把
内部评分快照、来源路径或离线生成信息投影给观众。

[查看更新日志](CHANGELOG.md)

## 目录内容

| 文件 | 用途 |
| --- | --- |
| `report_schema.py` | 8–20 项报告模板和 JSON 校验 |
| `detail_rules.py` | 13 个评分项的对象、动作、时序、完成状态核验依据 |
| `render_report.py` | 从报告 JSON 生成自包含 HTML |
| `build_mock_report.py` | 从 10 或 29 个视频 artifacts 生成事件回放 JSON；每项随机选一个完整得分样本 |
| `workflow_tool_stats.py` | 汇总 10 个视频的 `workflow_trace`，生成难度与分析工具 profile |
| `workflow_tool_profile_10video.json` | 由当前 10 个视频生成的无路径工具链统计 |
| `serve_demo.py` | 提供静态文件、实时轮询、回填和重置 API |
| `展示标准报告_8-20.json` | 现场使用的初始空模板 |
| `展示标准报告_8-20.html` | 现场报告页面 |
| `展示标准报告_8-20_mock.json` | artifacts 生成的 mock 事件数据 |
| `展示标准报告_8-20_mock.html` | mock 回放页面 |

## 视觉设计

`render_report.py` 内联的页面样式依据仓库上级的设计 token 文件
`../duanyan-design-token.html` 重构：纸张色背景（`paper`）、墨色文字（`ink`）、细规则线
（`rule`）、蓝色主色（`vermilion`）、金色提示（`gold`），并以衬线标题、等宽状态标签和
Inter 正文建立层级。时间线使用点状脊柱，卡片、证据和分析链使用 4–8px 圆角与轻阴影，状态
颜色沿用 success / warning / error 浅色语义。HTML 不依赖外部前端包，字体不可用时自动回退到
系统字体。

每张评分卡都保留“展开详细表单”入口。入口始终可见：项目尚未进入终态时，右侧抽屉只显示对象识别、动作过程、时序关系、完成状态四组核验维度及数量；进入“已完成评分”或“待人工确认”后，抽屉才显示逐条依据、当前核验状态、置信度、关联证据、现场时间范围、风险边界和分析链。抽屉为只读查看器，不提供改分、通过/不通过或人工备注操作。

证据缩略图和抽屉中的证据引用共用同一查看交互：鼠标或键盘聚焦约 120ms 后显示带阶段、时间和置信度的悬停预览，点击或触屏点击打开高清灯箱；灯箱支持关闭按钮、背景点击和 Esc。缺少图像时只显示占位状态，不生成图片。

## 环境与工作目录

以下命令均假定当前工作目录就是本目录：

```bash
cd engine-cylinder-head-realtime-demo
```

只使用 Python 标准库，不需要安装前端依赖。

## 重新生成现场模板

```bash
python render_report.py template \
  --output 展示标准报告_8-20.json

python render_report.py render \
  --input 展示标准报告_8-20.json \
  --output 展示标准报告_8-20.html
```

模板包含 13 个现场回填位置。每项同时保存隐藏的 `prefilled_result`（全对分数、评价和
逐条依据），以及留给现场回填的空白 `live_binding`/`detail_evaluation`。因此初始页面仍为
“待开始”、进度 `0/13`，不会显示预置结论；现场只需要写入对应证据和时间。

模板中的 `detail_form` 是固定的纯视觉核验框架；实时识别程序只写入对应项目的 `detail_evaluation`（`locked`、`analyzing`、`unlocked` 或 `unavailable`）和证据引用，详细核验不会改变分数。每条核验结果可使用 `pending`、`confirmed`、`not_confirmed` 或 `manual_review`，并带有置信度、观察摘要、原因和同项目证据 ID。

当某项的全部必需证据槽位（包括现场时间）已经写入后，`serve_demo.py` 自动将状态切换为
“证据生成中”，并启动 8–20 秒的分析窗口。窗口结束才把该项的预置全对依据绑定到实时证据，
状态变为“已完成评分”并显示 `1 / 1 分`。分析窗口内不会提前显示结论；证据被替换会重新开始
该项的窗口。显式提交“待人工确认”仍可用于真实识别器的低置信度结果，分数固定为 0。

模板中的 `difficulty`、`difficulty_label` 和 `analysis_tools` 来自仓库内的
`workflow_tool_profile_10video.json`。该 profile 只记录 10 个视频中各评分项的工具调用数量、
证据链复杂度特征，并为当前评分项生成一个实际分析任务数；它不作为现场分数输入。页面不展示
跨视频统计均值或必填证据类别数量。若重新统计同一批 artifacts，可执行：

```bash
python workflow_tool_stats.py \
  --source-run ../unified-scoring-engine/outputs/c475-nested-10-r1 \
  --output workflow_tool_profile_10video.json
```

前端在项目尚未进入终态时只展示状态、时间和正在生成的证据，不显示评价难度、工具链或评分结论。
进入“已完成评分”后显示该项 `1 / 1 分`、评价难度、实际分析任务数和实际统计出的分析工具；真实识别器若
返回“待人工确认”，则显示 `0 / 1 分` 与未决原因，但不显示“符合标准”的评价文案。总分从 `0/13` 开始，按
终态逐项累加，人工确认项计 0 分。

## 生成 mock 回放

mock 源可以是包含 10 个报告的嵌套 Engine 运行目录，也可以是包含 29 个报告的平铺 artifacts
目录。生成器会先按项目最终得分筛选正确样本，再从同一视频该项目的分析过程关键帧、mask 或
bbox 产物中随机取证；不会把不同视频的帧拼到同一个项目。需要从较大的平铺归档中取出指定
视频时，可用 `--video-manifest`（JSON 或每行一个视频路径）限定清单，并用 `--seed` 固定一次
回放的抽样结果：

```bash
python build_mock_report.py \
  --source-run ../unified-scoring-engine/outputs/c475-nested-10-r1 \
  --seed 20260903 \
  --template 展示标准报告_8-20.json \
  --output 展示标准报告_8-20_mock.json

python render_report.py render \
  --input 展示标准报告_8-20_mock.json \
  --output 展示标准报告_8-20_mock.html
```

mock JSON 的 13 个评分项初始仍为空；由 artifacts 提取出的真实证据保存在
`events[].item_patch`。每个事件都对应一个按规范完成的项目，回放时先显示“已定位”和
“证据生成中”，经过事件固定 3 秒的 mock 分析窗口后显示“已完成评分”、逐条全对依据和
`1 / 1 分`。历史 artifacts 只用于提供真实帧、时间和分析工具链，不改变这套现场标准结论。

## 启动实时演示

启动标准报告服务：

```bash
python serve_demo.py \
  --report 展示标准报告_8-20.json \
  --port 8765
```

浏览器访问：

```text
http://127.0.0.1:8765/展示标准报告_8-20.html
```

启动 mock 回放服务时只需替换报告文件，并打开对应页面：

```bash
python serve_demo.py \
  --report 展示标准报告_8-20_mock.json \
  --port 8765
```

```text
http://127.0.0.1:8765/展示标准报告_8-20_mock.html
```

页面按钮行为：

- `启动评测`：开始轮询报告 JSON；mock 页面会按事件补丁顺序回放。
- `重置`：调用 `/api/reset`，清空现场绑定并恢复初始模板；mock 文件会保留回放事件。
- `展开详细表单`：打开当前评分项的只读详情抽屉；未完成时显示核验维度骨架，终态后显示逐条依据和证据关联。
- 左侧流程项：只用于查看某个评分项，不承担“下一项”推进功能。

mock HTML 也可以直接用浏览器打开并启动内嵌回放；标准 HTML 的实时轮询和重置功能需要通过
上面的本地服务访问。

## API

### `GET /api/report`

返回不含内部来源和评分快照的公开投影。服务端会省略 mock 事件列表，因为事件已经嵌入 mock HTML，
避免浏览器轮询时重复传输大图片。每个项目的 `difficulty`、`analysis_tools` 和 `score` 只有在
“证据已绑定”“已完成评分”或“待人工确认”等终态才进入公开投影。

### `POST /api/update`

向单个评分项写入绑定数据，服务会自动增加该项 `live_binding.revision`。仅写入部分证据时项目
保持“待开始”或“已定位”；写齐必需槽位后自动进入分析窗口。示例：

```json
{
  "item_id": "item_5069",
  "live_binding": {
    "state": "已定位",
    "live_timestamp": "00:06:28",
    "time_confidence": 0.96,
    "evidence_explanation": "已定位扳手与螺栓，正在等待连续帧。"
  }
}
```

识别程序也可以提交 `item_patch`，其中包含该评分项的 `live_binding` 和证据槽位。服务端根据
必需槽位的实际内容自动推进状态，并根据 `live_binding.state` 归一化分数：`已完成评分`/
`证据已绑定` 为 1，`待人工确认` 为 0，其他状态保持空值，忽略请求中自行传入的分数字段。
分析计时保存在服务进程内，不写入页面或公开投影；服务重启后会为仍在分析的项目重新建立
一个 8–20 秒窗口。

详情结果可以随同项目补丁提交，评分仍只由 `live_binding.state` 决定：

```json
{
  "item_id": "item_5069",
  "detail_evaluation": {
    "state": "unlocked",
    "updated_at": "00:06:28",
    "checks": [
      {
        "criterion_id": "wrench_bolt_identity",
        "status": "confirmed",
        "confidence": 0.94,
        "evidence_ids": ["ev-example"],
        "observation": "扳手和目标螺栓清晰可辨。",
        "reason": ""
      }
    ],
    "unresolved_summary": ""
  }
}
```

`detail_form` 由模板固定提供，证据 ID 只能引用同一评分项的实时证据；服务端拒绝跨项目引用。

### `POST /api/reset`

以原子替换方式写回模板，保证页面和 JSON 不会读到半写入内容。

## 校验

```bash
python -m unittest -v test_report.py
python -m py_compile detail_rules.py report_schema.py render_report.py build_mock_report.py serve_demo.py
```

测试覆盖 13 项规则唯一性、隐藏全对基线、初始锁定与重置清空、完整证据触发的 8–20 秒分析窗口、
终态详情公开投影、人工确认状态、跨评分项证据复用、mock 全对事件、特殊轮次与顺序字段、抽屉和
证据图像查看器标记，以及公开页面脱敏。
