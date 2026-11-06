# ZBridge

> 让本机网页（WPS / Office 加载项、file:// 页面、本地 Web 应用）直连 Zotero，**不需要任何中继进程**。

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

## 安装

1. 下载 [`zbridge-10.0.xpi`](../../releases/latest)（或自己 `python build.py` 生成）
2. Zotero → 工具 → 插件 → 右上角齿轮 → **Install Plugin From File…**
3. 重启 Zotero（工具 → 插件 里应能看到 ZBridge 10.0）

> 插件未签名。Zotero 默认 `xpinstall.signatures.required=false`，可以直接装。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/zbridge/ping` | 返回 `{name, version, zotero, endpoints}`，不需要令牌 |
| POST | `/zbridge/execute?token=…` | body 是裸 JS（须自带 `return`），返回值 JSON 序列化 |

示例：

```bash
curl -s "http://127.0.0.1:23119/zbridge/ping"

curl -s -X POST "http://127.0.0.1:23119/zbridge/execute?token=e3b6034fbdd85456f5ac6df1a4e54ef4" \
  -H "Content-Type: text/plain" \
  --data-binary 'return JSON.stringify({ version: Zotero.version })'
```

返回值沿用 Debug Bridge 的「字符串套 JSON」两层编码，方便已有客户端原样复用。

## 令牌

默认令牌写在 `src/bootstrap.js` 的 `ZB_DEFAULT_TOKEN`，可用 prefs 覆盖：

```
extensions.zbridge.token
```

## 安全提醒

`Access-Control-Allow-Origin: *` + 任意 JS 执行 = **任何网页都能操纵你的 Zotero**（读文献库、改文件）。
令牌是唯一防线，所以默认是 32 位十六进制随机串，不要改成短字符串或可猜的值。

如果只想给自己用，建议保持默认值并**不要把它写进公开仓库的客户端代码**里。

## 构建

```bash
python build.py            # 生成 dist/zbridge-<版本>.xpi 与 update.json
python build.py --probe     # 另跑 5 组连通性验证（Zotero 需在运行且插件已装）
python build.py --install   # 拷进 Zotero profile 的 extensions/（需先退出 Zotero）
```

## 与 Zotero Debug Bridge 的关系

**没有代码关系。** ZBridge 是独立实现，只依赖 Zotero 公开的 `Zotero.Server.Endpoints` 注册机制。
可以与本机已有的 Debug Bridge 并存（端点路径不同），互不影响。

## License

MIT
