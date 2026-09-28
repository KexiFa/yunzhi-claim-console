# -*- coding: utf-8 -*-
"""
云智手机「云机空间服务」每日权益自动领取脚本 —— Playwright 真实页面版
（在 云智手机权益自动领取.py 基础上升级：关键结论——每日额度由"真实打开页面"
触发服务端发放，纯 API 查询不触发；故本脚本用 Playwright 打开 H5 页面 + 注入 token，
等待首页「今日登录福利」弹框出现并自动点击「立即领取」，随后自动领取云机空间。）

前提：
  1. yz_token.json 里已有有效 token（由 云智手机自动登录.py verify 生成）
  2. Python playwright 已安装，且系统装有 Chrome（用 executable_path 驱动，无需下载 chromium）

用法：
  python 云智手机权益自动领取_PW.py status    用 Playwright 打开页面查看权益/云机状态（只读）
  python 云智手机权益自动领取_PW.py claim    立即尝试领取一次：打开页面→点弹框→领云机空间
  python 云智手机权益自动领取_PW.py daily    定时任务入口：单次尝试，额度未刷新则拉起后台重试进程
  python 云智手机权益自动领取_PW.py auto [重试小时=23] [间隔分钟=20]
                                               后台重试：每 interval 分钟重新打开页面尝试，直到领取成功

逻辑说明：
  - 每日额度刷新模式为"上次领取 + 24小时"（2026-09-26 验证：9/25 22:41 领取后，
    9/26 凌晨 0 点起纯 API 查询一整天 popups 均为空）。
  - Playwright 打开页面后，前端 watch(loggedIn) 自动调 home-popups/init；
    额度刷新后 popups 会出现「今日登录福利」弹框，脚本自动点「立即领取」，
    前端调 home-popups/{id}/claim 发放额度，再由 benefit/claim 领取云机空间 2 天。
  - 今日已领判定：本地领取日志有今日成功记录，或额度查询 remainingQuota=0 且已领。
退出码： 0=今日已领或领取成功  1=领取失败/重试超时  2=token失效(需重新登录)
"""
import sys, os, json, time, subprocess, secrets

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "yz_token.json")
LOG_FILE = os.path.join(HERE, "yz_claim_log.json")
HEARTBEAT_FILE = os.path.join(HERE, "yz_auto.heartbeat")
PROFILE_DIR = os.path.join(HERE, ".temp", "pw_yunzhi_profile")   # Playwright 独立浏览器 profile

ACTIVITY_URL = "https://h5.play.cn/dl/QFj6ne"        # 每日活动链接（302 → yunzhi.play.cn/ai/?channel_code=00000042）
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

POPUP_WAIT_SEC = 12          # 打开页面后等待弹框出现的秒数
CLAIM_POLL_SEC = 90          # 点击「立即领取」后等待云机空间到账的轮询时长


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_token():
    try:
        tok = json.load(open(TOKEN_FILE, encoding='utf-8'))
        return tok.get("access_token") or tok.get("token") or ""
    except Exception:
        return ""


def append_log(result, msg="", extra=None):
    recs = []
    if os.path.exists(LOG_FILE):
        try:
            recs = json.load(open(LOG_FILE, encoding='utf-8'))
        except Exception:
            recs = []
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "result": result, "msg": msg}
    if extra:
        rec.update(extra)
    recs.append(rec)
    recs = recs[-200:]
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)


def today_success_in_log():
    """本地日志今日是否已有成功记录"""
    today = time.strftime("%Y-%m-%d")
    try:
        recs = json.load(open(LOG_FILE, encoding='utf-8'))
        return any(r.get("result") == "success" and (r.get("time") or "").startswith(today) for r in recs)
    except Exception:
        return False


def find_browser():
    if os.path.exists(CHROME_PATH):
        return CHROME_PATH
    if os.path.exists(EDGE_PATH):
        return EDGE_PATH
    return None


