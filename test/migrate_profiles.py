#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把本机所有 Zotero profile 一次性迁到 ZBridge，并彻底移除 Debug Bridge。

做四件事（每个 profile 各做一遍，全部先备份）：
  1. prefs.js 写入统一的 extensions.zotero.zbridge.token，删掉 debug-bridge.* 的令牌行
  2. extensions/ 里删掉 debug-bridge 的 xpi / 解包目录，删掉旧版 zbridge xpi
  3. 装上本次构建的 zbridge xpi
  4. extensions.json 里移除 debug-bridge 的注册项（不删这一项，Zotero 启动会报「插件损坏」）

⚠️ 必须先完全退出 Zotero 再跑：运行中 Zotero 会把 prefs.js 与 extensions.json 覆写回去。

用法：
    python test/migrate_profiles.py                # 预演，只报告不动手
    python test/migrate_profiles.py --apply        # 真正执行
    python test/migrate_profiles.py --apply --token a26c...  # 指定令牌
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
BACKUP_ROOT = os.path.abspath(os.path.join(ROOT, "..", "_backup"))

DB_ID = "debug-bridge@iris-advies.com"
ZB_ID = "zbridge@taotaochen86.github.io"
DEFAULT_TOKEN = "a26c69b1050dbdea9bb5d141d109aeaa"


def profiles_root():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    return os.path.join(appdata, "Zotero", "Zotero", "Profiles")


def latest_xpi():
    files = sorted(glob.glob(os.path.join(DIST, "zbridge-*.xpi")))
    if not files:
        sys.exit("[ERR] dist/ 下没有 zbridge-*.xpi，先跑 python build.py")
    return files[-1]


def zotero_running():
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq zotero.exe"],
                             capture_output=True, text=True, errors="ignore").stdout
        return "zotero.exe" in out.lower()
    except Exception:
        return False


def patch_prefs(path, token):
    """返回 (变更说明列表, 是否有改动)"""
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
        lines = f.read().splitlines()

    notes = []
    kept = []
    for ln in lines:
        if 'extensions.zotero.zbridge.token' in ln:
            notes.append("替换旧 zbridge.token")
            continue
        if ln.startswith('user_pref("extensions.autoDisableScopes"'):
            # 侧载 xpi 放进 profile 的 extensions/ 后，默认会被判「外部来源」并自动禁用；
            # 设 0 才会自动启用。只影响本 profile 的侧载，不放松其他作用域。
            notes.append("替换 autoDisableScopes")
            continue
        if ('extensions.zotero.debug-bridge.token' in ln
                or 'extensions.zotero.debug-bridge.password' in ln):
            notes.append("删除 debug-bridge 令牌行")
            continue
        if 'extensions.webextensions.uuids' in ln:
            # 该行是个 JSON 字符串，把 debug-bridge 的 uuid 条目摘掉
            m = re.search(r'user_pref\("extensions\.webextensions\.uuids", "(.*)"\);', ln)
            if m:
                raw = m.group(1)
                try:
                    data = json.loads(raw.replace('\\"', '"'))
                except Exception:
                    data = None
                if isinstance(data, dict) and DB_ID in data:
                    data.pop(DB_ID, None)
                    new = json.dumps(data, ensure_ascii=False).replace('"', '\\"')
                    ln = 'user_pref("extensions.webextensions.uuids", "%s");' % new
                    notes.append("uuids 里移除 debug-bridge")
        kept.append(ln)

    kept.append('user_pref("extensions.zotero.zbridge.token", "%s");' % token)
    kept.append('user_pref("extensions.autoDisableScopes", 0);')
    notes.append("写入 zbridge.token=" + token[:8] + "…")

    out = "\n".join(kept) + "\n"
    old = "\n".join(lines) + "\n"
    if out == old:
        return notes, False
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
        f.write(out)
    return notes, True


def patch_extensions_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    addons = data.get("addons")
    if not isinstance(addons, list):
        return "结构不认识，跳过", False
    before = len(addons)
    kept = [a for a in addons if a.get("id") != DB_ID]
    if len(kept) == before:
        return "无 debug-bridge 注册项", False
    data["addons"] = kept
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return "移除 %d 条注册项" % (before - len(kept)), True


