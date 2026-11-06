/*
 * ZBridge — 让本机网页（如 WPS 加载项）直连 Zotero 的桥
 *
 * 为什么需要它：
 *   Zotero 自带的 HTTP server 对「浏览器来源」的请求有三道拦截（行号以 10.0.7 的
 *   chrome/content/zotero/xpcom/server/server.js 为准，7.x–10.x 各版本逻辑一致）：
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
 *     所以默认 32 位十六进制；可用 prefs 覆盖。
 *     注意：Zotero.Prefs.get('zbridge.token') 会自动加 extensions.zotero. 前缀，
 *     所以真正的 pref 名是 extensions.zotero.zbridge.token（实测：写成
 *     extensions.zbridge.token 不生效）。
 */

if (typeof Zotero == 'undefined') {
  var Zotero
}

const ZB_VERSION = '10.1.5'

/* 兼容范围：Zotero 7.0 起 Zotero.Server.Endpoints 的端点契约（init 返回
   [status, headers, body]、request.searchParams、allowRequestsFromUnsafeWebContent）
   就稳定下来了，Zotero 10.* 全系列沿用同一套，无需任何版本分支。
   插件版本号（10.1.x）是独立递增的序号：每次改动都要加一，与 Zotero 版本号无关。 */
const ZB_MIN_ZOTERO = '7.0'
const ZB_COMPAT_NOTE = 'Zotero 7.0+ (verified on 10.0.7)'
const ZB_DEFAULT_TOKEN = 'e3b6034fbdd85456f5ac6df1a4e54ef4'
const ZB_MAX_BODY = 4 * 1024 * 1024
const ZB_ENDPOINTS = ['/zbridge/execute', '/zbridge/ping', '/debug-bridge/execute']

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

/* 令牌读取。Zotero.Prefs.get(name) 会自动补 extensions.zotero. 前缀，为了不受
   这个规则影响，这里用第二参数 true 按「完整名」直接读，与 README 写的名字严格一致
   （extensions.zotero.zbridge.token）。写成 extensions.zbridge.token 不会生效。 */
function token() {
  try {
    const v = Zotero.Prefs.get('extensions.zotero.zbridge.token', true)
    if (typeof v === 'string' && v) return v
  }
  catch (e) {}
  return ZB_DEFAULT_TOKEN
}

/* 取 query 参数。Zotero 7+ 给的是 URL 对象（searchParams），
   更早的实现给的是普通对象 { token: [...] }，两种都兜住。 */
function queryParam(request, name) {
  try {
    if (request && request.searchParams && typeof request.searchParams.get === 'function') {
      const v = request.searchParams.get(name)
      if (v) return String(v)
    }
  }
  catch (e) {}
  try {
    if (request && request.query && typeof request.query === 'object') {
      const v = request.query[name]
      if (Array.isArray(v)) return v.length ? String(v[0]) : ''
      if (v !== undefined && v !== null) return String(v)
    }
  }
  catch (e) {}
  return ''
}

/* Authorization 头。原版 debug-bridge 读的就是 request.headers.Authorization；
   代理层偶尔会改大小写，两种都试。 */
function authHeader(request) {
  try {
    const h = (request && request.headers) || {}
    const v = h.Authorization || h.authorization || ''
    return typeof v === 'string' ? v : ''
  }
  catch (e) {
    return ''
  }
}

/* 从 "Bearer xxx" 里取令牌；没带 Bearer 前缀就返回空串（不把裸串当令牌）。 */
function bearerToken(request) {
  const v = authHeader(request).trim()
  const m = /^Bearer\s+(.+)$/i.exec(v)
  return m ? m[1].trim() : ''
}

/* 把 query 摊成普通对象。原版 debug-bridge 会把它当作 `query` 形参传给被执行的脚本，
   有客户端依赖这个行为，兼容端点必须照样给。 */
function queryObject(request) {
  const out = {}
  try {
    if (request && request.searchParams && typeof request.searchParams.forEach === 'function') {
      request.searchParams.forEach((v, k) => { out[k] = v })
      return out
    }
  }
  catch (e) {}
  try {
    if (request && request.query && typeof request.query === 'object') {
      for (const k of Object.keys(request.query)) {
        const v = request.query[k]
        out[k] = Array.isArray(v) ? v[v.length - 1] : v
      }
    }
  }
  catch (e) {}
  return out
}

