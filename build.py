#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包 / 安装 ZBridge。

  python build.py            构建 dist/zbridge-<版本>.xpi 与 update.json
  python build.py --bump     先把版本号末位 +1（manifest 与 bootstrap 同步），再构建
  python build.py --test     先跑离线单测（不需要 Zotero），通过才构建
  python build.py --install  构建后拷进 Zotero profile 的 extensions/（需先退出 Zotero）
  python build.py --probe    构建后跑 6 组连通性验证（Zotero 需在运行、插件需已装）
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
import re
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


def compat_range():
    """从清单里取 (strict_min_version, strict_max_version)。"""
    z = manifest()["applications"]["zotero"]
    return z.get("strict_min_version", "*"), z.get("strict_max_version", "*")


def _vtuple(v):
    """把 '10.0.7' 拆成 (10, 0, 7)。非数字段（如 beta1）按 0 处理，够用。"""
    out = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def in_range(version, lo, hi):
    """按 toolkit 的语义判断 version 是否落在 [lo, hi]。
    hi 结尾的 '.*' 按前缀匹配（'10.*' 匹配 10.0~10.99），'*' 表示不设上限。"""
    v = _vtuple(version)
    if lo and lo != "*":
        n = len(_vtuple(lo))
        if v[:n] < _vtuple(lo)[:n]:
            return False
    if not hi or hi == "*":
        return True
    if hi.endswith(".*"):
        n = len(_vtuple(hi[:-2]))
        return v[:n] <= _vtuple(hi[:-2]) and v[0] == _vtuple(hi[:-2])[0]
    n = len(_vtuple(hi))
    return v[:n] <= _vtuple(hi)[:n]


def bootstrap_version():
    """从 bootstrap.js 里取 ZB_VERSION，用于和 manifest 比对。"""
    m = re.search(r"const\s+ZB_VERSION\s*=\s*'([^']*)'", (SRC / "bootstrap.js").read_text(encoding="utf-8"))
    if not m:
        die("bootstrap.js 里找不到 ZB_VERSION")
    return m.group(1)


def check_versions():
    """manifest.json 与 bootstrap.js 的版本号必须一致，否则装进去 ping 会报旧版本。"""
    mv, bv = manifest()["version"], bootstrap_version()
    if mv != bv:
        die("版本号不一致：manifest.json=%s，bootstrap.js=%s。两个都要改（或用 --bump 自动同步）" % (mv, bv))
    return mv


def bump():
    """把 patch 位加一并同步到两处。约定：每次改动都递增。"""
    m = manifest()
    old = m["version"]
    parts = old.split(".")
    if not parts[-1].isdigit():
        die("版本号末位不是数字，无法自增：%s" % old)
    parts[-1] = str(int(parts[-1]) + 1)
    new = ".".join(parts)

    m["version"] = new
    (SRC / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    bs = SRC / "bootstrap.js"
    text = bs.read_text(encoding="utf-8")
    text2 = re.sub(r"(const\s+ZB_VERSION\s*=\s*')[^']*(')", r"\g<1>%s\g<2>" % new, text, count=1)
    if text2 == text:
        die("bootstrap.js 里没能替换掉 ZB_VERSION")
    bs.write_text(text2, encoding="utf-8")

    print("[ OK ] 版本 %s → %s（manifest.json / bootstrap.js 已同步）" % (old, new))
    return new


def profiles():
    root = Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "Zotero" / "Zotero" / "Profiles"
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if (p / "prefs.js").is_file()]


def build():
    DIST.mkdir(parents=True, exist_ok=True)
    ver = check_versions()
    m = manifest()
    out = DIST / ("zbridge-%s.xpi" % ver)

    files = ["manifest.json", "bootstrap.js"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in files:
            z.write(SRC / name, name)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    lo, hi = compat_range()
    print("[ OK ] %s  (%d 字节)" % (out, out.stat().st_size))
    print("       sha256 %s" % digest)
    print("       兼容 Zotero %s ~ %s" % (lo, "不限" if hi == "*" else hi))

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
        ("执行 JS（Origin null + 令牌）", ex + "?token=" + TOKEN, {"Content-Type": "text/plain", "Origin": "null"}, "return Zotero.version", "POST", 200),
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

    # 兼容性断言：本机实际 Zotero 版本必须落在清单声明范围内
    lo, hi = compat_range()
    try:
        _, _, text = req(base + "/zbridge/ping", {}, None, "GET")
        info = json.loads(text)
        zv = str(info.get("zotero") or "")
        ok = bool(zv) and in_range(zv, lo, hi)
        print("[%s] %-28s 本机 Zotero %s，清单声明 %s~%s"
              % (" OK " if ok else "FAIL", "版本落在声明范围", zv or "?",
                 lo, "不限" if hi == "*" else hi))
    except Exception as e:
        print("[FAIL] %-28s %s" % ("版本落在声明范围", e))
        ok = False
    if not ok:
        bad += 1

    total = len(cases) + 1
    print("\n%d/%d 通过" % (total - bad, total))
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--test", action="store_true", help="构建前先跑离线单测（node test/unit.js，不需要 Zotero）")
    ap.add_argument("--bump", action="store_true", help="先把版本号末位加一（manifest + bootstrap 同步），再构建")
    a = ap.parse_args()

    if a.bump:
        bump()

    if a.test:
        import subprocess
        r = subprocess.run(["node", str(HERE / "test" / "unit.js")], cwd=str(HERE))
        if r.returncode != 0:
            die("离线单测未通过，已中止构建")

    xpi = build()
    if a.install:
        install(xpi)
    if a.probe:
        probe()


if __name__ == "__main__":
    main()