def migrate(prof_dir, token, xpi, backup_dir, apply):
    name = os.path.basename(prof_dir)
    report = {"profile": name, "prefs": [], "files": [], "extjson": "", "ok": True}
    ext_dir = os.path.join(prof_dir, "extensions")
    prefs = os.path.join(prof_dir, "prefs.js")
    extjson = os.path.join(prof_dir, "extensions.json")

    os.makedirs(os.path.join(backup_dir, name), exist_ok=True)
    for src in (prefs, extjson):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, name, os.path.basename(src)))

    # 1. 删 debug-bridge 的 xpi / 目录、旧 zbridge xpi
    if apply:
        os.makedirs(ext_dir, exist_ok=True)
    doomed = []
    if os.path.isdir(ext_dir):
        for entry in os.listdir(ext_dir):
            if DB_ID in entry:
                doomed.append(entry)
            elif entry.startswith(ZB_ID):
                doomed.append(entry)   # 标准命名的旧 zbridge（含 .xpi 与 .bak）
            elif re.match(r"zbridge-\d+\.\d+\.\d+.*\.xpi$", entry):
                doomed.append(entry)   # 构建产物名 zbridge-10.1.5.xpi
    for entry in doomed:
        p = os.path.join(ext_dir, entry)
        report["files"].append("备份并删除 " + entry)
        if apply:
            dst = os.path.join(backup_dir, name, entry)
            if os.path.isdir(p):
                shutil.copytree(p, dst, dirs_exist_ok=True)
                shutil.rmtree(p, ignore_errors=True)
            else:
                shutil.copy2(p, dst)
                os.remove(p)

    # 2. 装新 xpi
    # 文件名必须用 <插件id>.xpi：Zotero 的 extensions.json 是按 id + path 记的，
    # 换成 zbridge-10.1.5.xpi 这种「构建产物名」后，旧条目指向的文件消失，
    # AddonManager 会认为插件加载失败，端点一个都不注册（踩过）。
    if apply:
        target = os.path.join(ext_dir, ZB_ID + ".xpi")
        if not os.path.exists(target):
            shutil.copy2(xpi, target)
            report["files"].append("装入 " + os.path.basename(target))
        else:
            report["files"].append("已是最新 " + os.path.basename(xpi))

    # 3. prefs.js
    if os.path.exists(prefs):
        notes, changed = ([], False)
        if apply:
            notes, changed = patch_prefs(prefs, token)
        else:
            notes = ["(预演) 将写入 zbridge.token、清理 debug-bridge 令牌行"]
        report["prefs"] = notes + (["无实质改动"] if apply and not changed else [])

    # 4. extensions.json
    if os.path.exists(extjson):
        if apply:
            msg, _ = patch_extensions_json(extjson)
        else:
            msg = "(预演) 将移除 debug-bridge 注册项"
        report["extjson"] = msg
    else:
        report["extjson"] = "无 extensions.json"

    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预演）")
    ap.add_argument("--token", default=DEFAULT_TOKEN)
    a = ap.parse_args()

    if zotero_running():
        sys.exit("[ERR] Zotero 还在运行。先完全退出 Zotero，否则 prefs.js / extensions.json 会被覆写回去。")

    xpi = latest_xpi()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, "profile-migration-" + stamp)
    print("xpi    :", xpi)
    print("token  :", a.token)
    print("备份到 :", backup_dir)
    print("模式   :", "APPLY" if a.apply else "DRY-RUN")
    print()

    root = profiles_root()
    profs = sorted(p for p in glob.glob(os.path.join(root, "*")) if os.path.isdir(p)
                   and os.path.exists(os.path.join(p, "prefs.js")))
    if not profs:
        sys.exit("[ERR] 没找到 Zotero profile：%s" % root)

    out = []
    for p in profs:
        r = migrate(p, a.token, xpi, backup_dir, a.apply)
        out.append(r)
        print("== %s" % r["profile"])
        for x in r["files"]:
            print("   file :", x)
        for x in r["prefs"]:
            print("   prefs:", x)
        print("   extjson:", r["extjson"])
    print()
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
