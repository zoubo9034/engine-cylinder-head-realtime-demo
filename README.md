# 发动机气缸盖实时智能实训展示

本目录是一个独立的实时展示原型，只读用已有 Engine artifacts 生成 mock 回放，
不修改上级目录中的 core、engine 或 Video-MME 代码。页面只展示当前视频流的分析过程，
不会把内部评分快照、来源路径或离线生成信息投影给观众。

## 目录内容

| 文件 | 用途 |
| --- | --- |
| `report_schema.py` | 8–20 项报告模板和 JSON 校验 |
| `render_report.py` | 从报告 JSON 生成自包含 HTML |
| `build_mock_report.py` | 从 10 个视频 artifacts 生成事件回放 JSON |
| `workflow_tool_stats.py` | 汇总 10 个视频的 `workflow_trace`，生成难度与分析工具 profile |
| `workflow_tool_profile_10video.json` | 由当前 10 个视频生成的无路径工具链统计 |
| `serve_demo.py` | 提供静态文件、实时轮询、回填和重置 API |
| `展示标准报告_8-20.json` | 现场使用的初始空模板 |
| `展示标准报告_8-20.html` | 现场报告页面 |
| `展示标准报告_8-20_mock.json` | artifacts 生成的 mock 事件数据 |
| `展示标准报告_8-20_mock.html` | mock 回放页面 |

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

模板包含 13 个现场回填位置。初始状态为“待开始”，现场识别程序写入某项的绑定数据后，
页面通过轮询检测到变更并自动展开该项的证据动画。

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
进入“已完成评分”后显示该项 `1 / 1 分`、评价难度、实际分析任务数和实际统计出的分析工具；进入“待人工确认”
时显示 `0 / 1 分` 与同一分析链，但不显示“符合标准”的评价文案。总分从 `0/13` 开始，按终态
逐项累加，人工确认项计 0 分。

## 生成 mock 回放

默认示例使用上级 Engine 目录中已有的 10 个视频运行产物：

```bash
python build_mock_report.py \
  --source-run ../unified-scoring-engine/outputs/c475-nested-10-r1 \
  --template 展示标准报告_8-20.json \
  --output 展示标准报告_8-20_mock.json

python render_report.py render \
  --input 展示标准报告_8-20_mock.json \
  --output 展示标准报告_8-20_mock.html
```

mock JSON 的 13 个评分项初始仍为空；由 artifacts 提取出的证据保存在 `events[].item_patch`，
所以启动回放后可以看到“已定位 → 证据生成中 → 已完成评分/待人工确认”的逐项过程。
当前 fixture 刻意保留已完成、人工确认和仍在分析中的混合状态，用于检验难度、工具链、0/1 分
和总分的实时显隐逻辑。

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
- 左侧流程项：只用于查看某个评分项，不承担“下一项”推进功能。

mock HTML 也可以直接用浏览器打开并启动内嵌回放；标准 HTML 的实时轮询和重置功能需要通过
上面的本地服务访问。

## API

### `GET /api/report`

返回不含内部来源和评分快照的公开投影。服务端会省略 mock 事件列表，因为事件已经嵌入 mock HTML，
避免浏览器轮询时重复传输大图片。每个项目的 `difficulty`、`analysis_tools` 和 `score` 只有在
“证据已绑定”“已完成评分”或“待人工确认”等终态才进入公开投影。

### `POST /api/update`

向单个评分项写入绑定数据，服务会自动增加该项 `live_binding.revision`。示例：

```json
{
  "item_id": "item_5069",
  "live_binding": {
    "state": "证据生成中",
    "live_timestamp": "00:06:28",
    "time_confidence": 0.96,
    "evidence_explanation": "已定位扳手与螺栓，正在等待连续帧。"
  }
}
```

识别程序也可以提交 `item_patch`，其中包含该评分项的 `live_binding` 和证据槽位。服务端根据
`live_binding.state` 归一化分数：`已完成评分`/`证据已绑定` 为 1，`待人工确认` 为 0，其他状态
保持空值，忽略请求中自行传入的分数字段。

### `POST /api/reset`

以原子替换方式写回模板，保证页面和 JSON 不会读到半写入内容。

## 校验

```bash
python -m unittest -v test_report.py
python -m py_compile report_schema.py render_report.py build_mock_report.py serve_demo.py
```

测试覆盖模板范围、跨评分项证据复用、公开投影脱敏、mock 事件结构和重置行为。
