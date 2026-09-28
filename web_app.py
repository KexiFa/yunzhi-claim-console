# -*- coding: utf-8 -*-
"""
云智手机 Web 控制台（多账号定时领取）
  python web_app.py
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from yz_core import (
    DIR,
    ConfigError,
    claim_account,
    delete_account,
    get_account,
    list_accounts,
    load_config,
    log,
    login_with_sms,
    mask_phone,
    new_account_id,
    public_account,
    push_wechat,
    request_sms_code,
    save_config,
    settings_cfg,
    summarize_notify,
    update_account_fields,
    upsert_account,
)

_state_lock = threading.Lock()
_runtime: dict[str, dict[str, Any]] = {}
_sms_pending: dict[str, Any] = {}
_stop_event = threading.Event()
_today_ok = {"date": "", "count": 0}
_claim_lock = threading.Lock()


def _cfg() -> dict:
    return load_config()


def _web_cfg() -> dict:
    return _cfg().get("web") or {}


def _hash_pw(password: str, secret: str) -> str:
    return hashlib.sha256(f"{secret}:{password}".encode("utf-8")).hexdigest()


def _const_eq(a: str, b: str) -> bool:
    a, b = a or "", b or ""
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def _verify_pw(password: str, expected: str, secret: str) -> bool:
    expected = expected or ""
    if expected.startswith("sha256:"):
        return _const_eq("sha256:" + _hash_pw(password, secret), expected)
    return _const_eq(password, expected)


def require_login(request: Request):
    if not request.session.get("user"):
        raise HTTPException(status_code=401, detail="未登录")
    return request.session["user"]


def _rt(account_id: str) -> dict:
    with _state_lock:
        if account_id not in _runtime:
            _runtime[account_id] = {
                "running": False,
                "next_run_at": 0.0,
                "countdown": 0,
                "last_action": "等待调度",
            }
        return _runtime[account_id]


def _bump_today_ok():
    day = time.strftime("%Y-%m-%d")
    with _state_lock:
        if _today_ok["date"] != day:
            _today_ok["date"] = day
            _today_ok["count"] = 0
        _today_ok["count"] += 1


def _today_count() -> int:
    day = time.strftime("%Y-%m-%d")
    with _state_lock:
        if _today_ok["date"] != day:
            return 0
        return int(_today_ok["count"])


def _interval_min() -> int:
    return max(30, int(settings_cfg().get("claim_interval_min") or 120))


def do_claim(account_id: str) -> dict:
    rt = _rt(account_id)
    with _state_lock:
        if rt.get("running"):
            raise RuntimeError("该账号上一轮领取仍在进行中")
        rt["running"] = True
        rt["last_action"] = "领取执行中…"

    try:
        acc = get_account(account_id)
        if not acc:
            raise RuntimeError("账号不存在")
        result = claim_account(acc)
        status = result.get("status")
        message = result.get("message") or ""
        interval = _interval_min()
        next_at = time.time() + interval * 60
        with _state_lock:
            rt["next_run_at"] = next_at
            label = {
                "ok": "领取成功",
                "done": "今日已领",
                "wait": "额度未刷新",
                "expired": "Token失效已暂停",
                "fail": "领取失败",
            }.get(status, status)
            rt["last_action"] = f"{label}: {message}"
        if status == "ok":
            _bump_today_ok()
        if status == "expired":
            with _state_lock:
                rt["next_run_at"] = 0
                rt["countdown"] = 0
            settings = settings_cfg()
            push_wechat(
                settings.get("pushplus_token") or "",
                "云智手机 Token 失效",
                f"账号 {acc.get('name') or mask_phone(acc.get('phone') or '')} token 已失效，"
                f"已自动暂停定时领取。请打开控制台短信重新登录。",
            )
        return result
    except Exception as e:
        update_account_fields(account_id, {"last_error": str(e), "last_status": "fail", "last_message": str(e)})
        with _state_lock:
            rt["last_action"] = f"领取失败: {e}"
            rt["next_run_at"] = time.time() + _interval_min() * 60
        raise
    finally:
        with _state_lock:
            rt["running"] = False


def run_all_claims(only_enabled: bool = True) -> list:
    """串行领取（Playwright 不宜并发）。返回 [(name, status, message), ...]"""
    if not _claim_lock.acquire(blocking=False):
        raise RuntimeError("已有领取任务在执行")
    results = []
    try:
        for acc in list_accounts():
            if only_enabled and not acc.get("enabled", True):
                continue
            if not (acc.get("token") or "").strip():
                continue
            name = acc.get("name") or acc.get("id")
            try:
                r = do_claim(acc["id"])
                results.append((name, r.get("status"), r.get("message") or ""))
            except Exception as e:
                results.append((name, "fail", str(e)))
        summarize_notify(results)
        return results
    finally:
        _claim_lock.release()


def _scheduler_loop():
    log("云智手机领取调度器已启动")
    time.sleep(3)
    for acc in list_accounts():
        if acc.get("enabled", True) and (acc.get("token") or "").strip():
            _rt(acc["id"])["next_run_at"] = time.time() + 8

    while not _stop_event.is_set():
        now = time.time()
        try:
            due = []
            for acc in list_accounts():
                aid = acc.get("id")
                if not aid or not acc.get("enabled", True):
                    continue
                if not (acc.get("token") or "").strip():
                    continue
                rt = _rt(aid)
                next_at = float(rt.get("next_run_at") or 0)
                if next_at <= 0:
                    rt["next_run_at"] = now + _interval_min() * 60
                    continue
                rt["countdown"] = max(0, int(next_at - now))
                if now >= next_at and not rt.get("running"):
                    due.append(acc)

            if due and _claim_lock.acquire(blocking=False):
                try:
                    batch = []
                    for acc in due:
                        name = acc.get("name") or acc.get("id")
                        try:
                            r = do_claim(acc["id"])
                            batch.append((name, r.get("status"), r.get("message") or ""))
                            log(f"[定时] {name} -> {r.get('status')}: {r.get('message')}")
                        except Exception as e:
                            batch.append((name, "fail", str(e)))
                            log(f"[定时] {name} 失败: {e}")
                    summarize_notify(batch)
                finally:
                    _claim_lock.release()
        except Exception as e:
            log(f"[调度器] 异常: {e}")
        time.sleep(1)
    log("云智手机领取调度器已停止")


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=_scheduler_loop, name="yz-claim-scheduler", daemon=True)
    t.start()
    yield
    _stop_event.set()


app = FastAPI(title="云智手机领取控制台", lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(DIR, "static")), name="static")

_secret = "dev-secret"
try:
    _secret = _web_cfg().get("secret_key") or secrets.token_hex(16)
except Exception:
    pass
app.add_middleware(SessionMiddleware, secret_key=_secret, max_age=86400 * 7, same_site="lax")


def _dashboard_payload() -> dict:
    cfg = _cfg()
    settings = settings_cfg(cfg)
    cards = []
    token_ok = 0
    enabled_n = 0
    for acc in list_accounts(cfg):
        card = public_account(acc)
        rt = _rt(acc["id"])
        card["countdown"] = int(rt.get("countdown") or 0)
        card["running"] = bool(rt.get("running"))
        card["last_action"] = rt.get("last_action") or acc.get("last_message") or "待机"
        if card["token_status"] in ("已配置",) and "失效" not in str(acc.get("last_error") or ""):
            token_ok += 1
        if card.get("enabled"):
            enabled_n += 1
        cards.append(card)
    return {
        "accounts": cards,
        "stats": {
            "total": len(cards),
            "enabled": enabled_n,
            "token_ok": token_ok,
            "today_ok": _today_count(),
        },
        "settings": {
            "pushplus_token": settings.get("pushplus_token") or "",
            "notify": settings.get("notify") or "important",
            "claim_interval_min": int(settings.get("claim_interval_min") or 120),
            "claim_mode": settings.get("claim_mode") or "api",
        },
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=302)
    data = _dashboard_payload()
    return templates.TemplateResponse(
        request, "dashboard.html", {"user": request.session.get("user"), **data}
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    w = _web_cfg()
    if not (
        _const_eq(username.strip(), w.get("username") or "admin")
        and _verify_pw(password, w.get("password") or "", w.get("secret_key") or "")
    ):
        return templates.TemplateResponse(
            request, "login.html", {"error": "账号或密码错误"}, status_code=401
        )
    request.session["user"] = username.strip()
    return RedirectResponse("/", status_code=302)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/api/dashboard")
async def api_dashboard(_: str = Depends(require_login)):
    return {"ok": True, **_dashboard_payload()}


@app.post("/api/accounts/{account_id}/claim")
async def api_claim(account_id: str, _: str = Depends(require_login)):
    try:
        result = do_claim(account_id)
        summarize_notify([(
            (get_account(account_id) or {}).get("name") or account_id,
            result.get("status"),
            result.get("message") or "",
        )])
        return {"ok": True, "result": result, "dashboard": _dashboard_payload()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/claim-all")
async def api_claim_all(_: str = Depends(require_login)):
    try:
        results = run_all_claims(only_enabled=False)
        return {
            "ok": True,
            "results": [{"name": n, "status": s, "message": m} for n, s, m in results],
            "dashboard": _dashboard_payload(),
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.patch("/api/accounts/{account_id}")
async def api_patch_account(account_id: str, request: Request, _: str = Depends(require_login)):
    body = await request.json()
    allowed = {}
    if "name" in body:
        allowed["name"] = str(body["name"]).strip() or "云智账号"
    if "enabled" in body:
        allowed["enabled"] = bool(body["enabled"])
    if not allowed:
        return JSONResponse({"ok": False, "error": "无有效字段"}, status_code=400)
    try:
        acc = update_account_fields(account_id, allowed)
        if allowed.get("enabled") is False:
            _rt(account_id)["next_run_at"] = 0
        elif allowed.get("enabled") is True:
            _rt(account_id)["next_run_at"] = time.time() + 5
        return {"ok": True, "account": public_account(acc), "dashboard": _dashboard_payload()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.delete("/api/accounts/{account_id}")
async def api_delete_account(account_id: str, _: str = Depends(require_login)):
    if not delete_account(account_id):
        return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
    with _state_lock:
        _runtime.pop(account_id, None)
    return {"ok": True, "dashboard": _dashboard_payload()}


@app.post("/api/settings")
async def api_settings(request: Request, _: str = Depends(require_login)):
    body = await request.json()
    cfg = _cfg()
    settings = cfg.setdefault("settings", {})
    if "pushplus_token" in body:
        settings["pushplus_token"] = str(body.get("pushplus_token") or "").strip()
    if "notify" in body:
        n = str(body.get("notify") or "important").strip().lower()
        settings["notify"] = n if n in ("all", "important") else "important"
    if "claim_interval_min" in body:
        settings["claim_interval_min"] = max(30, min(24 * 60, int(body["claim_interval_min"])))
    if "claim_mode" in body:
        m = str(body.get("claim_mode") or "api").strip().lower()
        settings["claim_mode"] = m if m in ("api", "browser", "auto") else "api"
    save_config(cfg)
    return {"ok": True, "settings": settings}


@app.post("/api/settings/pushplus/test")
async def api_pushplus_test(_: str = Depends(require_login)):
    token = settings_cfg().get("pushplus_token") or ""
    if not token:
        return JSONResponse({"ok": False, "error": "未配置 PushPlus Token"}, status_code=400)
    ok = push_wechat(token, "云智手机控制台", "PushPlus 测试推送成功")
    if not ok:
        return JSONResponse({"ok": False, "error": "推送失败"}, status_code=400)
    return {"ok": True, "message": "已发送测试推送"}


@app.post("/api/sms/send")
async def api_sms_send(request: Request, _: str = Depends(require_login)):
    body = await request.json()
    phone = (body.get("phone") or "").strip()
    account_id = (body.get("account_id") or "").strip()
    if account_id:
        acc = get_account(account_id)
        if not acc:
            return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
        phone = phone or acc.get("phone") or ""
    if not phone or len(phone) != 11 or not phone.isdigit():
        return JSONResponse({"ok": False, "error": "请填写正确的11位手机号"}, status_code=400)
    try:
        pending = request_sms_code(phone)
        sid = secrets.token_hex(8)
        _sms_pending[sid] = {
            "phone": phone,
            "account_id": account_id,
            "name": (body.get("name") or "").strip(),
            "created": time.time(),
        }
        request.session["sms_sid"] = sid
        return {
            "ok": True,
            "phone_mask": mask_phone(phone),
            "sent_at": pending.get("sent_at"),
            "message": "验证码已发送",
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/sms/login")
async def api_sms_login(request: Request, _: str = Depends(require_login)):
    body = await request.json()
    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse({"ok": False, "error": "请输入验证码"}, status_code=400)
    sid = request.session.get("sms_sid")
    pending = _sms_pending.get(sid or "")
    if not pending:
        return JSONResponse({"ok": False, "error": "验证码会话已失效，请重新发送"}, status_code=400)
    try:
        out = login_with_sms(pending["phone"], code)
        account_id = pending.get("account_id")
        name = pending.get("name") or f"云智{mask_phone(pending['phone'])}"
        fields = {
            "token": out["token"],
            "token_get_time": out["get_time"],
            "user_id": out.get("userId") or "",
            "phone": pending["phone"],
            "last_error": "",
            "enabled": True,
        }
        if account_id and get_account(account_id):
            acc = update_account_fields(account_id, fields)
        else:
            existing = next(
                (a for a in list_accounts() if a.get("phone") == pending["phone"]), None
            )
            if existing:
                account_id = existing["id"]
                if pending.get("name"):
                    fields["name"] = name
                acc = update_account_fields(account_id, fields)
            else:
                account_id = new_account_id()
                acc = {
                    "id": account_id,
                    "name": name,
                    "phone": pending["phone"],
                    "token": out["token"],
                    "token_get_time": out["get_time"],
                    "user_id": out.get("userId") or "",
                    "enabled": True,
                    "last_ok": "",
                    "last_error": "",
                    "last_status": "",
                    "last_message": "",
                }
                upsert_account(acc)

        _sms_pending.pop(sid, None)
        request.session.pop("sms_sid", None)
        _rt(account_id)["next_run_at"] = time.time() + 3
        return {
            "ok": True,
            "account": public_account(get_account(account_id) or acc),
            "dashboard": _dashboard_payload(),
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


def main():
    import uvicorn

    try:
        w = _web_cfg()
    except ConfigError as e:
        log(str(e))
        example = os.path.join(DIR, "config.example.json")
        target = os.path.join(DIR, "config.json")
        if os.path.exists(example) and not os.path.exists(target):
            import shutil
            shutil.copy(example, target)
            log(f"已自动复制 config.example.json → config.json，请修改账号密码后重启")
            w = _web_cfg()
        else:
            raise SystemExit(1) from e

    host = w.get("host") or "0.0.0.0"
    port = int(w.get("port") or 8788)
    log(f"云智手机 Web 控制台启动 http://{host}:{port}")
    uvicorn.run("web_app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
