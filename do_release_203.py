# -*- coding: utf-8 -*-
"""创建 VoxEcho 2.0.3 (tkinter) Release，curl 上传 zip，删除 v2.0.2 release（保留 tag）"""
import json, subprocess, sys, os, time, urllib.request, urllib.error

REPO = "repos/Ray1979ANYWAY/VoxEcho"
TAG = "v2.0.3"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
ZIP = r"D:\Documents\VoxEcho-2.0.3-tk-win64.zip"

BODY = """# VoxEcho 2.0.3 (tkinter edition)

![Main UI](https://raw.githubusercontent.com/Ray1979ANYWAY/VoxEcho/v2.0.3/docs/screenshots/main_1.png)
![TTS Panel](https://raw.githubusercontent.com/Ray1979ANYWAY/VoxEcho/v2.0.3/docs/screenshots/main_tts.png)

Lightweight tkinter edition — one free program for voice typing (STT), e-book read-aloud, and long-form text-to-speech (TTS). Maintained alongside the Tauri edition (3.1.12).

## ✨ What's New in 2.0.3
- NEW: Fullscreen text editor for the TTS panel — the ⤢ button at the bottom-right of the input/output text boxes opens an immersive editor that covers the main panel exactly. Large 14px font, Ctrl+Z undo, right-click menu, and a "↺ restore original" button (snapshot taken on open, so closing by Esc/X can never lose your text). "Done" saves back to the original box.
- The editor window is positioned to cover the main panel (withdraw → geometry → deiconify), not the top-left corner of the screen.
- Built on 2.0.2: ESC gated by GetMenu + AttachThreadInput foreground unlock (no Alt simulation), confirm-original-then-translate window, Karwai Wong style, extension auto-detects ports 5005/5010.

## 📦 Assets
- `VoxEcho-2.0.3-tk-win64.zip` — portable, includes the browser extension and README

## ⚠️ Notes
- Unsigned build: SmartScreen / antivirus may warn — add to whitelist to run
- Do NOT run together with the Tauri edition (3.1.12)
- Depends on external services (Microsoft TTS / Groq / LLM) — subject to provider policies

---

# VoxEcho 2.0.3（tkinter 版）

轻量精简版：一个程序免费解决语音打字（STT）、电子书朗读、长文本转语音（TTS）。与 Tauri 版（3.1.12）并行维护。

## ✨ 本次更新
- 新增：TTS 面板文本全屏编辑——输入/输出文本框右下角 ⤢ 按钮打开沉浸式编辑窗，尺寸正好盖住主面板；14px 大字、支持 Ctrl+Z 撤销、右键菜单，新增「↺ 恢复原文」按钮（打开瞬间快照，Esc/X 关闭也绝不丢文本）；「完成」保存回原文本框
- 编辑窗定位修复：先隐藏再设尺寸位置再显示（withdraw→geometry→deiconify），默认正好盖住主面板，不再出现在屏幕左上角
- 基于 2.0.2：ESC 按 GetMenu 条件化 + AttachThreadInput 前台解锁（不模拟 Alt）、「确认原文再翻译」窗口、Karwai Wong 风格、扩展自动适配 5005/5010 端口

## 📦 下载
- `VoxEcho-2.0.3-tk-win64.zip` — 绿色免安装版，含浏览器扩展与说明

## ⚠️ 注意
- 程序未签名：SmartScreen / 杀毒软件可能提示——加入白名单即可运行
- 与 Tauri 版（3.1.12）不要同时运行
- 依赖外部服务（微软 TTS / Groq / LLM）——受供应方政策影响

## 反馈
发现任何问题欢迎在 Issues 提交，我会尽力维护。"""

# token
p = subprocess.run(["git", "credential", "fill"],
                   input="protocol=https\nhost=github.com\n\n",
                   capture_output=True, text=True, timeout=15)
token = ""
for line in p.stdout.splitlines():
    if line.startswith("password="):
        token = line[len("password="):]
if not token:
    print("NO_TOKEN"); sys.exit(1)
print("token len=%d" % len(token))

HDRS = {"Authorization": "token %s" % token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "VoxEcho-Release-Bot"}

def api(method, url, data=None, ctype=None, retries=3):
    h = dict(HDRS)
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = ctype or "application/json"
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            last = str(e)
            if attempt < retries:
                time.sleep(4)
    return -1, last

st, body = api("GET", "%s/%s/releases/tags/%s" % (API, REPO, TAG))
if st == 200:
    rel_id = json.loads(body)["id"]
    print("EXISTING release id=%d — PATCH" % rel_id)
    st, body = api("PATCH", "%s/%s/releases/%d" % (API, REPO, rel_id),
                   {"name": "VoxEcho 2.0.3 (tkinter edition)", "body": BODY})
    print("PATCH status=%d" % st)
else:
    st, body = api("POST", "%s/%s/releases" % (API, REPO),
                   {"tag_name": TAG, "name": "VoxEcho 2.0.3 (tkinter edition)",
                    "body": BODY, "draft": False, "prerelease": False})
    print("POST status=%d" % st)
    rel_id = json.loads(body)["id"]
print("release id=%d" % rel_id)

# curl 上传（urllib 传 35MB+ 会 SSL EOF，经验教训）
size = os.path.getsize(ZIP)
print("uploading zip (%.1f MB) via curl..." % (size / 1048576))
os.chdir(os.path.dirname(ZIP))
url = "%s/%s/releases/%d/assets?name=%s" % (UPLOADS, REPO, rel_id, os.path.basename(ZIP))
ok = False
for attempt in range(1, 4):
    r = subprocess.run(["curl.exe", "-s", "-X", "POST",
                        "-H", "Authorization: token %s" % token,
                        "-H", "Content-Type: application/octet-stream",
                        "--data-binary", "@%s" % os.path.basename(ZIP), url],
                       capture_output=True, text=True, timeout=900)
    try:
        d = json.loads(r.stdout or "{}")
    except Exception:
        print("attempt %d non-json: %r" % (attempt, r.stdout[:200])); time.sleep(8); continue
    if d.get("id"):
        print("UPLOAD OK", d.get("browser_download_url")); ok = True; break
    if d.get("errors") and any(e.get("code") == "already_exists" for e in d["errors"]):
        print("ALREADY EXISTS — treat as OK"); ok = True; break
    print("attempt %d failed: %s" % (attempt, json.dumps(d)[:200])); time.sleep(8)

# 删除 v2.0.2 release（保留 tag；Release 页只留最新两条）
st, body = api("GET", "%s/%s/releases/tags/v2.0.2" % (API, REPO))
if st == 200:
    rid = json.loads(body)["id"]
    st2, _ = api("DELETE", "%s/%s/releases/%d" % (API, REPO, rid))
    print("DELETED v2.0.2 release (id=%d) status=%d" % (rid, st2))
else:
    print("SKIP v2.0.2 (not found)")

st, body = api("GET", "%s/%s/releases" % (API, REPO))
print("--- remaining releases ---")
for r in json.loads(body):
    print(r.get("tag_name"), r.get("html_url"))
print("RELEASE_URL", "https://github.com/Ray1979ANYWAY/VoxEcho/releases/tag/" + TAG)
sys.exit(0 if ok else 1)
