/*
 * bootstrap.js 离线单测：用 vm 造一个假 Zotero，验证端点契约与版本兼容分支。
 * 运行：node test/unit.js
 *
 * 为什么要有这个：ZBridge 只能装在 Zotero 里跑，改一行代码就要退 Zotero 重装才能验。
 * 这里把「Zotero 给端点的 request 对象」按各版本可能的形态都造一遍，离线就能验出问题。
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "src", "bootstrap.js");
const CODE = fs.readFileSync(SRC, "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (detail ? "  → " + detail : "")); }
}

/* 造一个最小的 Zotero 环境。patch 用来覆盖/删除某些字段，模拟不同版本。 */
function makeZotero(patch) {
  const prefs = Object.create(null);
  const z = {
    version: "10.0.7",
    initializationPromise: Promise.resolve(),
    debug: function () {},
    Prefs: {
      get(name, global) {
        // 真实 Zotero：global=true 按全名读；否则自动补 extensions.zotero.
        const key = global ? name : "extensions.zotero." + name;
        return prefs[key];
      },
      set(name, value) { prefs["extensions.zotero." + name] = value; },
      __raw: prefs,
      __set(key, value) { prefs[key] = value; },
    },
    Server: { Endpoints: {} },
  };
  if (patch) patch(z);
  return z;
}

function load(zotero) {
  const sandbox = { Zotero: zotero, dump() {}, TextDecoder, Uint8Array, Array, Object, String, JSON, Date, Promise };
  vm.createContext(sandbox);
  vm.runInContext(CODE, sandbox, { filename: "bootstrap.js" });
  // class / const 是词法绑定，不会挂到 sandbox 上，显式取一次
  const api = vm.runInContext(
    "({ ExecuteEndpoint, PingEndpoint, token, queryParam, requestBody, register, unregister, serverReady," +
    "   ZB_VERSION, ZB_DEFAULT_TOKEN, ZB_MIN_ZOTERO, ZB_ENDPOINTS })",
    sandbox
  );
  return api;
}

/* 造 request 对象：style = 'url'（Zotero 7+，searchParams 是 URL 对象）或 'query'（更早的普通对象） */
function makeRequest({ style = "url", token, body, rawBody }) {
  const url = new URL("http://127.0.0.1:23119/zbridge/execute" + (token !== undefined ? "?token=" + encodeURIComponent(token) : ""));
  const r = { data: rawBody !== undefined ? rawBody : body };
  if (style === "url") r.searchParams = url.searchParams;
  else r.query = token !== undefined ? { token: [token] } : {};
  return r;
}

