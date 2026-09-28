#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云智手机「云机空间服务」每日权益自动领取核心模块
================================================================
原理（2026-09-26/27 实测）：
  每日额度必须"真实打开活动页"才由服务端发放（纯API查询不触发发放）。
  打开页面后首页弹「今日登录福利」→ 点「开心收下」→ 额度到账 →
  benefit/claim 延期（或新开）云机 +2 天。
  Playwright 无头浏览器完成"开页面+点弹框"，其余走纯API。

【推荐部署】Linux Web：python web_app.py（config.example.json）
【CLI】python yz_cloudphone_claim.py  （读 config.json；兼容环境变量 yz_token）
【依赖】requests、playwright；playwright install chromium
【Token】约30天；失效后在 Web 控制台短信登录，或 云智手机获取token.py
"""
import os, sys, json, time, hashlib, hmac, secrets
import requests

# ---------------------------------------------------------------- 常量
SIGN_KEY = "7f9e2d08c1b5a3709e4f6d2a8c0e1b3f"   # 普通接口 MD5 签名盐（逆向自前端）
HMAC_KEY = "8822FF81B6623e6f338d6F2A7F49DA83"   # 弹框接口 HmacSHA256 头签名密钥
HMAC_EXCLUDED = {"content-length", "host", "connection", "accept-encoding", "user-agent",
                 "sign", "content-type", "accept", "device_code", "model", "api_level", "cache-control"}
BASE = "https://yunzhi.new-gm.cn/yunzhi/api"
CHANNEL = "00000042"
BENEFIT_ID = "158"                              # 云机空间服务（新开）
ACTIVITY_URL = "https://h5.play.cn/dl/QFj6ne"   # 每日活动链接（302 → yunzhi.play.cn/ai）
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
POPUP_WAIT_SEC = 12     # 打开页面后等待弹框接口返回的秒数
CLICK_WAIT_SEC = 15     # 等待弹框按钮渲染的秒数
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "yz_ql_state.json")   # 设备号 + 当日已领标记（脚本目录持久化）
PROFILES_ROOT = os.path.join(HERE, ".yz_profiles")    # 每账号独立浏览器 profile


def _account_key(account_id) -> str:
    return str(account_id)


def _profile_dir(account_id) -> str:
    d = os.path.join(PROFILES_ROOT, _account_key(account_id))
    os.makedirs(d, exist_ok=True)
    return d


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 状态文件（设备号 / 当日已领标记）
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"device_nos": {}, "last_success": {}}


def save_state(st):
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def device_no(account_id, st):
    """account_id 可用稳定字符串（推荐 Web 账号 id），勿用可变数组下标。"""
    dnos = st.setdefault("device_nos", {})
    key = _account_key(account_id)
    if not dnos.get(key):
        dnos[key] = "".join(secrets.choice("0123456789abcdef") for _ in range(16))
        save_state(st)
    return dnos[key]


def mark_success(account_id, st):
    st.setdefault("last_success", {})[_account_key(account_id)] = time.strftime("%Y-%m-%d")
    save_state(st)


def success_today(account_id, st):
    return st.get("last_success", {}).get(_account_key(account_id)) == time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 签名与请求
def base_headers(dno):
    return {
        "device_type": "3",
        "timestamp": str(int(time.time() * 1000)),
        "api_version": "1",
        "device_no": dno,
        "client_type": "h5",
        "request_id": secrets.token_hex(16),
        "accept": "application/json",
        "cache-control": "no-cache",
        "channel_code": CHANNEL,
        "referer": "https://yunzhi.play.cn/",
        "origin": "https://yunzhi.play.cn",
        "user-agent": MOBILE_UA,
        "version": "10310",
    }


def make_sign(params):
    s = "&".join(f"{k}={params[k]}" for k in sorted(params.keys())) + SIGN_KEY
    return hashlib.md5(s.encode()).hexdigest()


def post_sign(path, params, dno, token):
    body = dict(params)
    body["sign"] = make_sign(params)
    h = base_headers(dno)
    h["authorization"] = token
    r = requests.post(f"{BASE}{path}", json=body, headers=h, timeout=20)
    return r.json()


def hmac_sign(method, sign_path, params, body, headers):
    """弹框接口签名：HmacSHA256('METHOD\\nPATH\\nPARAMS\\nBODY\\nHEADERS\\n', HMAC_KEY)"""
    params_str = ""
    if params:
        params_str = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] is not None)
    body_str = ""
    if body:
        parts = []
        for k in sorted(body):
            v = body[k]
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            parts.append(f"{k}={v}")
        body_str = "&".join(parts)
    hl = {}
    for k, v in headers.items():
        lk = k.lower()
        if v is None or lk in HMAC_EXCLUDED:
            continue
        hl[lk] = str(v)
    hdr_str = "&".join(f"{k}={hl[k]}" for k in sorted(hl))
    payload = f"{method.upper()}\n{sign_path}\n{params_str}\n{body_str}\n{hdr_str}\n"
    return hmac.new(HMAC_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def signed_request(method, api_path, dno, token, params=None, body=None):
    """弹框接口请求（HmacSHA256 头签名）"""
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "request_id": secrets.token_hex(16),
        "timestamp": str(int(time.time() * 1000)),
        "version": "10310",
        "device_type": "3",
        "device_no": dno,
        "client_type": "h5",
        "channel_code": CHANNEL,
        "api_version": "1",
    }
    if token:
        h["authorization"] = token
    h["sign"] = hmac_sign(method, "/yunzhi/api" + api_path, params, body, h)
    r = requests.request(method, f"{BASE}{api_path}",
                         params=params if method.upper() == "GET" else None,
                         json=body if method.upper() != "GET" else None,
                         headers=h, timeout=20)
    try:
        return r.json()
    except Exception:
        return {"code": -1, "message": r.text[:200]}


def is_401(resp):
    code = resp.get("code")
    msg = str(resp.get("message") or "")
    return code == 401 or "未登录" in msg or ("Token" in msg and "无效" in msg)


def token_valid(dno, token):
    try:
        h = base_headers(dno)
        h["authorization"] = token
        r = requests.get(f"{BASE}/user/info", headers=h, timeout=20)
        return r.json().get("code") == 0
    except Exception:
        return False


def benefit_detail(dno, token):
    p = {"benefitConfigId": BENEFIT_ID, "timestamp": int(time.time() * 1000)}
    return post_sign("/benefit/user/benefit", p, dno, token)


def devices_desc(data):
    devs = data.get("cloudDevices") or []
    if not devs:
        return "无云机"
    return "; ".join(f"{d.get('vendorResourceId')}({d.get('flavor')}) 至 {d.get('expireTime')}" for d in devs)


# ---------------------------------------------------------------- 云机空间领取（纯API）
def do_claim(dno, token, detail):
    """userItems[0].userItemId → /benefit/claim → 轮询 status
    默认延期模式：优先续现有云机，无云机才新开"""
    items = sorted(detail.get("userItems") or [], key=lambda x: x.get("expireTime") or "")
    if not items:
        return False, "无可用领取资格(userItems为空)"
    uid = items[0].get("userItemId")
    resource_id = ""
    devs = detail.get("cloudDevices") or []
    if devs:
        runnable = [d for d in devs if d.get("status") in (0, 1, 2)]
        target = runnable[0] if runnable else devs[0]
        resource_id = target.get("vendorResourceId") or ""
        log(f"延期模式：续期现有云机 {resource_id}（当前有效期至 {target.get('expireTime')}）")
    else:
        log("无现有云机，将新开一台")
    p = {"userItemId": uid, "timestamp": int(time.time() * 1000), "resourceId": resource_id}
    resp = post_sign("/benefit/claim", p, dno, token)
    if resp.get("code") != 0:
        if is_401(resp):
            return False, "TOKEN_EXPIRED"
        return False, f"claim接口失败: {json.dumps(resp, ensure_ascii=False)[:200]}"
    data = resp.get("data") or {}
    claim_id, status = data.get("claimId"), data.get("status")
    log(f"claim已受理 claimId={claim_id} 初始status={status}")
    if status == 1:
        return True, f"领取成功（{'延期云机 ' + resource_id if resource_id else '新开云机'}）"
    if status == 2:
        return False, f"领取失败: {data.get('errorMsg')}"
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(2)
        p2 = {"claimId": claim_id, "timestamp": int(time.time() * 1000)}
        try:
            r2 = post_sign("/benefit/claim/status", p2, dno, token)
        except Exception as e:
            log(f"status查询异常: {e}")
            continue
        if r2.get("code") == 0:
            st = (r2.get("data") or {}).get("status")
            log(f"status轮询 -> {st}")
            if st == 1:
                return True, f"领取成功（{'延期云机 ' + resource_id if resource_id else '新开云机'}）"
            if st == 2:
                return False, "领取失败(status=2)"
        else:
            log(f"status查询返回异常: {json.dumps(r2, ensure_ascii=False)[:120]}")
    return False, "领取超时(60秒未出结果)"


def _claimable_popup(x):
    bt = x.get("benefitType") if x.get("benefitType") is not None else 1
    return bt in (1, 3) and x.get("state") in ("CAN_CLAIM", None) and x.get("canClaim") is not False


def api_popup_flow(dno, token):
    """纯 API 领弹框（不需要 Chromium）。
    调 home-popups/init → 有可领弹框则 HMAC 签名 claim。
    返回 (状态, 说明)：claimed / done / wait / expired / fail
    """
    log("纯API弹框流程：GET /content/home-popups/init …")
    init = signed_request("GET", "/content/home-popups/init", dno, token)
    if is_401(init):
        return "expired", "Token失效（init 401）"
    if init.get("code") != 0:
        return "fail", f"init失败: {json.dumps(init, ensure_ascii=False)[:150]}"

    popups = (init.get("data") or {}).get("popups") or []
    log(f"init 返回 {len(popups)} 个弹框")
    if not popups:
        return "done", "今日已领（API无待领弹框）"

    target = next((x for x in popups if _claimable_popup(x)), None)
    if not target:
        return "wait", "弹框均为非领取类(广告)，无需操作"

    pid = target.get("id") or target.get("popupId")
    title = target.get("popupName") or target.get("title") or ""
    log(f"发现弹框 id={pid}「{title}」→ API claim")
    r = signed_request("POST", f"/content/home-popups/{pid}/claim", dno, token)
    if r.get("code") == 0:
        return "claimed", "弹框领取成功（纯API）"
    if is_401(r):
        return "expired", "Token失效（弹框claim 401）"
    return "fail", f"弹框领取失败: {json.dumps(r, ensure_ascii=False)[:150]}"


def claim_mode() -> str:
    """api=只用接口  browser=强制Playwright  auto=先API，失败/无弹框且允许时再浏览器"""
    m = (os.environ.get("YZ_CLAIM_MODE") or "").strip().lower()
    if m in ("api", "browser", "auto"):
        return m
    try:
        from yz_core import settings_cfg
        m = (settings_cfg().get("claim_mode") or "api").strip().lower()
    except Exception:
        m = "api"
    return m if m in ("api", "browser", "auto") else "api"


def popup_flow(token, dno, account_id="default"):
    """统一弹框入口：默认纯 API（云服务器无需装 Chromium）。"""
    mode = claim_mode()
    if mode == "browser":
        return pw_popup_flow(token, dno, account_id=account_id)

    st, msg = api_popup_flow(dno, token)
    if mode == "api":
        return st, msg

    # auto：API 已领到 / 确定已领 / 失效 → 直接返回；其余可尝试浏览器兜底
    if st in ("claimed", "done", "expired"):
        return st, msg
    log(f"纯API结果 {st}: {msg}；尝试 Playwright 兜底…")
    return pw_popup_flow(token, dno, account_id=account_id)


# ---------------------------------------------------------------- Playwright 弹框流程（可选兜底）
def _browser_path():
    p = os.environ.get("YZ_BROWSER_PATH")
    if p and os.path.exists(p):
        return p
    for c in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
              "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(c):
            return c
    return None   # None → 使用 playwright 自带 chromium


async def _pw_flow_async(token, dno, account_id="default"):
    """打开活动页 → 注入token → 等弹框 → 点「开心收下」
    返回 (状态, 说明)：claimed=弹框已领  wait=额度未刷新  expired/fail"""
    from playwright.async_api import async_playwright

    api = {"user_info": None, "init": None, "popup_claim": [], "pool": None, "gen": 0}
    async with async_playwright() as p:
        kw = dict(
            user_data_dir=_profile_dir(account_id),
            headless=True,
            viewport={"width": 390, "height": 844},
            user_agent=MOBILE_UA,
            is_mobile=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
        )
        bp = _browser_path()
        if bp:
            kw["executable_path"] = bp
        ctx = await p.chromium.launch_persistent_context(**kw)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            async def on_response(response):
                url = response.url
                try:
                    if "/user/info" in url and "new-gm.cn" in url:
                        api["user_info"] = {"status": response.status, "body": await response.text(), "gen": api["gen"]}
                    elif "home-popups/init" in url:
                        api["init"] = {"status": response.status, "body": await response.text(), "gen": api["gen"]}
                    elif "/home-popups/" in url and "/claim" in url:
                        api["popup_claim"].append({"status": response.status, "body": await response.text(), "gen": api["gen"]})
                    elif "lobster/pool/byUser" in url:
                        api["pool"] = {"status": response.status, "body": await response.text(), "gen": api["gen"]}
                except Exception:
                    pass

            page.on("response", on_response)

            log(f"打开活动页 {ACTIVITY_URL}")
            await page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)

            # 进入第2代（reload 后）：丢弃第1代（旧token）的响应，避免旧 401 被误判
            api["user_info"] = None; api["init"] = None; api["pool"] = None; api["popup_claim"] = []
            api["gen"] = 2

            await page.evaluate(
                "t => { localStorage.setItem('cloud_phone_token', t);"
                "localStorage.setItem('cloud_phone_token_time', String(Date.now())); }", token)
            log("已注入 token，刷新页面...")
            await page.reload(wait_until="domcontentloaded", timeout=45000)

            # 登录态校验：仅当 reload 后（第2代）接口返回 401 才判失效；等待期间旧代响应被忽略
            deadline = time.time() + 15
            while time.time() < deadline and not api["user_info"] and not api["pool"]:
                await page.wait_for_timeout(500)
            if not api["user_info"] and not api["pool"]:
                return "fail", "页面未发出登录态校验请求（页面加载异常）"
            # 若第一响应是 401，再等 5 秒看 reload 后是否有 200 覆盖（鲁棒性）
            for k in ("pool", "user_info"):
                if api[k] and api[k].get("gen") != 2:
                    await page.wait_for_timeout(3000)
            def latest(k):
                v = api[k]
                if isinstance(v, list):
                    v = v[-1] if v else None
                return v
            for k in ("pool", "user_info"):
                v = latest(k)
                if v:
                    try:
                        b = json.loads(v["body"])
                        if b.get("code") == 401:
                            return "expired", f"{k} 返回 401（token已失效）"
                    except Exception:
                        pass

            # 等 init 弹框结果（页面可能尚未发出请求，最多等 POPUP_WAIT_SEC）
            deadline = time.time() + POPUP_WAIT_SEC
            while time.time() < deadline and not api["init"]:
                await page.wait_for_timeout(500)
            if not api["init"]:
                # 二次等待：刷新确认按钮点击后页面 reload，init 可能重发
                await page.wait_for_timeout(3000)
            if not api["init"]:
                return "wait", "额度未刷新（页面无待领弹框）"
            try:
                init = json.loads(api["init"]["body"])
            except Exception:
                return "fail", "init 响应解析失败"
            popups = (init.get("data") or {}).get("popups") or []
            log(f"init 返回 {len(popups)} 个弹框")

            # 处理可领取弹框：优先 benefitType=1（每日登录福利），其次 benefitType=3（新客体验福利/含云机权益包）
            # type2 为纯广告展示，无领取动作，跳过
            target = next((x for x in popups if _claimable_popup(x)), None)
            if not popups:
                # 弹框为空 = 今日已领或额度已通过其他渠道到账（弹框领取后即消失）
                return "done", "今日已领（页面无待领弹框）"
            if not target:
                return "wait", "弹框均为非领取类(广告)，无需操作"
            pid = target.get("id") or target.get("popupId")
            btn_text = target.get("buttonText") or "立即领取"
            log(f"发现弹框 id={pid}「{target.get('popupName') or target.get('title') or ''}」按钮「{btn_text}」")

            # 点击弹框按钮（按钮动态渲染，轮询等待最多15秒）
            clicked = False
            btn_deadline = time.time() + CLICK_WAIT_SEC
            while time.time() < btn_deadline and not clicked:
                for sel in (".benefit-btn", f"text={btn_text}", "text=立即领取", "text=开心收下"):
                    try:
                        loc = page.locator(sel).first
                        if await loc.count() > 0 and await loc.is_visible():
                            await loc.click(timeout=3000)
                            clicked = True
                            log(f"已点击按钮「{btn_text}」(selector={sel})")
                            break
                    except Exception:
                        continue
                if not clicked:
                    await page.wait_for_timeout(1000)

            if not clicked:
                # 兜底：纯API HmacSHA256 签名调弹框 claim（按钮未渲染时）
                log("按钮未渲染，走纯API兜底领取弹框...")
                r = signed_request("POST", f"/content/home-popups/{pid}/claim", dno, token)
                if r.get("code") == 0:
                    return "claimed", "弹框领取成功（API兜底）"
                if is_401(r):
                    return "expired", "Token失效（弹框claim 401）"
                return "fail", f"弹框领取失败: {json.dumps(r, ensure_ascii=False)[:150]}"

            # 等待 claim 响应
            deadline = time.time() + 20
            while time.time() < deadline and not api["popup_claim"]:
                await page.wait_for_timeout(500)
            if api["popup_claim"]:
                try:
                    cr = json.loads(api["popup_claim"][0]["body"])
                    if cr.get("code") == 0:
                        return "claimed", "弹框领取成功（额度已发放）"
                    if is_401(cr):
                        return "expired", "Token失效（claim 401）"
                    return "fail", f"弹框claim返回: {cr.get('message')}"
                except Exception:
                    pass
            return "claimed", "已点击按钮（未捕获响应，进入额度确认）"
        finally:
            await ctx.close()


def pw_popup_flow(token, dno, account_id="default"):
    """在独立线程+独立事件循环中跑 Playwright，避免与 FastAPI/uvicorn 循环冲突。"""
    import asyncio
    import concurrent.futures

    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _pw_flow_async(token, dno, account_id=account_id)
            )
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_runner).result(timeout=180)
    except ImportError:
        return "fail", ("未安装 playwright：pip install playwright && playwright install chromium "
                        "（Linux 还需 playwright install-deps chromium）")
    except concurrent.futures.TimeoutError:
        return "fail", "Playwright流程超时(180秒)"
    except Exception as e:
        return "fail", f"Playwright流程异常: {e}"


# ---------------------------------------------------------------- 单账号主流程
def _after_popup_claim(account_id, token, dno, st, popup_msg):
    """弹框领取成功后：查权益 → benefit/claim 延期/新开云机。"""
    try:
        d2 = benefit_detail(dno, token)
    except Exception as e:
        return "fail", f"弹框已领但查询异常: {e}"
    if is_401(d2):
        return "expired", "Token已失效"
    if d2.get("code") != 0:
        return "wait", (
            f"{popup_msg}；权益查询仍失败({d2.get('code')}:{d2.get('message')})，"
            "可能新客包尚未写入，等下次再试"
        )
    data2 = d2.get("data") or {}
    if data2.get("remainingQuota") and (data2.get("userItems") or []):
        ok, msg2 = do_claim(dno, token, data2)
        if ok:
            mark_success(account_id, st)
            d3 = benefit_detail(dno, token)
            tail = devices_desc((d3.get("data") or {})) if d3.get("code") == 0 else ""
            return "ok", f"{popup_msg} → {msg2} | {tail}"
        if msg2 == "TOKEN_EXPIRED":
            return "expired", "Token已失效"
        return "fail", f"{popup_msg}，但云机空间领取失败: {msg2}"
    return "wait", "弹框已领但额度未出现，等下次运行再确认"


def _is_benefit_not_ready(resp) -> bool:
    """新注册账号常见：权益包尚未激活，code=1017「权益不存在或已过期」。"""
    code = resp.get("code")
    msg = str(resp.get("message") or "")
    return code == 1017 or "权益不存在" in msg or "已过期" in msg


def run_account(account_id, token, st):
    """返回 (状态, 说明)
    ok=领取成功  done=今日已领  wait=额度未刷新(等下次cron)  fail=失败  expired=token失效
    account_id: 稳定字符串（Web 账号 id）或数字下标（兼容旧调用）"""
    dno = device_no(account_id, st)
    if not token_valid(dno, token):
        return "expired", "Token已失效，请在 Web 控制台短信重新登录"

    try:
        d = benefit_detail(dno, token)
    except Exception as e:
        return "fail", f"benefit查询异常: {e}"
    if is_401(d):
        return "expired", "Token已失效"

    # 新账号 / 权益未激活：必须先开页面领「新客体验福利」(benefitType=3)，
    # 不能在这里直接 fail，否则永远到不了 Playwright。
    if d.get("code") != 0:
        if not _is_benefit_not_ready(d):
            return "fail", f"benefit查询失败: {json.dumps(d, ensure_ascii=False)[:150]}"
        log(f"权益未激活({d.get('code')}:{d.get('message')})，按新账号走页面弹框领取…")
        st2, msg = popup_flow(token, dno, account_id=account_id)
        if st2 == "claimed":
            return _after_popup_claim(account_id, token, dno, st, msg)
        if st2 in ("done", "wait"):
            # 无弹框也可能是新客包已领过但 158 仍未同步，保留 wait 下次再试
            return st2, f"新账号权益未就绪且页面无待领弹框: {msg}"
        return st2, msg

    data = d.get("data") or {}
    quota = data.get("remainingQuota")

    # 1) 额度已在账（弹框已通过其他渠道领过）→ 直接领云机空间
    if quota and (data.get("userItems") or []):
        ok, msg = do_claim(dno, token, data)
        if ok:
            mark_success(account_id, st)
            d2 = benefit_detail(dno, token)
            tail = devices_desc((d2.get("data") or {})) if d2.get("code") == 0 else ""
            return "ok", f"{msg} | {tail}"
        if msg == "TOKEN_EXPIRED":
            return "expired", "Token已失效"
        return "fail", msg

    # 2) 额度为0 → 判断今日是否已领
    today = time.strftime("%Y-%m-%d")
    created_today = any((x.get("createTime") or "").startswith(today)
                        for x in data.get("cloudDevices") or [])
    if success_today(account_id, st) or created_today:
        return "done", f"今日已领过，跳过 | {devices_desc(data)}"

    # 3) 未领且无额度 → 弹框（每日登录福利 / 新客体验福利）
    st2, msg = popup_flow(token, dno, account_id=account_id)
    if st2 == "claimed":
        return _after_popup_claim(account_id, token, dno, st, msg)
    return st2, msg


# ---------------------------------------------------------------- 入口（CLI / 兼容旧青龙；推荐改用 web_app.py）
def main():
    # 优先读本地 config.json（Web 部署）
    try:
        from yz_core import list_accounts, load_config, summarize_notify, settings_cfg
        cfg = load_config()
        accounts = [a for a in list_accounts(cfg) if (a.get("token") or "").strip()]
        if accounts:
            st = load_state()
            results = []
            code = 0
            for acc in accounts:
                name = acc.get("name") or acc.get("id")
                log(f"===== {name} =====")
                try:
                    s, m = run_account(acc["id"], acc["token"].strip(), st)
                except Exception as e:
                    s, m = "fail", f"运行异常: {e}"
                log(f"[{name}] {s}: {m}")
                results.append((name, s, m))
                if s == "expired":
                    code = 2
                elif s == "fail" and code == 0:
                    code = 1
            summarize_notify(results, settings_cfg(cfg))
            sys.exit(code)
    except Exception as e:
        log(f"[*] 未走 config.json（{e}），回退环境变量 yz_token")

    raw = (os.environ.get("yz_token") or os.environ.get("YZ_TOKEN") or "").strip()
    if not raw:
        log("[!] 未配置账号：请使用 web_app.py，或配置环境变量 yz_token")
        sys.exit(1)
    tokens = [t.strip() for t in raw.replace("\n", "&").split("&") if t.strip()]

    st = load_state()
    results = []
    code = 0
    for i, tok in enumerate(tokens, 1):
        name = f"账号{i}"
        aid = f"env_{i}"
        log(f"===== {name} =====")
        try:
            s, m = run_account(aid, tok, st)
        except Exception as e:
            s, m = "fail", f"运行异常: {e}"
        log(f"[{name}] {s}: {m}")
        results.append((name, s, m))
        if s == "expired":
            code = 2
        elif s == "fail" and code == 0:
            code = 1

    try:
        from yz_core import summarize_notify, settings_cfg
        summarize_notify(results, settings_cfg())
    except Exception:
        pass
    sys.exit(code)


if __name__ == "__main__":
    main()
