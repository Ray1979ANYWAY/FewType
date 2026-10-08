# ⚠️ HANDOFF — FewType 官网（gh-pages / Cloudflare Pages）接手手册

> 接手本官网仓库（`FewType-site`）的任何 Agent/开发者，先读本文件。
> 完整项目手册见主仓库 `D:\Documents\FewType\HANDOFF.md`。

## 本仓库是什么

- 官网源码（`index.html` 英文 / `zh.html` 中文），Cloudflare Pages 绑定 **gh-pages 分支**，线上 https://fewtype.pages.dev/
- 独立 clone：`D:\Documents\FewType-site`，push 用 `origin gh-pages`

## 关键规则

1. **canonical 必须自指**：EN→`https://fewtype.pages.dev/`，ZH→`https://fewtype.pages.dev/zh`（勿指回 github.io）
2. **版本号/下载链接是硬编码**：每次发布新版本，手动更新两页的 downloadUrl（指向最新 GitHub Release）
3. **IndexNow 自动 ping 已配好**：`.github/workflows/indexnow-ping.yml`——push 到 gh-pages 即自动通知 Bing（key 文件 `9346fbd5a68844a9af42e393ee1c4ad8.txt` 在根目录，勿删）
4. **GSC 验证 meta 两个都在**（google-site-verification + msvalidate.01），勿删
5. **sitemap.xml** 保持 200 且合法；GSC 显示"无法抓取"是已知兼容问题，勿反复提交
6. 本地兜底脚本 `ping-indexnow.cmd`：发布后手动运行可立即 ping Bing（返回 202 = 成功）
7. **JSON-LD（SoftwareApplication）两页都在**，改版时保持字段完整，勿加 fake 评分

## 常见操作

- push（schannel 会失败，用 openssl 后端）：
  `git -c http.sslBackend=openssl -c https.sslBackend=openssl push origin gh-pages`
- 验证部署：Cloudflare 构建约 1-2 分钟，curl https://fewtype.pages.dev/ 确认 200