(async function main() {
  console.log("=== 1. 版本常量 ===");
  {
    const api = load(makeZotero());
    const mf = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "src", "manifest.json"), "utf8"));
    check("ZB_VERSION 与 manifest.version 一致", api.ZB_VERSION === mf.version, api.ZB_VERSION + " vs " + mf.version);
    check("版本号是 x.y.z 三段式", /^\d+\.\d+\.\d+$/.test(api.ZB_VERSION), api.ZB_VERSION);
    check("minZotero 声明为 7.0", api.ZB_MIN_ZOTERO === "7.0", api.ZB_MIN_ZOTERO);
    check("两个端点路径", api.ZB_ENDPOINTS.join(",") === "/zbridge/execute,/zbridge/ping", api.ZB_ENDPOINTS.join(","));
    check("strict_max_version 不设上限", mf.applications.zotero.strict_max_version === "*",
      String(mf.applications.zotero.strict_max_version));
    check("strict_min_version 为 7.0", mf.applications.zotero.strict_min_version === "7.0",
      String(mf.applications.zotero.strict_min_version));
    check("update_url 存在（Zotero 10 清单校验要求）", !!mf.applications.zotero.update_url);
  }

  console.log("=== 2. 令牌读取 ===");
  {
    const z = makeZotero();
    const api = load(z);
    check("未设 pref 时用默认令牌", api.token() === api.ZB_DEFAULT_TOKEN, api.token());

    z.Prefs.__set("extensions.zotero.zbridge.token", "CUSTOM32");
    check("设了正式 pref 后生效", api.token() === "CUSTOM32", api.token());

    z.Prefs.__set("extensions.zotero.zbridge.token", "");
    check("空字符串回退默认", api.token() === api.ZB_DEFAULT_TOKEN, api.token());

    z.Prefs.__set("extensions.zbridge.token", "WRONG-PREFIX");
    check("错误前缀 extensions.zbridge.token 不生效", api.token() === api.ZB_DEFAULT_TOKEN, api.token());
  }

  console.log("=== 3. query 参数兼容两种 request 形态 ===");
  {
    const api = load(makeZotero());
    check("searchParams（Zotero 7+）",
      api.queryParam(makeRequest({ style: "url", token: "abc" }), "token") === "abc");
    check("query 普通对象（更早实现）",
      api.queryParam(makeRequest({ style: "query", token: "xyz" }), "token") === "xyz");
    check("都没有时返回空串",
      api.queryParam({}, "token") === "");
    check("searchParams 抛异常时降级到 query",
      api.queryParam({ searchParams: { get() { throw new Error("boom"); } }, query: { token: ["q"] } }, "token") === "q");
  }

  console.log("=== 4. 请求体兼容 ===");
  {
    const api = load(makeZotero());
    check("字符串直通", api.requestBody({ data: "return 1" }) === "return 1");
    check("undefined → 空串", api.requestBody({}) === "");
    check("null → 空串", api.requestBody({ data: null }) === "");
    check("UTF-8 字节数组解码",
      api.requestBody({ data: new Uint8Array(Buffer.from("return '中文'", "utf8")) }) === "return '中文'");
  }

  console.log("=== 5. execute 端点 ===");
  {
    const z = makeZotero();
    z.Prefs.__set("extensions.zotero.zbridge.token", "T0K3N");
    const api = load(z);
    const ep = new api.ExecuteEndpoint();

    check("allowRequestsFromUnsafeWebContent 已开", ep.allowRequestsFromUnsafeWebContent === true);
    check("只接受 POST", ep.supportedMethods.join(",") === "POST", ep.supportedMethods.join(","));

    let r = await ep.init(makeRequest({}));
    check("无令牌 → 401 token required", r[0] === 401 && r[2] === "token required", JSON.stringify(r));

    r = await ep.init(makeRequest({ token: "bad" }));
    check("错令牌 → 401 invalid token", r[0] === 401 && r[2] === "invalid token", JSON.stringify(r));

    r = await ep.init(makeRequest({ token: "T0K3N", body: "  " }));
    check("空 body → 400", r[0] === 400, JSON.stringify(r));

    r = await ep.init(makeRequest({ token: "T0K3N", body: "return 1+1" }));
    check("正确令牌 + 脚本 → 200/2", r[0] === 200 && r[2] === "2", JSON.stringify(r));
    check("响应带 ACAO *", r[1]["Access-Control-Allow-Origin"] === "*", JSON.stringify(r[1]));

    r = await ep.init(makeRequest({ token: "T0K3N", body: "return {a:[1,2]}" }));
    check("对象返回值被 JSON 序列化", r[0] === 200 && r[2] === '{"a":[1,2]}', r[2]);

    r = await ep.init(makeRequest({ token: "T0K3N", body: "throw new Error('x')" }));
    check("脚本抛错 → 500", r[0] === 500 && /zbridge failed/.test(r[2]), String(r[0]));

    r = await ep.init(makeRequest({ style: "query", token: "T0K3N", body: "return 'old'" }));
    check("旧 request 形态也能鉴权通过", r[0] === 200 && r[2] === '"old"', JSON.stringify(r));
  }

  console.log("=== 6. ping 端点 ===");
  {
    const api = load(makeZotero());
    const r = new api.PingEndpoint().init({});
    const j = JSON.parse(r[2]);
    check("200", r[0] === 200);
    check("name=ZBridge", j.name === "ZBridge", j.name);
    check("version 是插件版本", j.version === api.ZB_VERSION, j.version);
    check("zotero 是宿主版本", j.zotero === "10.0.7", j.zotero);
    check("返回 minZotero", j.minZotero === "7.0", j.minZotero);
    check("返回 compat 说明", typeof j.compat === "string" && j.compat.length > 0, j.compat);
    check("返回端点列表", Array.isArray(j.endpoints) && j.endpoints.length === 2, JSON.stringify(j.endpoints));
  }

  console.log("=== 7. 注册 / 降级 ===");
  {
    const z = makeZotero();
    const api = load(z);
    check("serverReady 正常为 true", api.serverReady() === true);
    check("register 返回 true", api.register() === true);
    check("两个端点已写入", Object.keys(z.Server.Endpoints).sort().join(",") === "/zbridge/execute,/zbridge/ping",
      Object.keys(z.Server.Endpoints).join(","));
    api.unregister();
    check("unregister 清空", Object.keys(z.Server.Endpoints).length === 0);
  }
  {
    const z = makeZotero((o) => { delete o.Server; });
    const api = load(z);
    check("无 Zotero.Server 时 serverReady=false", api.serverReady() === false);
    let threw = false;
    try { check("无 Zotero.Server 时 register 返回 false", api.register() === false); }
    catch (e) { threw = true; }
    check("无 Zotero.Server 时不抛异常", threw === false);
    let threw2 = false;
    try { api.unregister(); } catch (e) { threw2 = true; }
    check("无 Zotero.Server 时 unregister 不抛", threw2 === false);
  }
  {
    const z = makeZotero((o) => { o.Server = {}; });
    const api = load(z);
    check("Server 存在但缺 Endpoints 时 serverReady=false", api.serverReady() === false);
    check("此时 register 返回 false 而不是崩", api.register() === false);
  }

  console.log("=== 8. startup 正常注册 ===");
  {
    const z = makeZotero();
    const api = load(z);
    const sandbox = { Zotero: z, dump() {}, TextDecoder, Uint8Array, Array, Object, String, JSON, Date, Promise };
    vm.createContext(sandbox);
    vm.runInContext(CODE, sandbox, { filename: "bootstrap.js" });
    const startup = vm.runInContext("startup", sandbox);
    await startup({ id: "zbridge@taotaochen86.github.io", version: api.ZB_VERSION, rootURI: "file:///x/" }, "install");
    check("startup 后端点已注册",
      Object.keys(z.Server.Endpoints).sort().join(",") === "/zbridge/execute,/zbridge/ping",
      Object.keys(z.Server.Endpoints).join(","));
  }
  {
    // initializationPromise 缺失时不应崩（老版本可能没有这个字段）
    const z = makeZotero((o) => { delete o.initializationPromise; });
    const sandbox = { Zotero: z, dump() {}, TextDecoder, Uint8Array, Array, Object, String, JSON, Date, Promise };
    vm.createContext(sandbox);
    vm.runInContext(CODE, sandbox, { filename: "bootstrap.js" });
    const startup = vm.runInContext("startup", sandbox);
    let threw = false;
    try { await startup({ version: "x" }, "install"); } catch (e) { threw = true; }
    check("无 initializationPromise 时 startup 不崩", threw === false);
    check("且仍完成注册", Object.keys(z.Server.Endpoints).length === 2, String(Object.keys(z.Server.Endpoints).length));
  }

  console.log("\n" + pass + "/" + (pass + fail) + " 通过" + (fail ? "（" + fail + " 失败）" : ""));
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.error("测试自身异常：" + (e && e.stack || e));
  process.exit(1);
});
