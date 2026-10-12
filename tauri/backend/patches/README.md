# edge-tts 端点迁移补丁（2026-10 微软弃用旧端点）

## 背景
2026 年 10 月起，微软弃用旧合成端点 `wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1`，
对所有旧客户端返回 `WSServerHandshakeError: 403`。Microsoft Edge 155 已迁移到
`wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1`（该端点接受旧 token，
作为 `Ocp-Apim-Subscription-Key` 参数）。音色清单接口 `speech.platform.bing.com/voices/list` 仍可用。

edge-tts 官方库（PyPI/GitHub 双端最新 7.2.8，2026-03-22 发布）尚未合入修复
（上游 PR：https://github.com/rany2/edge-tts/pull/491 ，open 状态）。

## 应用方式
安装 edge-tts 后，将本补丁应用到 site-packages 里的 edge_tts 包：
```
cd <site-packages>/edge_tts
git apply <本目录>/edge-tts-msedgeservices.patch
```
或用工具按 diff 手工修改 constants.py 与 drm.py。

## 需要重新打包
`fewtype-backend.exe`（PyInstaller）内置 edge-tts，应用补丁后必须重新运行
`python build_backend.py` 并复制新 sidecar 到 `src-tauri/binaries/`。

## 未来跟踪
- 上游 PR #491 合入并发布新版 edge-tts 后，可移除补丁改回 pip 官方版。
- 若微软再次调整端点，检查 edge-tts 仓库 release/issue 动态。
