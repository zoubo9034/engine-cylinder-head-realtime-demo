# 实时评测 · Coursera 课程页风格 V4

本目录是后续前端视觉与交互修改的基线，独立于根目录的原始页面生成器。

## 视觉

- 参考 Coursera 课程页：白顶栏、深蓝课程 banner、浅灰内容区、`#0056D2` 主色、Source Sans 3。
- Banner 用深蓝到亮蓝渐变，右侧圆环/暖橙光斑作装饰；指标卡、进度条、主按钮用蓝橙渐变提一点活泼，避免紫粉 mesh 和玻璃拟态。
- 顶部 banner 标题为「发动机气缸盖拆装智能实训分析」，右侧为启动/重置。
- 页面使用固定 1920px 基准画布和固定三栏比例；小窗口通过横纵滚动查看，不缩放或重排评分卡与视频区域。

## 交互

与端砚 UI 共用同一报告契约：左侧 8→20 流程、单列项目卡、证据槽位、详情抽屉、查看器、启动/暂停/重置。
`image` 模式显示关键帧，`video` 模式显示 960×540、10fps 视频片段；两种模式不会混用，且页面同时最多播放一个证据视频。

## 生成

```powershell
python worldskills-redesign-v2/render_report_v4.py --input 展示标准报告_8-20.json --output worldskills-redesign-v2/展示标准报告_8-20_v4.html
python worldskills-redesign-v2/render_report_v4.py --input 展示标准报告_8-20_mock.json --output worldskills-redesign-v2/展示标准报告_8-20_mock_v4.html
python worldskills-redesign-v2/render_report_v4.py --input 展示标准报告_8-20_video.json --output worldskills-redesign-v2/展示标准报告_8-20_video_v4.html
python worldskills-redesign-v2/render_report_v4.py --input 展示标准报告_8-20_mock_video.json --output worldskills-redesign-v2/展示标准报告_8-20_mock_video_v4.html
python -m unittest -v worldskills-redesign-v2/test_redesign_v4.py
```

图片 Mock 可以直接打开；视频 Mock 需要通过根目录的 `serve_demo.py` 访问
`worldskills-redesign-v2/展示标准报告_8-20_mock_video_v4.html`。
