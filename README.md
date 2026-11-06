# ZBridge

> 让本机网页（WPS / Office 加载项、file:// 页面、本地 Web 应用）直连 Zotero，**不需要任何中继进程**。

[中文](#安装详细步骤) · [English](#english)

Zotero 自带的 HTTP server 只对「原生程序」友好，对浏览器来源有三道拦截。ZBridge 注册一个自己的端点，
把这三件事自己处理掉，于是 `file://` 里的加载项终于能直接调 Zotero。

## 为什么需要它

Zotero 10.0.7 的 `chrome/content/zotero/xpcom/server/server.js` 里：

| 行 | 行为 |
|---|---|
| 411-431 | 带 `Origin` 的请求在进入端点前直接断连接，**除非端点声明 `allowRequestsFromUnsafeWebContent`** |
| 280-284 | `Access-Control-Allow-Origin` 只在 `origin === zotero.org` 时才自动加 |
| 369 | `OPTIONS` 在进入端点前短路返回 200，且不带任何 CORS 头 → 预检必挂 |

所以浏览器发的跨源请求：要么被断连，要么读不到响应，要么预检失败。ZBridge 的对策：

- 端点声明 `allowRequestsFromUnsafeWebContent = true`
- 响应自己带 `Access-Control-Allow-Origin: *`
- **令牌走 query（`?token=`），不用自定义头** —— 这样是「简单请求」，不触发预检

## 安装（详细步骤）

### 0. 前置条件

| 项 | 要求 | 怎么确认 |
|---|---|---|
| Zotero | **7.0 及以上，不设上限**（`strict_min_version` 7.0，`strict_max_version` `*`）。Zotero 10.* 全系列（10.0 / 10.0.x / 10.1 / 10.5 / 10.99…）均可安装使用 | Zotero 菜单「帮助 → 关于 Zotero」 |
| 端口 | 默认 `23119`，由 Zotero 自己的 HTTP server 提供 | 见下面第 1 步 |
| 权限 | 普通用户即可，**不需要管理员权限** | —— |
| 网络 | 不需要。插件完全在本机跑，不联网 | —— |

ZBridge 是 Zotero 的插件，装的是 `.xpi` 文件，跟 Zotero 本体一起启动，**没有独立进程、不占额外端口**。

### 0.1 支持哪些 Zotero 版本

清单里写的是 `strict_min_version: "7.0"`、`strict_max_version: "*"`，也就是**7.0 以上不设上限**：

| Zotero 版本 | 是否支持 | 说明 |
|---|---|---|
| 7.0 – 7.0.x | ✅ 可安装 | 端点契约与 10 相同 |
| 10.0 – 10.0.x | ✅ 可安装，已实测 | 当前开发环境为 10.0.7 |
| 10.1 / 10.2 / 10.5 / 10.9 / 10.99 … | ✅ 可安装 | 10.x 全系列共用同一套 HTTP server 与端点 API，代码里没有任何版本分支 |
| 11 及以后 | ✅ 可安装（不设上限） | 若端点 API 变了，插件启动时会探测失败并**只写日志、不注册**，不会拖垮 Zotero 启动 |

**为什么能做到跨版本**：ZBridge 只用了三样东西 —— `Zotero.Server.Endpoints` 注册表、
端点的 `init(request)` 返回 `[status, headers, body]` 的契约、`allowRequestsFromUnsafeWebContent` 开关。
这三样从 Zotero 7 到 10 一直没变。插件没有硬编码任何版本号判断。

对旧形态也做了兜底，不是只按 10.0.7 写死：

| 差异点 | 兼容做法 |
|---|---|
| query 参数的载体：新版本是 `request.searchParams`（URL 对象），更早是 `request.query`（普通对象） | `queryParam()` 两种都试，前者抛异常自动降级 |
| 请求体类型：通常是 UTF-8 字符串，个别情况是字节数组 | `requestBody()` 用 `TextDecoder` 转码 |
| `Zotero.initializationPromise` 可能不存在 | `startup()` 里判空，缺失时直接注册 |
| `Zotero.Server.Endpoints` 可能不存在（未来大改） | `serverReady()` 先探测，不满足就返回 false 并跳过，**绝不抛异常** |

**插件版本号与 Zotero 版本号无关，是插件自己的递增序号：当前 `10.1.4`。**
每次改动（改代码、改文档、重构包）都把最后一位加一 → `10.1.5`、`10.1.6`……
版本号只影响「Zotero 认为是新版还是旧版」，与「支持哪个 Zotero 版本」毫无关系。

> 早期版本叫 `10.0`，是为了跟 Zotero 主版本对齐，结果常被误读成「只配 Zotero 10.0 用」。
> 现在 `10.1.x` 只表示插件自身的迭代次数，Zotero 从 10.0 升到 10.5 不需要动插件版本。

自查命令（Zotero 开着时）：

```bash
curl -s "http://127.0.0.1:23119/zbridge/ping"
```

返回里的 `zotero` 是你本机版本，`minZotero` 是插件要求的最低版本，`version` 是插件版本。

### 1. 先确认 Zotero 的 HTTP server 在跑

Zotero 启动后，用浏览器或命令行访问：

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:23119/"
```

返回 `404` 是**正常**的 —— Zotero 的 HTTP server 对根路径没有处理程序，只要 curl 能拿到状态码就说明服务在监听。
只有「连接被拒绝」（curl 退出码 7）才说明 Zotero 没开，或者端口被改过（检查 Zotero 配置编辑器里的
`extensions.zotero.httpServer.port`）。

> 装 ZBridge **之前**，`http://127.0.0.1:23119/zbridge/ping` 应该返回 404 —— 这是正常的，说明端点还没注册。

### 2. 拿到 xpi

三种来源，任选一种：

- **发布页下载**：<https://github.com/taotaochen86/zbridge/releases/latest> → `zbridge-10.1.4.xpi`
- **本地已构建**：直接用仓库里的 `dist/zbridge-10.1.4.xpi`
- **自己构建**：`python build.py`（见文末「构建」，需要 Python 3.8+）

> 文件名里的 `10.1.4` 是**插件自己的版本号，与 Zotero 版本号无关**（每次改动递增一位），
> 不要因为它不是 `10.x` 就以为装错了。

> 不要解压 xpi。它就是一个 zip 包，交给 Zotero 处理。

### 3. 在 Zotero 里安装（推荐方式）

1. 打开 Zotero → 顶部菜单 **工具 → 插件**
2. 插件窗口右上角点 **齿轮图标** ⚙
3. 选 **Install Plugin From File…**（中文界面：「从文件安装插件…」）
4. 选中 `zbridge-10.1.4.xpi` → 打开
5. Zotero 会弹一个确认框（提示插件未签名），点 **Install / 安装**
6. Zotero 提示需要重启，点 **Restart Now / 立即重启**（或手动关掉 Zotero 再打开）

**备选：拖拽安装。** 把 `zbridge-10.1.4.xpi` 直接拖到「工具 → 插件」窗口里，效果相同。

安装成功后，插件列表里应出现一行：

```
ZBridge  10.1.4
```

并且**是启用状态**（右侧开关为开、或没有「已禁用」标记）。如果显示为禁用，点一下开关启用，再重启 Zotero。

### 4. 命令行安装（可选，适合批量/无界面环境）

```bash
python build.py --install
```

它会把 xpi 拷进每个 Zotero profile 的 `extensions/` 目录，然后需要**重启 Zotero**。

> ⚠️ 侧载（直接拷文件）有可能被 Zotero 的 `extensions.autoDisableScopes` 判为「外部来源」而自动禁用。
> 所以**首次安装优先用第 3 步的图形界面方式**；命令行方式更适合升级已有安装。

### 5. 安装后验证（三步，全过才算装好）

先把令牌放进变量，后面三条命令都用它（**没改过令牌就用下面这个默认值；改过就换成你自己的**）：

```bash
TOKEN=e3b6034fbdd85456f5ac6df1a4e54ef4
```

**① 端点活着**（不需要令牌）

```bash
curl -s "http://127.0.0.1:23119/zbridge/ping"
```

期望返回：

```json
{"name":"ZBridge","version":"10.1.4","zotero":"10.0.7","minZotero":"7.0","compat":"Zotero 7.0+ (verified on 10.0.7)","endpoints":["/zbridge/execute","/zbridge/ping"]}
```

其中 `zotero` 是**你本机实际的 Zotero 版本**（这里以 10.0.7 举例，你的可能是 10.1、10.5…），
`version` 是插件版本。两者无关，不要拿它们互相比对。

**② 能执行 JS**（带令牌）

```bash
curl -s -X POST "http://127.0.0.1:23119/zbridge/execute?token=$TOKEN" \
  -H "Content-Type: text/plain" \
  --data-binary 'return 1+1'
```

期望返回 `2`。

**③ 模拟浏览器来源**（这一步才是 ZBridge 存在的意义）

```bash
curl -s -X POST "http://127.0.0.1:23119/zbridge/execute?token=$TOKEN" \
  -H "Content-Type: text/plain" -H "Origin: null" \
  --data-binary 'return Zotero.version'
```

期望返回 `"10.0.7"`（带引号，因为返回值被再序列化一层）。**如果这一步返回空、或 curl 报 `Connection reset`，说明端点没生效**，回到第 3 步检查插件是否已启用。

也可以一条命令跑完全部检查：

```bash
python build.py --probe
```

会依次验证 ping、带 Origin 执行、无令牌 401、错令牌 401、脚本报错 500，外加一条
「本机 Zotero 版本是否落在清单声明范围内」，输出 `6/6 通过` 即正常。
**如果你改过令牌，要先告诉它**，否则那三条带令牌的用例会 401：

```bash
# Windows Git Bash / macOS / Linux
ZB_TOKEN=你的令牌 python build.py --probe

# Windows CMD
set ZB_TOKEN=你的令牌 && python build.py --probe

# PowerShell
$env:ZB_TOKEN="你的令牌"; python build.py --probe
```

### 6. 升级

- **自动**：`manifest.json` 里有 `update_url`，指向仓库的 `update.json`。但国内访问 `raw.githubusercontent.com`
  经常不通，实际很少生效。
- **手动（推荐）**：重新下载新版 xpi，重复第 3 步；Zotero 会覆盖同 ID 插件，配置与令牌都保留。

> Zotero 判断升级只看 `version`。版本号不涨，覆盖安装等于没升级。
> 所以本项目约定：**每次改动都递增版本号**（`10.1.4` → `10.1.5` → …），哪怕只改了一行文档。

### 7. 卸载

Zotero → **工具 → 插件** → 选中 **ZBridge** → 点 **移除 / Remove** → 重启 Zotero。

卸载后 `/zbridge/*` 端点立即消失，依赖它的客户端（如 WPS 加载项）会报连接失败，这是预期行为。

### 8. 安装故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 齿轮菜单里没有 Install Plugin From File… | Zotero 版本太老（< 7） | 升级 Zotero |
| 选文件后提示「无法安装，此插件与此版本不兼容」 | `strict_max_version` 不匹配 | 装对应版本的 xpi，或改 `manifest.json` 里的版本区间后重新打包 |
| 装完插件列表里没有 ZBridge | 装到了别的 profile | Zotero 菜单「帮助 → 故障排除 → 打开配置文件夹」，确认当前 profile，再装一次 |
| 插件列表里有，但显示已禁用 | 被 `autoDisableScopes` 判为侧载 | 手动点开关启用，重启 |
| `/zbridge/ping` 仍是 404 | 端点未注册：插件没启用 / 没重启 | 确认插件启用后**完全退出 Zotero**再打开 |
| `/zbridge/ping` 连接被拒 | Zotero 没运行，或端口不是 23119 | 启动 Zotero；查 `extensions.zotero.httpServer.port` |
| `execute` 返回 401 `token required` | URL 里没带 `?token=` | 补上令牌 |
| `execute` 返回 401 `invalid token` | 令牌与 Zotero 里的 pref 不一致 | 见下面「令牌」一节 |
| `execute` 返回 500 | 你提交的 JS 自己抛异常了 | 看响应里的报错栈，是脚本问题，不是插件问题 |

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/zbridge/ping` | 返回 `{name, version, zotero, minZotero, compat, endpoints}`，不需要令牌 |
| POST | `/zbridge/execute?token=…` | body 是裸 JS（须自带 `return`），返回值 JSON 序列化 |

示例：

```bash
curl -s "http://127.0.0.1:23119/zbridge/ping"

curl -s -X POST "http://127.0.0.1:23119/zbridge/execute?token=e3b6034fbdd85456f5ac6df1a4e54ef4" \
  -H "Content-Type: text/plain" \
  --data-binary 'return JSON.stringify({ version: Zotero.version })'
```

返回值是「字符串套 JSON」两层编码（历史协议），方便已有客户端原样复用。

## 令牌

令牌是唯一鉴权手段：`/zbridge/execute` 必须带 `?token=`，`/zbridge/ping` 不校验（只回版本，方便排障）。

默认令牌写在 `src/bootstrap.js` 的 `ZB_DEFAULT_TOKEN`。不改代码也能覆盖，用 Zotero 的配置编辑器：

1. Zotero → **编辑 → 设置 → 高级** → 拉到底部「配置编辑器」→ 点「我了解风险」
2. 搜索框输入 `zbridge.token`
3. 没有就点「+」新增：
   - 类型选 **String**
   - 名称填 `extensions.zotero.zbridge.token`
   - 值填你自己的令牌（建议 ≥32 位随机串，例如 `openssl rand -hex 16` 的输出）
4. 点「确定」保存

**立即生效，不需要重启 Zotero** —— 端点每次请求都会重新读这个 pref。改完后可以马上验证：

```bash
# 新令牌 → 200
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "http://127.0.0.1:23119/zbridge/execute?token=你的新令牌" \
  -H "Content-Type: text/plain" --data-binary 'return 1'

# 旧默认令牌 → 401（已被覆盖，不是追加）
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "http://127.0.0.1:23119/zbridge/execute?token=e3b6034fbdd85456f5ac6df1a4e54ef4" \
  -H "Content-Type: text/plain" --data-binary 'return 1'
```

> ⚠️ 前缀必须是 `extensions.zotero.`。
> `Zotero.Prefs.get('zbridge.token')` 会自动补上 `extensions.zotero.`，
> 所以真正生效的是 `extensions.zotero.zbridge.token`；写成 `extensions.zbridge.token`
> **不生效**（2026-11-06 实测：前者 200、后者 401）。
>
> 也可以直接改 profile 里的 `prefs.js`（Zotero 需先退出），加一行：
> ```js
> user_pref("extensions.zotero.zbridge.token", "你的新令牌");
> ```

**改了令牌之后，所有客户端都要同步**，否则请求一律 401。例如 WPS 加载项
`zotero-bridge.js` 里的 `webToken` 字段要改成同一个值，然后重启 WPS
（加载项脚本缓存在内存里，不重启仍用旧令牌）。

## 安全提醒

`Access-Control-Allow-Origin: *` + 任意 JS 执行 = **任何网页都能操纵你的 Zotero**（读文献库、改文件）。
令牌是唯一防线，所以默认是 32 位十六进制随机串，不要改成短字符串或可猜的值。

如果只想给自己用，建议保持默认值并**不要把它写进公开仓库的客户端代码**里。

## 构建

```bash
python build.py             # 生成 dist/zbridge-<版本>.xpi 与 update.json（含 sha256）
python build.py --bump      # 先把版本号末位 +1（manifest 与 bootstrap 同步），再构建
python build.py --test      # 先跑离线单测（node test/unit.js，不需要 Zotero），通过才构建
python build.py --probe     # 另跑 6 组连通性验证（Zotero 需在运行且插件已装；改过令牌要设 ZB_TOKEN）
python build.py --install   # 拷进 Zotero profile 的 extensions/（需先退出 Zotero）
```

**版本号约定：每次改动都递增。** 日常就一条命令：

```bash
python build.py --bump --test --probe
```

`--bump` 会把 `10.1.4` 改成 `10.1.5`，并同时写进 `src/manifest.json` 的 `version` 和
`src/bootstrap.js` 的 `ZB_VERSION`。两处必须一致，不一致时构建会直接报错退出。

`build.py` 会读 `src/manifest.json` 的版本号与兼容范围，把 xpi 的 sha256 写进 `update.json`
（自动更新用），构建时打印一行 `兼容 Zotero 7.0 ~ 不限`。改完 `src/` 里任何文件都要重新打包，
否则装进去的还是旧代码。

### 离线单测

```bash
node test/unit.js
```

用 `vm` 造一个假 Zotero，把端点契约逐条验一遍：令牌读取（含错误前缀不生效）、
`searchParams` 与 `query` 两种 request 形态、UTF-8 字节数组请求体、
401/400/500 各分支、`register()` 在 `Zotero.Server` 缺失时是否安全降级、`startup` 是否完成注册。
共 45 条断言，不需要 Zotero 在运行。

## License

MIT

---

# English

> A browser-friendly local bridge for Zotero: lets local web pages (`file://` add-ins, WPS/Office add-ins, local web apps) talk to Zotero **without any relay process**.

[中文](#安装详细步骤) · [English](#installation-step-by-step)

Zotero's built-in HTTP server is friendly to native programs but puts three obstacles in front of browser-originated requests. ZBridge registers its own endpoint and handles all three, so an add-in running from `file://` can finally call Zotero directly.

## Why it exists

In Zotero 7–10, `chrome/content/zotero/xpcom/server/server.js` (line numbers per 10.0.7):

| Lines | Behaviour |
|---|---|
| 411-431 | Requests carrying an `Origin` header are disconnected before reaching the endpoint — **unless the endpoint sets `allowRequestsFromUnsafeWebContent`** |
| 280-284 | `Access-Control-Allow-Origin` is added automatically only when `origin === zotero.org` |
| 369 | `OPTIONS` short-circuits before the endpoint and returns no CORS headers at all → preflight always fails |

So a cross-origin request from a browser is either dropped, unreadable, or blocked by preflight. ZBridge works around it by:

- setting `allowRequestsFromUnsafeWebContent = true` on its endpoint
- returning `Access-Control-Allow-Origin: *` itself
- **sending the token as a query parameter (`?token=`), never a custom header** — this keeps the request "simple", so no preflight is triggered

## Installation (step by step)

### 0. Requirements

| Item | Requirement | How to check |
|---|---|---|
| Zotero | **7.0 or newer, no upper bound** (`strict_min_version` 7.0, `strict_max_version` `*`). Every Zotero 10.* release (10.0 / 10.0.x / 10.1 / 10.5 / 10.99 …) installs and works | Zotero menu **Help → About Zotero** |
| Port | `23119` by default, served by Zotero's own HTTP server | See step 1 below |
| Privileges | A normal user account. **Administrator rights are not needed** | — |
| Network | None. Everything runs locally | — |

ZBridge is a Zotero add-on shipped as an `.xpi`. It starts and stops with Zotero — **no separate process, no extra port**.

### 0.1 Supported Zotero versions

The manifest declares `strict_min_version: "7.0"` and `strict_max_version: "*"` — that is, **7.0 and up, with no upper bound**:

| Zotero version | Supported | Notes |
|---|---|---|
| 7.0 – 7.0.x | ✅ installs | Same endpoint contract as 10 |
| 10.0 – 10.0.x | ✅ installs, tested | Development environment is 10.0.7 |
| 10.1 / 10.2 / 10.5 / 10.9 / 10.99 … | ✅ installs | All 10.x share the same HTTP server and endpoint API; there is not a single version branch in the code |
| 11 and later | ✅ installs (no upper bound) | If the endpoint API ever changes, startup probes for it, logs a message and skips registration — it will not break Zotero's startup |

**Why it works across versions.** ZBridge relies on exactly three things: the `Zotero.Server.Endpoints`
registry, the `init(request)` → `[status, headers, body]` contract, and the `allowRequestsFromUnsafeWebContent`
flag. Those three have been unchanged from Zotero 7 through 10. The add-on contains no hard-coded version checks.

Older shapes are handled too, rather than assuming 10.0.7:

| Difference | How it is handled |
|---|---|
| Query params: newer versions expose `request.searchParams` (a URL object), older ones `request.query` (a plain object) | `queryParam()` tries both, falling back automatically if the first throws |
| Request body: normally a UTF-8 string, occasionally a byte array | `requestBody()` decodes it with `TextDecoder` |
| `Zotero.initializationPromise` may be absent | `startup()` guards for it and registers regardless |
| `Zotero.Server.Endpoints` may be absent (a future rewrite) | `serverReady()` probes first; if missing, registration is skipped and **nothing is thrown** |

**The add-on version is not tied to the Zotero version — it is the add-on's own incrementing counter (currently `10.1.4`).**
Every change (code, docs, rebuilt package) bumps the last digit → `10.1.5`, `10.1.6`, and so on.
The version only tells Zotero whether this is newer or older; it says nothing about which Zotero versions are supported.

> The old `10.0` was chosen to mirror Zotero's major version, which led people to read it as "only for Zotero 10.0".
> `10.1.x` now just counts add-on iterations — Zotero going from 10.0 to 10.5 needs no add-on version change.

Quick self-check (with Zotero running):

```bash
curl -s "http://127.0.0.1:23119/zbridge/ping"
```

`zotero` is your local Zotero version, `minZotero` the minimum this add-on requires, `version` the add-on version.

### 1. Make sure Zotero's HTTP server is up

With Zotero running, hit it from a browser or a terminal:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:23119/"
```

A `404` here is **normal** — Zotero's HTTP server has no handler for the root path, so any status code at all means the
server is listening. Only "connection refused" (curl exit code 7) means Zotero is not running, or the port was changed —
check `extensions.zotero.httpServer.port` in the Zotero Config Editor.

> **Before** installing ZBridge, `http://127.0.0.1:23119/zbridge/ping` should return 404. That is expected: the endpoint does not exist yet.

### 2. Get the .xpi

Pick one:

- **Download a release**: <https://github.com/taotaochen86/zbridge/releases/latest> → `zbridge-10.1.4.xpi`
- **Use the built artefact**: `dist/zbridge-10.1.4.xpi` in this repository
- **Build it yourself**: `python build.py` (Python 3.8+, see "Building" below)

> The `10.1.4` in the filename is the **add-on's own version, unrelated to the Zotero version** (bumped on every
> change). Do not assume you grabbed the wrong file just because it does not match your Zotero version.

> Do not unzip the `.xpi`. It is a zip archive, but Zotero must receive it as-is.

### 3. Install from the Zotero UI (recommended)

1. Open Zotero → menu **Tools → Plugins**
2. Click the **gear icon** ⚙ in the top-right corner of the Plugins window
3. Choose **Install Plugin From File…**
4. Select `zbridge-10.1.4.xpi` → Open
5. Zotero shows a confirmation dialog (the add-on is unsigned) → click **Install**
6. Zotero asks to restart → click **Restart Now** (or quit Zotero completely and start it again)

**Alternative: drag and drop.** Drop `zbridge-10.0.xpi` straight onto the **Tools → Plugins** window. Same result.

After a successful install the list shows one entry:

```
ZBridge  10.1.4
```

It must be **enabled** (toggle on, no "disabled" marker). If it appears disabled, enable it and restart Zotero.

### 4. Command-line install (optional, for scripted or headless setups)

```bash
python build.py --install
```

This copies the xpi into the `extensions/` folder of every Zotero profile. **Restart Zotero afterwards.**

> ⚠️ Sideloading a brand-new add-on this way can be treated as an external source and auto-disabled by Zotero's `extensions.autoDisableScopes`. **Prefer the UI method in step 3 for a first install**; the CLI path is convenient for upgrades.

### 5. Verify (three checks — all must pass)

Put the token in a variable first; the next three commands use it (**keep the default below if you never changed it;
otherwise substitute your own**):

```bash
TOKEN=e3b6034fbdd85456f5ac6df1a4e54ef4
```

**① The endpoint is alive** (no token needed)

```bash
curl -s "http://127.0.0.1:23119/zbridge/ping"
```

Expected:

```json
{"name":"ZBridge","version":"10.0","zotero":"10.0.7","endpoints":["/zbridge/execute","/zbridge/ping"]}
```

**② It can execute JS** (with the token)

```bash
curl -s -X POST "http://127.0.0.1:23119/zbridge/execute?token=$TOKEN" \
  -H "Content-Type: text/plain" \
  --data-binary 'return 1+1'
```

Expected: `2`.

**③ Simulate a browser origin** (this is the whole point of ZBridge)

```bash
curl -s -X POST "http://127.0.0.1:23119/zbridge/execute?token=$TOKEN" \
  -H "Content-Type: text/plain" -H "Origin: null" \
  --data-binary 'return Zotero.version'
```

Expected: `"10.0.7"` (quoted, because the return value is serialised once more). **If this returns an empty body or curl
reports `Connection reset`, the endpoint is not active** — go back to step 3 and check that the add-on is enabled.

Or run every check at once:

```bash
python build.py --probe
```

It verifies ping, execution with an `Origin` header, no-token 401, wrong-token 401 and script-error 500.
`5/5 passed` means everything works. **If you changed the token, tell it first**, otherwise the three token-bearing
cases return 401:

```bash
# Git Bash / macOS / Linux
ZB_TOKEN=your-token python build.py --probe

# Windows CMD
set ZB_TOKEN=your-token && python build.py --probe

# PowerShell
$env:ZB_TOKEN="your-token"; python build.py --probe
```

### 6. Upgrading

- **Automatic**: `manifest.json` declares an `update_url` pointing at the repository's `update.json`. In practice this rarely fires, since `raw.githubusercontent.com` is unreachable from many networks.
- **Manual (recommended)**: download the new xpi and repeat step 3. Zotero replaces the add-on with the same ID; your configuration and token are preserved.

> Zotero only upgrades when `version` increases. Reinstalling the same version is a no-op.
> Project convention: **bump the version on every change** (`10.1.4` → `10.1.5` → …), even for a one-line docs edit.

### 7. Uninstalling

Zotero → **Tools → Plugins** → select **ZBridge** → **Remove** → restart Zotero.

The `/zbridge/*` endpoints disappear immediately; any client relying on them (the WPS add-in, for example) will start reporting connection failures. That is expected.

### 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No "Install Plugin From File…" in the gear menu | Zotero older than 7 | Upgrade Zotero |
| "This add-on is not compatible with this version" | `strict_max_version` mismatch | Install a matching xpi, or widen the range in `manifest.json` and rebuild |
| ZBridge missing from the Plugins list after install | Installed into a different profile | Zotero menu **Help → Troubleshooting → Open Profile Folder** to see the active profile, then reinstall |
| Listed but marked disabled | Auto-disabled as a sideloaded add-on | Toggle it on, then restart |
| `/zbridge/ping` still 404 | Endpoint not registered: add-on disabled or Zotero not restarted | Enable the add-on, **quit Zotero completely**, start again |
| `/zbridge/ping` connection refused | Zotero not running, or port is not 23119 | Start Zotero; check `extensions.zotero.httpServer.port` |
| `execute` returns 401 `token required` | No `?token=` in the URL | Append the token |
| `execute` returns 401 `invalid token` | Token does not match the Zotero pref | See "Token" below |
| `execute` returns 500 | Your own JS threw an exception | Read the stack trace in the response — a script problem, not an add-on problem |

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/zbridge/ping` | Returns `{name, version, zotero, endpoints}`. No token required |
| POST | `/zbridge/execute?token=…` | Body is raw JS (must `return`); the result is JSON-serialised |

```bash
curl -s "http://127.0.0.1:23119/zbridge/ping"

curl -s -X POST "http://127.0.0.1:23119/zbridge/execute?token=e3b6034fbdd85456f5ac6df1a4e54ef4" \
  -H "Content-Type: text/plain" \
  --data-binary 'return JSON.stringify({ version: Zotero.version })'
```

Responses use the historical "JSON inside a JSON string" double encoding, so existing clients can consume them unchanged.

## Token

The token is the only authentication mechanism. `/zbridge/execute` requires `?token=`; `/zbridge/ping` does not check it (it only reports the version, to help with diagnostics).

The default lives in `ZB_DEFAULT_TOKEN` in `src/bootstrap.js`. You can override it without touching code, using Zotero's Config Editor:

1. Zotero → **Edit → Settings → Advanced** → scroll to **Config Editor** → click **I accept the risk**
2. Search for `zbridge.token`
3. If absent, click **+** and add:
   - Type: **String**
   - Name: `extensions.zotero.zbridge.token`
   - Value: your own token (32+ random characters recommended, e.g. the output of `openssl rand -hex 16`)
4. Click OK to save

**It takes effect immediately — no restart needed**, because the endpoint re-reads the pref on every request. Verify right away:

```bash
# New token → 200
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "http://127.0.0.1:23119/zbridge/execute?token=YOUR_NEW_TOKEN" \
  -H "Content-Type: text/plain" --data-binary 'return 1'

# Old default token → 401 (it is overridden, not appended)
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "http://127.0.0.1:23119/zbridge/execute?token=e3b6034fbdd85456f5ac6df1a4e54ef4" \
  -H "Content-Type: text/plain" --data-binary 'return 1'
```

> ⚠️ The `extensions.zotero.` prefix is mandatory.
> `Zotero.Prefs.get('zbridge.token')` prepends it automatically, so the effective name is
> `extensions.zotero.zbridge.token`. Writing `extensions.zbridge.token` **does not work**
> (measured 2026-11-06: the former returns 200, the latter 401).
>
> You can also edit `prefs.js` in your profile (Zotero must be closed first):
> ```js
> user_pref("extensions.zotero.zbridge.token", "YOUR_NEW_TOKEN");
> ```

**Every client must be updated after changing the token**, otherwise all requests return 401. For the WPS add-in, change `webToken` in `zotero-bridge.js` to the same value and restart WPS (add-in scripts are cached in memory).

## Security

`Access-Control-Allow-Origin: *` plus arbitrary JS execution means **any web page can drive your Zotero** (read your library, modify files). The token is the only line of defence, which is why the default is a 32-character hex string. Do not weaken it to something short or guessable.

If you only use this yourself, keep the default and **do not commit your token into client code in a public repository**.

## Building

```bash
python build.py             # produces dist/zbridge-<version>.xpi and update.json (with sha256)
python build.py --bump      # bumps the last version digit (manifest + bootstrap kept in sync), then builds
python build.py --test      # runs the offline unit tests first (node test/unit.js, no Zotero needed)
python build.py --probe     # additionally runs 6 connectivity checks (Zotero running, add-on installed; set ZB_TOKEN if you changed the token)
python build.py --install   # copies into the Zotero profile's extensions/ (quit Zotero first)
```

**Convention: bump the version on every change.** In practice that is one command:

```bash
python build.py --bump --test --probe
```

`--bump` turns `10.1.4` into `10.1.5` and writes it to both `src/manifest.json` (`version`) and
`src/bootstrap.js` (`ZB_VERSION`). If the two disagree, the build aborts instead of shipping a mismatch.

### Offline unit tests

```bash
node test/unit.js
```

A fake Zotero is built with `vm`, then the endpoint contract is exercised: token lookup (including the
wrong prefix correctly having no effect), both `searchParams` and `query` request shapes, UTF-8 byte-array
bodies, the 401/400/500 branches, `register()` degrading safely when `Zotero.Server` is missing, and
`startup` completing registration. 45 assertions, no running Zotero required.

## License

MIT