# ---------------------------------------------------------------- 页面流程
async def _run_flow_async(headless=True, flow="claim"):
    """Playwright 主流程：打开页面 → 注入 token → 等弹框 → 点领取 → 等到账
    返回 (状态, 说明)
    状态: 'ok'=领取成功 'done'=今日已领 'wait'=额度未刷新 'fail'=失败 'expired'=token失效"""
    from playwright.async_api import async_playwright

    token = load_token()
    if not token:
        return "expired", "yz_token.json 缺失或无 token，请先运行 云智手机自动登录.py send 登录"

    browser_path = find_browser()
    if not browser_path:
        return "fail", "未找到 Chrome/Edge 浏览器，无法启动 Playwright"

    api = {"user_info": None, "init": None, "popup_claim": [], "pool": None, "benefit": None}
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            executable_path=browser_path,
            headless=headless,
            viewport={"width": 390, "height": 844},
            user_agent=MOBILE_UA,
            is_mobile=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        async def on_response(response):
            url = response.url
            try:
                if "/user/info" in url and "new-gm.cn" in url:
                    api["user_info"] = {"status": response.status, "body": await response.text()}
                elif "home-popups/init" in url:
                    api["init"] = {"status": response.status, "body": await response.text()}
                elif "/home-popups/" in url and "/claim" in url:
                    api["popup_claim"].append({"status": response.status, "body": await response.text()})
                elif "lobster/pool/byUser" in url:
                    api["pool"] = {"status": response.status, "body": await response.text()}
            except Exception:
                pass

        page.on("response", on_response)

        # 1) 打开活动链接（首次可能未登录，属正常）
        log(f"打开活动页 {ACTIVITY_URL}")
        await page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)

        # 2) 注入 token 并刷新（前端 watch(loggedIn) 会自动拉弹框 init）
        await page.evaluate(
            "t => { localStorage.setItem('cloud_phone_token', t);"
            "localStorage.setItem('cloud_phone_token_time', String(Date.now())); }", token)
        log("已注入 token，刷新页面...")
        await page.reload(wait_until="domcontentloaded", timeout=45000)

        # 3) 验证登录态：等 user/info 返回（或用 pool/byUser 间接确认登录）
        deadline = time.time() + 15
        while time.time() < deadline and not api["user_info"] and not api["pool"]:
            await page.wait_for_timeout(500)
        if not api["user_info"] and not api["pool"]:
            return "fail", "页面未发出 user/info 或 pool 请求（页面加载异常）"
        # 先检查 pool 响应确认登录态（pool 包含云机信息，不登录返回空或 401）
        if api["pool"]:
            try:
                pool_data = json.loads(api["pool"]["body"])
                if pool_data.get("code") == 401:
                    return "expired", "TOKEN已失效（pool 返回 401）"
            except Exception:
                pass
        # 如果有 user/info 再检查
        if api["user_info"]:
            try:
                body = json.loads(api["user_info"]["body"])
                if body.get("code") == 401 or "Token" in str(body.get("message") or ""):
                    return "expired", "TOKEN已失效（页面 user/info 返回 401）"
                if body.get("code") != 0:
                    return "fail", f"user/info 异常: {body.get('message')}"
            except Exception:
                return "fail", "user/info 响应解析失败"

        # 4) 等待 init 弹框结果
        deadline = time.time() + POPUP_WAIT_SEC
        while time.time() < deadline and not api["init"]:
            await page.wait_for_timeout(500)
        if not api["init"]:
            return "wait", "页面未发出 home-popups/init 请求"

        try:
            init = json.loads(api["init"]["body"])
        except Exception:
            return "fail", "init 响应解析失败"
        popups = (init.get("data") or {}).get("popups") or []
        log(f"init 返回 {len(popups)} 个弹框")

        if not popups:
            # 无弹框：额度未刷新（按 上次领取+24h 模式）
            if flow == "status":
                return "done", "（只读模式）今日无待领弹框"
            return "wait", "额度未刷新(当前无待领弹框)"

        # 5) 有弹框 → 自动点击页面上的领取按钮（buttonText 默认「立即领取」）
        popup = popups[0]
        pid = popup.get("id") or popup.get("popupId")
        btn_text = popup.get("buttonText") or "立即领取"
        log(f"发现弹框 id={pid} title={popup.get('title') or popup.get('popupName') or ''} 按钮「{btn_text}」")

        clicked = False
        # 前端 .benefit-btn 是弹框主按钮（benefitType=1 普通弹框），渲染需要时间，最多等 15 秒
        btn_deadline = time.time() + 15
        while time.time() < btn_deadline and not clicked:
            for sel in [".benefit-btn", "text=立即领取", "text=开心收下", f"text={btn_text}"]:
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
            log("15秒内按钮未渲染，尝试兜底接口…")
        if not clicked:
            # 兜底：dispatch 弹框按钮的点击事件（等价前端 onClick S() 调 Le(id) 领取）
            # 注：home-popups/{id}/claim 校验 HmacSHA256 头签名，直接 fetch 会报"缺少timestamp参数"
            log("未找到可见按钮，尝试 dispatch 按钮点击事件...")
            try:
                await page.evaluate(
                    "() => { const b = document.querySelector('.benefit-btn');"
                    " if (b) { b.dispatchEvent(new MouseEvent('click', {bubbles: true})); return true; }"
                    " return false; }")
                await page.wait_for_timeout(2000)
                if not api["popup_claim"]:
                    # 按钮事件也没触发接口，最后试一次 text 匹配的 span（按钮内层元素）
                    await page.evaluate(
                        "() => { const els = [...document.querySelectorAll('span,div')];"
                        " const t = els.find(e => /^(立即领取|开心收下)$/.test((e.textContent||'').trim()));"
                        " if (t) { t.closest('.benefit-btn')?.click() || t.click(); return true; }"
                        " return false; }")
            except Exception:
                return "fail", f"弹框按钮点击失败(id={pid})"

        # 6) 等待 claim 响应（前端领取成功后 toast「领取成功」并 reload）；最长 30 秒
        deadline = time.time() + 30
        while time.time() < deadline and not api["popup_claim"]:
            await page.wait_for_timeout(500)
        if not api["popup_claim"]:
            log("（未捕获到 claim 接口响应，可能按钮点击未生效，继续检查额度）")
        else:
            try:
                cr = json.loads(api["popup_claim"][0]["body"])
                if cr.get("code") == 0:
                    log("弹框领取成功（额度已发放）")
                elif cr.get("code") == 401 or "Token" in str(cr.get("message") or ""):
                    return "expired", "TOKEN失效（claim 返回 401）"
                else:
                    log(f"claim 返回: {cr.get('message')}")
            except Exception:
                pass

        # 7) 领取成功后前端会 reload，此时额度已到账；再导航到权益页确认 + 触发 benefit/claim
        log("等待页面刷新后检查云机空间到账...")
        await page.wait_for_timeout(3000)
        try:
            await page.goto("https://yunzhi.play.cn/ai/#/mine/benefit",
                             wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(4000)

        # 截图留证（无论成败）
        try:
            shot = os.path.join(HERE, ".temp", f"pw_claim_{time.strftime('%Y%m%d_%H%M%S')}.png")
            os.makedirs(os.path.dirname(shot), exist_ok=True)
            await page.screenshot(path=shot, full_page=True)
            log(f"截图: {shot}")
        except Exception:
            pass

        # 8) 用云端接口确认最终结果（调用旧脚本的纯 API 查询逻辑验证）
        ok, msg = verify_claim_via_api()
        if ok:
            return "ok", msg
        return "wait", msg

    return "fail", "流程异常结束"


def verify_claim_via_api():
    """用纯 API（旧脚本逻辑）确认云机空间是否到账
    返回 (True, 说明) 表示今日已领/领取成功；(False, 说明) 表示尚未到账"""
    try:
        from importlib.machinery import SourceFileLoader
        mod = SourceFileLoader("yz_old",
            os.path.join(HERE, "云智手机权益自动领取.py")).load_module()
        h = mod.auth_headers()
        if not mod.token_valid(h):
            return False, "token失效，无法确认"
        d = mod.benefit_detail(h)
        if d.get("code") != 0:
            return False, f"查询失败: {json.dumps(d, ensure_ascii=False)[:120]}"
        data = d.get("data") or {}
        quota = data.get("remainingQuota")
        items = data.get("userItems") or []
        devs = data.get("cloudDevices") or []
        dev_desc = "; ".join(
            f"{x.get('vendorResourceId')} 至 {x.get('expireTime')}" for x in devs)
        log(f"API确认: 今日剩余额度={quota} 资格={len(items)}个 云机[{dev_desc}]")
        if quota and items:
            # 弹框领取成功后额度已到账 → 直接调旧脚本 do_claim 完成云机空间领取
            ok, msg = mod.do_claim(h, data)
            if ok:
                return True, f"云机空间领取成功: {msg}"
            if msg == "TOKEN_EXPIRED":
                return False, "TOKEN失效"
            return False, f"云机空间领取失败: {msg}"
        if quota == 0 and devs:
            # 已领完（今日额度已用）
            today = time.strftime("%Y-%m-%d")
            created_today = any((x.get("createTime") or "").startswith(today) for x in devs)
            if created_today or today_success_in_log():
                return True, f"今日已领（云机有效期: {dev_desc}）"
            return False, f"额度为0但云机创建时间非今日（云机: {dev_desc}）"
        return False, f"额度={quota} 资格={len(items)}，暂未到账"
    except ImportError:
        return False, "缺少旧脚本 云智手机权益自动领取.py，无法API确认"
    except Exception as e:
        return False, f"API确认异常: {e}"


# ---------------------------------------------------------------- 同步包装
def run_flow(headless=True, flow="claim"):
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        st, msg = loop.run_until_complete(_run_flow_async(headless=headless, flow=flow))
        return st, msg
    except Exception as e:
        return "fail", f"Playwright流程异常: {e}"
    finally:
        try:
            loop.close()
        except Exception:
            pass


def write_heartbeat():
    try:
        with open(HEARTBEAT_FILE, 'w') as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


def heartbeat_fresh(max_age=35 * 60) -> bool:
    try:
        ts = int(open(HEARTBEAT_FILE).read().strip())
        return time.time() - ts < max_age
    except Exception:
        return False


def spawn_bg_auto():
    """拉起独立后台进程执行 auto 23 20"""
    out = open(os.path.join(HERE, "yz_auto_out.txt"), "a", encoding="utf-8")
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "auto", "23", "20"],
            stdout=out, stderr=out, cwd=HERE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    finally:
        out.close()


