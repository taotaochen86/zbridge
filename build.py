#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包 / 安装 ZBridge。

  python build.py            构建 dist/zbridge-<版本>.xpi 与 update.json
  python build.py --install  构建后拷进 Zotero profile 的 extensions/（需先退出 Zotero）
  python build.py --probe    构建后跑 5 组连通性验证（Zotero 需在运行、插件需已装）
                             改过令牌的话先设环境变量 ZB_TOKEN=你的令牌

注意：全新的插件直接丢进 extensions/ 有可能被 Zotero 的
extensions.autoDisableScopes 判为「侧载」而自动禁用；首次安装更稳的做法是
在 Zotero 里 工具 → 插件 → 齿轮 → Install Plugin From File…，或用
AddonManager 装（见 README）。
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
DIST = HERE / "dist"
ADDON_ID = "zbridge@taotaochen86.github.io"
ADDON_FILE = ADDON_ID + ".xpi"
REPO = "https://github.com/taotaochen86/zbridge"
TOKEN = os.environ.get("ZB_TOKEN") or "e3b6034fbdd85456f5ac6df1a4e54ef4"


def die(msg):
    print("[FAIL] " + msg, file=sys.stderr)
    sys.exit(1)


def manifest():
    return json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))


def profiles():
    root = Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "Zotero" / "Zotero" / "Profiles"
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if (p / "prefs.js").is_file()]


def build():
    DIST.mkdir(parents=True, exist_ok=True)
    m = manifest()
    ver = m["version"]
    out = DIST / ("zbridge-%s.xpi" % ver)

    files = ["manifest.json", "bootstrap.js"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in files:
            z.write(SRC / name, name)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print("[ OK ] %s  (%d 字节)" % (out, out.stat().st_size))
    print("       sha256 %s" % digest)

    # Zotero 的更新源（可选）：让已装旧版的人能自动升级
    update = {
        "addons": {
            ADDON_ID: {
                "updates": [{
                    "version": ver,
                    "update_link": "%s/releases/download/v%s/zbridge-%s.xpi" % (REPO, ver, ver),
                    "update_hash": "sha256:" + digest,
                }]
            }
        }
    }
    (HERE / "update.json").write_text(json.dumps(update, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("       update.json 已更新")
    return out


def install(xpi: Path):
    found = profiles()
    if not found:
        die("找不到 Zotero profile")
    for prof in found:
        ext = prof / "extensions"
        if not ext.is_dir():
            continue
        target = ext / ADDON_FILE
        shutil.copy2(xpi, target)
        print("[ OK ] 已拷入 %s" % target)
    print("       重启 Zotero 生效；若被自动禁用，去 工具 → 插件 里手动启用")


def probe():
    import urllib.request

    # 本机回环请求必须绕开系统/环境代理（WorkBuddy 会话里 http_proxy 常指向一个
    # 不可达端口，走它会得到 502 upstream connect failed）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:23119"
    ex = base + "/zbridge/execute"

    def req(url, headers=None, body=None, method="POST"):
        data = body.encode() if body is not None else None
        r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with opener.open(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode()

    cases = [
        ("ping（不需要令牌）", base + "/zbridge/ping", {}, None, "GET", 200),
        ("执行 JS（Origin null + 令牌）", ex + "?token=" + TOKEN, {"Content-Type": "text/plain", "Origin": "null"}, "return JSON.stringify({v: Zotero.version})", "POST", 200),
        ("无令牌", ex, {"Content-Type": "text/plain", "Origin": "null"}, "return 1", "POST", 401),
        ("错令牌", ex + "?token=wrong", {"Content-Type": "text/plain", "Origin": "null"}, "return 1", "POST", 401),
        ("脚本报错应回 500", ex + "?token=" + TOKEN, {"Content-Type": "text/plain", "Origin": "null"}, "throw new Error('boom')", "POST", 500),
    ]
    bad = 0
    for name, url, hdr, body, method, want in cases:
        try:
            code, h, text = req(url, hdr, body, method)
        except Exception as e:
            print("[FAIL] %-28s %s" % (name, e))
            bad += 1
            continue
        acao = h.get("Access-Control-Allow-Origin")
        ok = code == want and acao == "*"
        print("[%s] %-28s HTTP=%d ACAO=%s %s" % (" OK " if ok else "FAIL", name, code, acao, text[:60].replace("\n", " ")))
        if not ok:
            bad += 1
    print("\n%d/%d 通过" % (len(cases) - bad, len(cases)))
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    xpi = build()
    if a.install:
        install(xpi)
    if a.probe:
        probe()


if __name__ == "__main__":
    main()
