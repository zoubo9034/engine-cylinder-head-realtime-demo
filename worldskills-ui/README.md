# 实时评测 · 世赛 UI V4

本目录是世赛 UI 的前端视觉与交互基线，独立于根目录的端砚 UI 生成器。

## 视觉

- 图片证据页保留课程页式 Banner、指标卡和固定 1920×1080 基准画布。
- 视频证据页使用赛事判定台布局：顶部延续蓝色赛事主视觉和大尺寸联合 Logo，固定 8—20 评分流程栏、2:3 实时竖屏主区和可滚动的完整评分卡流；1760px 工作区在 1920px 全屏窗口两侧各保留约 80px 余量。
- 评分卡的视频证据位固定为 9:16。原始 16:9 视频保持完整比例置中，周围区域由同帧 Canvas 实时模糊延展；Canvas 不可用时回退为深色渐变，不裁切原始画面。

## 交互

与端砚 UI 共用同一报告契约：左侧 8→20 流程、单列项目卡、证据槽位、详情抽屉、查看器、启动/暂停/重置。
`image` 模式显示关键帧，`video` 模式显示 960×540、10fps 视频片段；两种模式不会混用，且页面同时最多播放一个证据视频。
实时画面通过 `window.realtimeVideoInput.acceptOffer()` 接收 WebRTC offer 并将远端轨道挂载到
`#live-video.srcObject`；视频事件版启动时使用 `../demo-media/live-input-sample.webm` 构造虚拟发送端，
走同一 WebRTC 接收链路。

## 生成

```powershell
python worldskills-ui/render_report_v4.py --input 展示标准报告_8-20.json --output worldskills-ui/image/展示标准报告_8-20_v4.html
python worldskills-ui/render_report_v4.py --input 展示标准报告_8-20_mock.json --output worldskills-ui/image/展示标准报告_8-20_mock_v4.html
python worldskills-ui/render_report_v4.py --input 展示标准报告_8-20_video.json --output worldskills-ui/video/展示标准报告_8-20_video_v4.html
python worldskills-ui/render_report_v4.py --input 展示标准报告_8-20_mock_video.json --output worldskills-ui/video/展示标准报告_8-20_mock_video_v4.html
python -m unittest -v worldskills-ui/test_redesign_v4.py
```

图片 Mock 可以直接打开；视频 Mock 需要通过根目录的 `serve_demo.py` 访问
`worldskills-ui/video/展示标准报告_8-20_mock_video_v4.html`。
