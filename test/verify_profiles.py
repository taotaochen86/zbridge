#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逐个 Zotero profile 做真机验证：拉起 → 测三个端点 → 关闭。

验证项（每个 profile）：
  1. GET  /zbridge/ping            → 200，version=当前构建版本，endpoints 含 3 条
  2. POST /debug-bridge/execute    → 带正确 Bearer token 返回 201
  3. POST /debug-bridge/execute    → 带旧令牌 CTT 返回 401
  4. 旧端点遗留检查                 → /debug-bridge 之外的 debug-bridge 痕迹（跳过）

Zotero 必须是关着的才能开始。脚本会逐个拉起、验证、优雅关闭，结束后把
--restore 指定的 profile（默认 3jqr9vvl.default）重新拉起来，回到用户原来的状态。

用法：
    python test/verify_profiles.py
    python test/verify_profiles.py --restore none     # 验证完不拉起任何 profile
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZOTERO = r"C:\Program Files\Zotero\zotero.exe"
BASE = "http://127.0.0.1:23119"
TOKEN = "a26c69b1050dbdea9bb5d141d109aeaa"
OLD_TOKEN = "CTT"

# 本机所有 HTTP 请求都要绕开环境变量里的代理，否则 127.0.0.1 会被代理吞掉
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method, path, body=None, headers=None, timeout=6):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=body.encode() if isinstance(body, str) else body,
                                 headers=headers or {})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def zotero_procs():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq zotero.exe"],
                         capture_output=True, text=True, errors="ignore").stdout
    return out.lower().count("zotero.exe")


def _ps(script):
    """用 -EncodedCommand 跑 PowerShell：profile 名可能含中文，直接拼命令行会乱码。"""
    import base64
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                          capture_output=True, text=True, errors="ignore")


def close_zotero():
    for _ in range(20):
        if zotero_procs() == 0:
            return True
        _ps("Get-Process zotero -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }")
        time.sleep(1.5)
    subprocess.run(["taskkill", "/F", "/IM", "zotero.exe"], capture_output=True)
    time.sleep(3)
    return zotero_procs() == 0


def wait_port(seconds=60):
    t0 = time.time()
    while time.time() - t0 < seconds:
        st, _ = http("GET", "/zbridge/ping", timeout=2)
        if st == 200:
            return True
        time.sleep(1)
    return False


def _task_name(profile):
    return "ZB_Verify"


def launch(profile):
    """通过计划任务拉起 Zotero。
    直接 Popen 的进程挂在调用方的 job object 里，工具一退出就被一起杀掉；
    计划任务由 Task Scheduler 托管，生命周期独立。"""
    name = _task_name(profile)
    script = (
        "$a = New-ScheduledTaskAction -Execute '%s' -Argument '-P \"%s\"';"
        "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2);"
        "Register-ScheduledTask -TaskName '%s' -Action $a -Trigger $t -Force | Out-Null;"
        "Start-ScheduledTask -TaskName '%s'"
    ) % (ZOTERO, profile, name, name)
    _ps(script)


def drop_task(profile):
    _ps("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false -ErrorAction SilentlyContinue" % _task_name(profile))


def verify(profile):
    with open(os.path.join(ROOT, "src", "manifest.json"), encoding="utf-8") as f:
        want = json.load(f)["version"]

    r = {"profile": profile, "ping": "", "version": "", "endpoints": [], "bearer_ok": None,
         "old_token": None, "echo": "", "checks": {}}

    st, body = http("GET", "/zbridge/ping")
    r["ping"] = st
    if st == 200:
        try:
            j = json.loads(body)
            r["version"] = j.get("version", "")
            r["endpoints"] = j.get("endpoints", [])
        except Exception:
            pass

    st, body = http("POST", "/debug-bridge/execute", body="return 'zbridge-ok'",
                    headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "text/plain"})
    r["bearer_ok"] = st
    r["echo"] = body[:40]

    st2, _ = http("POST", "/debug-bridge/execute", body="return 1",
                  headers={"Authorization": "Bearer " + OLD_TOKEN, "Content-Type": "text/plain"})
    r["old_token"] = st2

    r["checks"] = {
        "ping 200": r["ping"] == 200,
        "版本 = %s" % want: r["version"] == want,
        "三端点齐全": sorted(r["endpoints"]) == sorted(["/zbridge/execute", "/zbridge/ping", "/debug-bridge/execute"]),
        "Bearer 正确令牌 201": r["bearer_ok"] == 201,
        "脚本执行有回显": "zbridge-ok" in r["echo"],
        "旧令牌 CTT 被拒 401": r["old_token"] == 401,
    }
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", default="3jqr9vvl.default")
    ap.add_argument("--only", default="", help="只验某个 profile")
    a = ap.parse_args()

    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    root = os.path.join(appdata, "Zotero", "Zotero", "Profiles")
    profs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(root, "*"))
                   if os.path.isdir(p) and os.path.exists(os.path.join(p, "prefs.js")))
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        profs = [p for p in profs if p in want]

    results = []
    for prof in profs:
        print("=" * 60)
        print("profile:", prof)
        close_zotero()
        launch(prof)
        if not wait_port():
            print("  [FAIL] 端口未就绪，跳过")
            results.append({"profile": prof, "checks": {"端口就绪": False}})
            close_zotero()
            drop_task(prof)
            continue
        r = verify(prof)
        results.append(r)
        print("  version:", r["version"], "| endpoints:", len(r["endpoints"]))
        for k, v in r["checks"].items():
            print(("  PASS  " if v else "  FAIL  ") + k)
        close_zotero()
        drop_task(prof)

    if a.restore and a.restore != "none":
        launch(a.restore)
        wait_port()
        drop_task(a.restore)
        print("\n已恢复到", a.restore)

    print("\n" + json.dumps(results, ensure_ascii=False, indent=1))
    bad = [r for r in results if not all((r.get("checks") or {}).values())]
    print("\n汇总：%d/%d profile 全通过" % (len(results) - len(bad), len(results)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