# ---------------------------------------------------------------- 命令入口
def cmd_status():
    st, msg = run_flow(headless=True, flow="status")
    log(msg)
    if st == "expired":
        append_log("token_expired", msg)
        sys.exit(2)
    if st == "fail":
        append_log("fail", msg)
        sys.exit(1)
    append_log("status", msg)
    sys.exit(0)


def cmd_claim():
    if today_success_in_log():
        log("今日已领过（本地日志），无需重复操作")
        sys.exit(0)
    st, msg = run_flow(headless=False, flow="claim")   # 领取动作可见窗口，便于首次观察
    log(msg)
    if st == "ok":
        append_log("success", msg)
        sys.exit(0)
    if st == "done":
        append_log("already", msg)
        sys.exit(0)
    if st == "expired":
        append_log("token_expired", msg)
        sys.exit(2)
    if st == "wait":
        append_log("wait", msg)
        sys.exit(1)
    append_log("fail", msg)
    sys.exit(1)


def cmd_auto(retry_hours=23.0, interval_min=20.0):
    deadline = time.time() + retry_hours * 3600
    attempt = 0
    while True:
        write_heartbeat()
        attempt += 1
        st, msg = run_flow(headless=True, flow="claim")
        if st == "ok":
            log(f"[第{attempt}次] 领取成功: {msg}")
            append_log("success", f"第{attempt}次尝试领取成功(PW) {msg}")
            sys.exit(0)
        if st == "done":
            log(f"[第{attempt}次] {msg}")
            append_log("already", msg)
            sys.exit(0)
        if st == "expired":
            log(f"[第{attempt}次] TOKEN失效，需人工重新登录")
            append_log("token_expired", msg)
            sys.exit(2)
        log(f"[第{attempt}次] {msg}")
        if time.time() >= deadline:
            log(f"已达最大重试时长{retry_hours}小时，放弃")
            append_log("fail", f"重试{retry_hours}小时后仍未领取(PW): {msg}")
            sys.exit(1)
        log(f"等待{interval_min}分钟后重试（截止 {time.strftime('%H:%M', time.localtime(deadline))}）...")
        time.sleep(interval_min * 60)


