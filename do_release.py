# -*- coding: utf-8 -*-
"""用 urllib 更新 Release body + 上传 asset（绕开 curl 环境问题）"""
import json, subprocess, sys, os, urllib.request, urllib.error

REPO = "repos/Ray1979ANYWAY/VoxEcho"
ZIP = r"D:\Documents\VoxEcho-2.0.1-tk-win64.zip"
PAYLOAD = r"D:\Documents\FewType-tk\release_payload.json"
TAG = "v2.0.1"
API = "https://api.github.com"

# token（git credential，不落盘不打印）
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

def api(method, url, data=None, ctype=None):
    h = dict(HDRS)
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = ctype or "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

# 1. 查 release
st, body = api("GET", "%s/%s/releases/tags/%s" % (API, REPO, TAG))
try:
    rel = json.loads(body)
    rel_id = rel.get("id")
except Exception:
    rel_id = None
if not rel_id:
    print("GET_RELEASE_FAIL", st, body[:300]); sys.exit(1)
print("release id=%d" % rel_id)

# 2. PATCH（双语 body + 图片）
payload = json.load(open(PAYLOAD, encoding="utf-8"))
st, body = api("PATCH", "%s/%s/releases/%d" % (API, REPO, rel_id),
               {"name": payload["name"], "body": payload["body"]})
try:
    upd = json.loads(body)
    print("PATCH status=%d body_len=%d" % (st, len(upd.get("body", ""))))
except Exception:
    print("PATCH status=%d raw=%s" % (st, body[:300]))

# 3. 上传 asset
zip_size = os.path.getsize(ZIP)
print("zip size=%.1f MB" % (zip_size / 1048576))
with open(ZIP, "rb") as f:
    zip_bytes = f.read()
st, body = api("POST",
               "%s/%s/releases/%d/assets?name=VoxEcho-2.0.1-tk-win64.zip" % (API, REPO, rel_id),
               zip_bytes, "application/zip")
try:
    ass = json.loads(body)
    dl = ass.get("browser_download_url")
    if dl:
        print("ASSET_OK", dl)
    else:
        print("ASSET_FAILED status=%d %s" % (st, body[:300]))
except Exception:
    print("ASSET_PARSE status=%d raw=%s" % (st, body[:300]))

# 4. 最终确认
st, body = api("GET", "%s/%s/releases/%d" % (API, REPO, rel_id))
try:
    final = json.loads(body)
    print("RELEASE_URL", final.get("html_url"))
    print("assets_final:", [a["name"] for a in final.get("assets", [])])
    b = final.get("body", "")
    print("body_has_image:", "raw.githubusercontent.com" in b)
    print("body_has_zh:", "tkinter 版" in b)
except Exception:
    print("FINAL_GET_FAIL", st, body[:300])
