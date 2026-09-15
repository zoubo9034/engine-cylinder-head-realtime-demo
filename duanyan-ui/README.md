# 端砚 UI

本目录保存端砚视觉下的生成页面，业务数据和生成器仍由项目根目录统一维护。

- `image/`：图片证据标准页与事件页。
- `video/`：视频证据标准页与事件页。

从项目根目录重新生成：

```bash
python render_report.py render --input 展示标准报告_8-20.json --output duanyan-ui/image/展示标准报告_8-20.html
python render_report.py render --input 展示标准报告_8-20_mock.json --output duanyan-ui/image/展示标准报告_8-20_mock.html
python render_report.py render --input 展示标准报告_8-20_video.json --output duanyan-ui/video/展示标准报告_8-20_video.html
python render_report.py render --input 展示标准报告_8-20_mock_video.json --output duanyan-ui/video/展示标准报告_8-20_mock_video.html
```