def cmd_daily():
    """定时任务入口：单次尝试；额度未刷新则拉起后台重试进程（幂等，靠心跳防重复）"""
    if today_success_in_log():
        log("今日已领过（本地日志）")
        print("[RESULT] ALREADY_CLAIMED")
        sys.exit(0)
    st, msg = run_flow(headless=True, flow="claim")
    log(msg)
    if st == "ok":
        append_log("success", msg)
        print("[RESULT] CLAIM_OK")
        sys.exit(0)
    if st == "done":
        append_log("already", msg)
        print("[RESULT] ALREADY_CLAIMED")
        sys.exit(0)
    if st == "expired":
        append_log("token_expired", msg)
        print("[RESULT] TOKEN_EXPIRED")
        sys.exit(2)
    if st == "wait":
        append_log("wait", msg)
        if heartbeat_fresh():
            log("后台重试进程已在运行，跳过重复拉起")
            print("[RESULT] BACKGROUND_ALREADY_RUNNING")
        else:
            spawn_bg_auto()
            log("已拉起后台重试进程（每20分钟一次，最长23小时）")
            print("[RESULT] QUOTA_NOT_REFRESHED")
            print("[RESULT] BACKGROUND_RETRY_STARTED")
        sys.exit(0)
    append_log("fail", msg)
    print("[RESULT] CLAIM_FAIL")
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    elif sys.argv[1] == "status":
        cmd_status()
    elif sys.argv[1] == "claim":
        cmd_claim()
    elif sys.argv[1] == "daily":
        cmd_daily()
    elif sys.argv[1] == "auto":
        rh = float(sys.argv[2]) if len(sys.argv) >= 3 else 23.0
        im = float(sys.argv[3]) if len(sys.argv) >= 4 else 20.0
        cmd_auto(rh, im)
    else:
        print(__doc__)
