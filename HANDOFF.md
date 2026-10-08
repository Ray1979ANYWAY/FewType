# ⚠️ HANDOFF — FewType 项目接手手册（新 Agent 必读）

> **接手本项目的任何 Agent/开发者，请先完整读完本文件再动手。**
> 本文件汇总了项目全貌、关键资产、发布流程、SEO 状态与踩坑记录，避免重复踩坑、避免破坏既有状态。
> 上次更新：2026-10-08

---

## 0. 一句话

FewType = **Windows 语音助手**（语音打字 STT / 电子书朗读 TTS / 长文本转语音）+ **浏览器扩展**（网页电子书朗读）+ **官网**（Cloudflare Pages 托管）。免费开源，用户自带 AI API Key，无订阅。

两条代码线并存：
- **Tauri 新版（3.x）** —— 当前主力
- **TKinter 老线（2.x，目录 FewType-tk）** —— 维护线，已改名，勿与主线混淆

---

## 1. 本地目录地图

| 目录 | 内容 | Git 分支 |
|---|---|---|
| `D:\Documents\FewType` | **主程序**（Tauri）：`tauri\frontend` 前端、`tauri\src-tauri` Rust | master |
| `D:\Documents\FewType-site` | **官网独立 clone**（Cloudflare Pages 绑定） | gh-pages |
| `D:\Documents\FewType-tk` | TK 老线 2.x | 独立分支/库 |

**关键**：官网不在主仓库工作目录，是独立 clone `FewType-site`，push 用 `origin gh-pages`。

---

## 2. 关键资产（勿丢失、勿误改）

| 资产 | 值 / 位置 |
|---|---|
| 官网（英文主页） | https://fewtype.pages.dev/ |
| 官网（中文页） | https://fewtype.pages.dev/zh |
| GitHub 仓库 | https://github.com/Ray1979ANYWAY/FewType |
| GSC（Google）属性 | `https://fewtype.pages.dev/`（github.io 属性已废弃） |
| Bing Webmaster | `fewtype.pages.dev` 已验证 |
| IndexNow key | `9346fbd5a68844a9af42e393ee1c4ad8`（key 文件 `9346fbd5a68844a9af42e393ee1c4ad8.txt` 在官网根目录） |
| 旧 GitHub Pages 站 | `ray1979anyway.github.io/FewType` **已关闭（404），勿再启用** |

---

## 3. 发布流程（版本 X.Y.Z）— checklist

1. **版本号 5 处同步**：主界面左下角 / update.json / 前端 package / 后端 / 官网链接（硬编码，需手动更新）
2. **前后端版本号必须一致**（历史多次出错，用户很在意）
3. 打包：NSIS 安装版 + portable zip；签名（见 processing.md 记录的正确签名写法）
4. GitHub Release 发布 + 上传资产 + 更新 update.json
5. 官网 index.html/zh.html 下载链接指向最新 release
6. push gh-pages → **IndexNow 自动 ping（GitHub Actions 已配好，无需手动）**
7. **发布前让用户试用**（用户偏好：试用通过才发布）

---

## 4. SEO 状态与红线

**已完成**：
- canonical 自指（EN→ `/`，ZH→ `/zh`）；hreflang 双语言
- JSON-LD（SoftwareApplication）两页就位（含 version/downloadUrl/featureList）
- sitemap.xml 200 且合法（`/` + `/zh`）
- 旧站 github.io 已关闭；README/About 外链指向官网
- GSC 验证 token（两个）与 Bing 验证都在线上

**红线（勿踩）**：
- ❌ 不要反复向 GSC 提交 sitemap——GSC 抓 pages.dev 的 sitemap 显示"无法抓取"是**已知兼容问题**，页面最终仍会被收录，反复提交无意义
- ❌ 不要填 fake aggregateRating（评分）——无真实用户评价时留空，否则有被 Google 处罚风险
- ❌ 不要把 canonical 指向 github.io
- Google 收录处于等待期：旧站 404 后靠 README/About 外链 + Google 周期抓取，预计 1-3 周，**不要催用户反复操作 GSC**

---

## 5. 已自动化流程

- **IndexNow 自动 ping**：`gh-pages/.github/workflows/indexnow-ping.yml`（master 有副本，本地兜底 `ping-indexnow.cmd`）。任何 push 到 gh-pages → 等 2 分钟 → 自动 ping Bing 收录两页。

---

## 6. 踩坑速查（详见 processing.md 完整记录）

- **git push 到 GitHub**：本机 schannel 常失败，用
  `git -c http.sslBackend=openssl -c https.sslBackend=openssl push origin <branch>`
- **GitHub API 本机调用**：PUT 常被网络吞（返回空/26 字节），改用 `-o 文件` + 文件 body + 重试；GET 正常
- **GSC 请求编入索引**：新站配额极小，报"超出每日限额"属正常，别再点
- **热键/静置保活/透明窗口/胶囊主题**等大量 UI 与系统级坑：全部记录在 `processing.md`

---

## 7. 安全红线（用户明确要求）

- 日志**只记错误，不记转写原文**（隐私）
- 打包产物/config **不含 API Key**（用户自己填）；用户要求密钥"不出笼"
- 不向任何外部渠道泄露 token / key / 用户信息

---

## 8. 用户偏好（协作方式）

- 用**中文**回复；诚实——做不到直接说，不拖延、不假装完成
- 出错不反复道歉，一次修好
- 大改动谨慎（用户对"大改动推翻重来"很疲惫）；改动前说明影响
- 发布前先让用户试用；版本号前后端一致是硬要求
- 用户对"自动流程/可复用资产"有强烈偏好——能用脚本/工作流固化的，别靠记忆

---

## 9. 相关文档

- `processing.md` —— 处理日志（诊断/根因/验证，最强参考）
- `ARCHITECTURE.md` —— 架构说明（注意：内容偏旧，以源码为准）
- `README*.md` —— 用户面向的 README（三语言）
