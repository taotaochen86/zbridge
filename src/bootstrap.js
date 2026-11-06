/*
 * ZBridge — 让本机网页（如 WPS 加载项）直连 Zotero 的桥
 *
 * 为什么需要它：
 *   Zotero 自带的 HTTP server 对「浏览器来源」的请求有三道拦截（Zotero 10.0.7
 *   chrome/content/zotero/xpcom/server/server.js）：
 *     411-431  带 Origin 的请求在进端点前直接断连接，除非端点声明 allowRequestsFromUnsafeWebContent
 *     280-284  Access-Control-Allow-Origin 只在 origin === zotero.org 时才自动加
 *     369      OPTIONS 在进端点前短路返回、且不带 CORS 头 → 预检必挂
 *   所以 file:// 里跑的加载项既拿不到 CORS 头，又会被断连。这个插件注册一个
 *   自己的端点，把这三件事都自己处理掉，于是不再需要任何本机中继进程。
 *
 * 设计取舍：
 *   - 令牌只走 query（?token=…），因为自定义头会触发 CORS 预检，而预检必挂。
 *   - 响应自带 Access-Control-Allow-Origin: *，浏览器才读得到 body。
 *   - 返回值沿用「字符串套 JSON」两层编码，方便已有客户端原样复用。
 *   - 令牌是唯一防线（ACAO:* + 任意 JS 执行 = 任何网页都能操纵 Zotero），
 *     所以默认 32 位十六进制；可用 prefs 覆盖：extensions.zbridge.token
 */

if (typeof Zotero == 'undefined') {
  var Zotero
}

const ZB_VERSION = '10.0'
const ZB_DEFAULT_TOKEN = 'e3b6034fbdd85456f5ac6df1a4e54ef4'
const ZB_MAX_BODY = 4 * 1024 * 1024
const ZB_ENDPOINTS = ['/zbridge/execute', '/zbridge/ping']

function log(msg) {
  try {
    Zotero.debug('ZBridge: ' + msg)
  }
  catch (e) {
    dump('ZBridge: ' + msg + '\n')
  }
}

function headers(contentType) {
  return {
    'Content-Type': contentType || 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Cache-Control': 'no-store',
  }
}

function token() {
  let v = ''
  try {
    v = Zotero.Prefs.get('zbridge.token') || ''
  }
  catch (e) {}
  return v || ZB_DEFAULT_TOKEN
}

/* POST /zbridge/execute?token=…   body = 裸 JS，须自带 return */
class ExecuteEndpoint {
  constructor() {
    this.supportedMethods = ['POST']
    this.supportedDataTypes = ['text/plain', 'application/javascript']
    // 关键：不设这一项，带 Origin 的请求会在进端点前被 server.js 断掉
    this.allowRequestsFromUnsafeWebContent = true
  }

  async init(request) {
    let found = ''
    try {
      found = request.searchParams ? (request.searchParams.get('token') || '') : ''
    }
    catch (e) {}
    if (!found) return [401, headers('text/plain'), 'token required']
    if (found !== token()) return [401, headers('text/plain'), 'invalid token']

    let code = request.data
    if (typeof code !== 'string' || !code.trim()) {
      return [400, headers('text/plain'), 'empty body']
    }
    if (code.length > ZB_MAX_BODY) {
      return [413, headers('text/plain'), 'body too large']
    }

    const start = Date.now()
    try {
      const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
      const action = new AsyncFunction(code)
      let result = await action()
      if (typeof result === 'undefined') result = null
      const body = JSON.stringify(result)
      log(`exec ok (${Date.now() - start}ms, ${code.length}B)`)
      return [200, headers(), body]
    }
    catch (err) {
      log(`exec failed: ${err}`)
      return [500, headers('text/plain'), `zbridge failed: ${err}\n${err && err.stack ? err.stack : ''}`]
    }
  }
}

/* GET /zbridge/ping —— 只回版本，不要求令牌，方便排障
   注意：这里必须声明一个形参。Zotero 按 init.length 决定调用方式：
   0 个参数 → 走三参数回调形式，返回值会被丢弃（连接直接空响应）。 */
class PingEndpoint {
  constructor() {
    this.supportedMethods = ['GET']
    this.allowRequestsFromUnsafeWebContent = true
  }

  init(request) {
    let zv = ''
    try {
      zv = Zotero.version
    }
    catch (e) {}
    return [200, headers(), JSON.stringify({
      name: 'ZBridge',
      version: ZB_VERSION,
      zotero: zv,
      endpoints: ZB_ENDPOINTS,
    })]
  }
}

function register() {
  Zotero.Server.Endpoints['/zbridge/execute'] = ExecuteEndpoint
  Zotero.Server.Endpoints['/zbridge/ping'] = PingEndpoint
  log(`registered ${ZB_ENDPOINTS.join(', ')}`)
}

function unregister() {
  for (const path of ZB_ENDPOINTS) {
    try {
      delete Zotero.Server.Endpoints[path]
    }
    catch (e) {}
  }
}

async function startup({ id, version, rootURI }, reason) {
  log(`startup ${version || ''} (${reason})`)
  try {
    if (Zotero.initializationPromise) await Zotero.initializationPromise
  }
  catch (e) {
    log('initializationPromise rejected: ' + e)
  }
  register()
}

function shutdown(data, reason) {
  log('shutdown')
  unregister()
}

function install() {}

function uninstall() {}