/* 请求体。Zotero 7+ 是 UTF-8 字符串；个别版本 / 编码会给字节数组，一并转掉。 */
function requestBody(request) {
  const d = request ? request.data : null
  if (typeof d === 'string') return d
  if (d === null || d === undefined) return ''
  try {
    if (typeof TextDecoder !== 'undefined' && (d instanceof Uint8Array || Array.isArray(d))) {
      return new TextDecoder('utf-8').decode(d)
    }
  }
  catch (e) {}
  try {
    return String(d)
  }
  catch (e) {
    return ''
  }
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
    const found = queryParam(request, 'token')
    if (!found) return [401, headers('text/plain'), 'token required']
    if (found !== token()) return [401, headers('text/plain'), 'invalid token']

    const code = requestBody(request)
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
      minZotero: ZB_MIN_ZOTERO,
      compat: ZB_COMPAT_NOTE,
      endpoints: ZB_ENDPOINTS,
    })]
  }
}

/* POST /debug-bridge/execute —— 与原版 Zotero Debug Bridge 同形的兼容端点。
 *
 * 存在的唯一目的：让已经按原版接口写死的客户端（思源 zotero-cite 插件等）在卸掉原版
 * 插件之后，改一个令牌值就能继续跑，不必等着客户端发新版。契约逐项对齐原版 1.2：
 *   Authorization: Bearer <token>   必填
 *     没有该头            → 401 'bearer token required'
 *     令牌不匹配          → 401 'invalid bearer token'
 *   成功                  → 201 + application/json，body = JSON.stringify(返回值)
 *   脚本抛错              → 500 + application/text，'debug-bridge failed: …'
 *   被执行的脚本能拿到一个 query 形参（原版就这么传，有客户端依赖）
 *
 * 与 /zbridge/execute 的区别只有两点：用 Authorization 头而不是 query 传令牌（浏览器侧会
 * 触发预检，所以原版从不给浏览器用）；状态码用 201 而不是 200。
 *
 * 令牌只认 zbridge 的 extensions.zotero.zbridge.token，**不读** debug-bridge.token：
 * 卸掉原版之后旧令牌就该一起作废，留着等于多开一道门。
 */
class LegacyExecuteEndpoint {
  constructor() {
    this.supportedMethods = ['POST']
    this.supportedDataTypes = ['text/plain', 'application/json', 'application/javascript']
    this.allowRequestsFromUnsafeWebContent = true
    this.permitBookmarklet = false
  }

  async init(request) {
    // 头优先；也容忍 ?token=（同一个令牌值，只是换个携带方式）
    const found = bearerToken(request) || queryParam(request, 'token')
    if (!found) return [401, headers('text/plain'), 'bearer token required']
    if (found !== token()) return [401, headers('text/plain'), 'invalid bearer token']

    const code = requestBody(request)
    if (typeof code !== 'string' || !code.trim()) {
      return [400, headers('text/plain'), 'empty body']
    }
    if (code.length > ZB_MAX_BODY) {
      return [413, headers('text/plain'), 'body too large']
    }

    const start = Date.now()
    try {
      const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
      const action = new AsyncFunction('query', code)
      let result = await action(queryObject(request))
      if (typeof result === 'undefined') result = null
      const body = JSON.stringify(result)
      log(`legacy exec ok (${Date.now() - start}ms, ${code.length}B)`)
      return [201, headers(), body]
    }
    catch (err) {
      log(`legacy exec failed: ${err}`)
      return [500, headers('application/text'),
        `debug-bridge failed: ${err}\n${err && err.stack ? err.stack : ''}`]
    }
  }
}

/* 端点注册机制从 Zotero 7 开始就是 Zotero.Server.Endpoints 上的一个普通对象属性。
   未来版本若改了这块，这里探测失败就只记日志、不抛异常，避免整个插件把 Zotero 启动流程带崩。 */
function serverReady() {
  try {
    return !!(Zotero && Zotero.Server && Zotero.Server.Endpoints
      && typeof Zotero.Server.Endpoints === 'object')
  }
  catch (e) {
    return false
  }
}

function register() {
  if (!serverReady()) {
    log('Zotero.Server.Endpoints 不可用，跳过注册；该版本不支持端点 API')
    return false
  }
  try {
    Zotero.Server.Endpoints['/zbridge/execute'] = ExecuteEndpoint
    Zotero.Server.Endpoints['/zbridge/ping'] = PingEndpoint
    Zotero.Server.Endpoints['/debug-bridge/execute'] = LegacyExecuteEndpoint
    log(`registered ${ZB_ENDPOINTS.join(', ')} on Zotero ${Zotero.version || '?'}`)
    return true
  }
  catch (e) {
    log('register failed: ' + e)
    return false
  }
}

function unregister() {
  if (!serverReady()) return
  for (const path of ZB_ENDPOINTS) {
    try {
      delete Zotero.Server.Endpoints[path]
    }
    catch (e) {}
  }
}

async function startup({ id, version, rootURI }, reason) {
  log(`startup v${version || '?'} (${reason || ''}) on Zotero ${Zotero.version || '?'}`)
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
