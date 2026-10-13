# -*- coding: utf-8 -*-
"""Tauri 版发布必做步骤 #5：签名 setup → 重写 update.json → 上传 release → push master。
【强制】每次发布 Tauri 新版必须运行本脚本，否则已装用户收不到更新（历史教训：3.1.27~3.1.29 漏跑，update.json 停在 3.1.26，检查更新失效一个多月）。
用法：python scripts\release\updater_publish.py 3.1.31
前置：tauri build 已产出 <setup>；release tag v<VER> 已创建（本脚本不建 release）。
"""
import json, subprocess, os, sys, time, re, urllib.request, urllib.error

VER = sys.argv[1] if len(sys.argv) > 1 else "X.Y.Z"
NODE = r"D:\Program Files\nodejs\node.exe"
TAURI_JS = r"D:\Documents\FewType\tauri\frontend\node_modules\@tauri-apps\cli\tauri.js"
KEY = r"D:\Documents\FewType\keys\fewtype.key"
SETUP = r"D:\Documents\FewType\tauri\src-tauri\target\release\bundle\nsis\FewType_%s_x64-setup.exe" % VER
REPO = "repos/Ray1979ANYWAY/FewType"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"

env = dict(os.environ)
env["TAURI_SIGNING_PRIVATE_KEY_PATH"] = KEY
env["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] = ""
r = subprocess.run([NODE, TAURI_JS, "signer", "sign", "-f", KEY, SETUP],
                   input="\n", capture_output=True, text=True, timeout=300, env=env)
if r.returncode != 0:
    print("SIGN_FAIL", r.stderr[-400:]); sys.exit(1)
m = re.search(r"Public signature:\s*([A-Za-z0-9+/=]+)", r.stdout)
if not m:
    print("PARSE_FAIL", r.stdout[-400:]); sys.exit(1)
signature = m.group(1).strip()
print("signed ok, sig_len=%d" % len(signature))

pub_date = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
url = "https://github.com/Ray1979ANYWAY/FewType/releases/download/v%s/FewType_%s_x64-setup.exe" % (VER, VER)
doc = {
    "version": VER,
    "notes": "FewType %s" % VER,
    "pub_date": pub_date,
    "platforms": {
        "windows-x86_64": {"signature": signature, "url": url}
    },
}
for path in (r"D:\Documents\FewType\dist\update.json", r"D:\Documents\FewType\update.json"):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
print("update.json written (version %s)" % VER)

p = subprocess.run(["git", "credential", "fill"],
                   input="protocol=https\nhost=github.com\n\n",
                   capture_output=True, text=True, timeout=15)
token = ""
for line in p.stdout.splitlines():
    if line.startswith("password="):
        token = line[len("password="):]
if not token:
    print("NO_TOKEN"); sys.exit(1)

HDRS = {"Authorization": "token %s" % token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "FewType-Release-Bot"}

def api(method, url, data=None, ctype=None, retries=4):
    h = dict(HDRS)
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        h["Content-Type"] = ctype or "application/json"
    last = ""
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            last = str(e)
            if attempt < retries:
                time.sleep(5)
    return -1, last

st, rb = api("GET", "%s/%s/releases/tags/v%s" % (API, REPO, VER))
if st != 200:
    print("RELEASE_NOT_FOUND — 先创建 release tag v%s" % VER); sys.exit(1)
rel = json.loads(rb)
rid = rel["id"]
print("release id=%d" % rid)
for asset in rel["assets"]:
    if asset["name"] == "update.json":
        st, _ = api("DELETE", "%s/%s/releases/assets/%d" % (API, REPO, asset["id"]))
        print("deleted old update.json status=%d" % st)
        time.sleep(2)

with open(r"D:\Documents\FewType\dist\update.json", "rb") as f:
    data = f.read()
st, body = api("POST", "%s/%s/releases/%d/assets?name=update.json" % (UPLOADS, REPO, rid),
               data, "application/octet-stream")
print("upload update.json status=%d" % st)
if st not in (201, 202):
    print(body[:300]); sys.exit(1)

os.chdir(r"D:\Documents\FewType")
subprocess.run(["git", "add", "update.json"], check=True)
subprocess.run(["git", "commit", "-m", "updater: update.json 升至 v%s" % VER], check=True)
r2 = subprocess.run(["git", "-c", "http.sslBackend=openssl", "-c", "https.sslBackend=openssl",
                     "push", "origin", "master"], capture_output=True, text=True, timeout=600)
print("push:", r2.returncode, (r2.stdout or r2.stderr).strip()[-200:])
print("DONE")
