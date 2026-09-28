# -*- coding: utf-8 -*-
"""云智手机公共模块：多账号配置、短信登录、领取封装、PushPlus。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Optional

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_FILE = os.path.join(DIR, "config.json")
LEGACY_ACCOUNTS_FILE = os.path.join(DIR, "yz_accounts.json")
STATE_FILE = os.path.join(DIR, "yz_login_state.json")

SIGN_KEY = "7f9e2d08c1b5a3709e4f6d2a8c0e1b3f"
AES_KEY = "5f3a7d2b91c4e806275910ad3fc6b241"
BASE = "https://yunzhi.new-gm.cn/yunzhi/api"
CHANNEL = "00000042"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)
PUSHPLUS_API = "https://www.pushplus.plus/send"

_config_lock = threading.Lock()


class ConfigError(Exception):
    pass


class TokenInvalid(Exception):
    pass


def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def mask_phone(phone: str) -> str:
    if phone and len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone or "-"


def new_account_id() -> str:
    return "acc_" + secrets.token_hex(4)


# ---------- 配置 ----------

def _atomic_write_json(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _default_config() -> dict:
    return {
        "web": {
            "host": "0.0.0.0",
            "port": 8788,
            "username": "admin",
            "password": "请修改为强密码",
            "secret_key": secrets.token_hex(16),
        },
        "settings": {
            "pushplus_token": "",
            "notify": "important",
            "claim_interval_min": 120,
        },
        "accounts": [],
    }


def migrate_legacy(cfg: dict, path: str) -> dict:
    """兼容旧 yz_accounts.json。"""
    if cfg.get("accounts") or not os.path.exists(LEGACY_ACCOUNTS_FILE):
        return cfg
    try:
        with open(LEGACY_ACCOUNTS_FILE, encoding="utf-8") as f:
            legacy = json.load(f)
    except Exception:
        return cfg
    settings = cfg.setdefault("settings", {})
    if legacy.get("pushplus_token") and not settings.get("pushplus_token"):
        settings["pushplus_token"] = legacy["pushplus_token"]
    if legacy.get("notify") and not settings.get("notify"):
        settings["notify"] = legacy["notify"]
    accounts = cfg.setdefault("accounts", [])
    for i, a in enumerate(legacy.get("accounts") or []):
        tok = (a.get("token") or "").strip()
        if not tok:
            continue
        accounts.append({
            "id": new_account_id(),
            "name": a.get("name") or f"账号{i + 1}",
            "phone": "",
            "token": tok,
            "user_id": jwt_user_id(tok) or "",
            "enabled": True,
            "last_ok": "",
            "last_error": "",
            "last_status": "",
            "last_message": "",
            "token_get_time": "",
        })
    if accounts:
        log(f"已从 yz_accounts.json 迁移 {len(accounts)} 个账号到 config.json")
        save_config(cfg, path)
    return cfg


def load_config(path: str = DEFAULT_CONFIG_FILE) -> dict:
    if not os.path.exists(path):
        if os.path.exists(LEGACY_ACCOUNTS_FILE):
            cfg = _default_config()
            cfg = migrate_legacy(cfg, path)
            save_config(cfg, path)
            return cfg
        raise ConfigError(f"缺少配置文件: {path}，请先复制 config.example.json 为 config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("web", {})
    cfg.setdefault("settings", {})
    cfg.setdefault("accounts", [])
    return migrate_legacy(cfg, path)


def save_config(cfg: dict, path: str = DEFAULT_CONFIG_FILE):
    with _config_lock:
        _atomic_write_json(path, cfg)


def settings_cfg(cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    return cfg.setdefault("settings", {})


def list_accounts(cfg: Optional[dict] = None) -> list:
    cfg = cfg or load_config()
    return cfg.get("accounts") or []


def get_account(account_id: str, cfg: Optional[dict] = None) -> Optional[dict]:
    for acc in list_accounts(cfg):
        if acc.get("id") == account_id:
            return acc
    return None


def upsert_account(account: dict, path: str = DEFAULT_CONFIG_FILE) -> dict:
    cfg = load_config(path)
    accounts = cfg.setdefault("accounts", [])
    for i, acc in enumerate(accounts):
        if acc.get("id") == account.get("id"):
            accounts[i] = account
            save_config(cfg, path)
            return account
    accounts.append(account)
    save_config(cfg, path)
    return account


def update_account_fields(account_id: str, fields: dict, path: str = DEFAULT_CONFIG_FILE) -> dict:
    cfg = load_config(path)
    for i, acc in enumerate(cfg.get("accounts") or []):
        if acc.get("id") == account_id:
            acc.update(fields)
            cfg["accounts"][i] = acc
            save_config(cfg, path)
            return acc
    raise RuntimeError(f"账号不存在: {account_id}")


def delete_account(account_id: str, path: str = DEFAULT_CONFIG_FILE) -> bool:
    cfg = load_config(path)
    accounts = cfg.get("accounts") or []
    new_list = [a for a in accounts if a.get("id") != account_id]
    if len(new_list) == len(accounts):
        return False
    cfg["accounts"] = new_list
    save_config(cfg, path)
    return True


def jwt_user_id(token: str) -> Optional[str]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("userId")
    except Exception:
        return None


def public_account(acc: dict) -> dict:
    phone = acc.get("phone") or ""
    name = acc.get("name") or "云智账号"
    has_token = bool((acc.get("token") or "").strip())
    last_err = acc.get("last_error") or ""
    if "失效" in str(last_err):
        token_status = "已失效"
    elif has_token:
        token_status = "已配置"
    else:
        token_status = "未配置"
    return {
        "id": acc.get("id"),
        "name": name,
        "phone": phone,
        "phone_mask": mask_phone(phone) if phone else "-",
        "avatar": (name[:1] or "云"),
        "user_id": acc.get("user_id") or jwt_user_id(acc.get("token") or "") or "-",
        "has_token": has_token,
        "token_status": token_status,
        "enabled": bool(acc.get("enabled", True)),
        "last_ok": acc.get("last_ok") or "",
        "last_error": last_err,
        "last_status": acc.get("last_status") or "",
        "last_message": acc.get("last_message") or "",
        "token_get_time": acc.get("token_get_time") or "",
    }


# ---------- 登录状态（设备号 / smsToken） ----------

def load_login_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"phones": {}, "sms": {}}


def save_login_state(st: dict):
    try:
        _atomic_write_json(STATE_FILE, st)
    except Exception as e:
        log(f"[!] 登录状态保存失败: {e}")


def device_no_for_phone(phone: str, st: Optional[dict] = None) -> str:
    st = st if st is not None else load_login_state()
    dnos = st.setdefault("phones", {}).setdefault(phone, {})
    if not dnos.get("device_no"):
        dnos["device_no"] = "".join(secrets.choice("0123456789abcdef") for _ in range(16))
        save_login_state(st)
    return dnos["device_no"]


# ---------- 签名 / 请求 ----------

def make_sign(params: dict) -> str:
    s = "&".join(f"{k}={params[k]}" for k in sorted(params.keys())) + SIGN_KEY
    return hashlib.md5(s.encode()).hexdigest()


def aes_phone(phone: str) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    cipher = AES.new(AES_KEY.encode(), AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(phone.encode(), AES.block_size))).decode()


def api_headers(dno: str) -> dict:
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
        "user-agent": UA,
        "version": "10310",
    }


def api_post(path: str, body: dict, dno: str) -> dict:
    r = requests.post(f"{BASE}{path}", json=body, headers=api_headers(dno), timeout=20)
    return r.json()


# ---------- 滑块 ----------

def detect_gap_x(bg_b64: str, slider_b64: str) -> int:
    from PIL import Image
    import numpy as np

    if not bg_b64 or not slider_b64:
        return 160
    bg = np.array(Image.open(io.BytesIO(base64.b64decode(bg_b64))).convert("RGB")).astype(float)
    tpl = np.array(Image.open(io.BytesIO(base64.b64decode(slider_b64))).convert("RGB")).astype(float)
    bh, bw = bg.shape[:2]
    th, tw = tpl.shape[:2]
    if th == 0 or tw == 0 or bh < th or bw < tw:
        return 160
    best_ncc, best_x = -2.0, 160
    step_y = max(1, th // 6)
    for x0 in range(0, bw - tw + 1, 2):
        for y0 in range(0, bh - th + 1, step_y):
            win = bg[y0:y0 + th, x0:x0 + tw]
            wm = win - win.mean()
            tm = tpl - tpl.mean()
            denom = np.sqrt((wm ** 2).sum() * (tm ** 2).sum())
            if denom < 1e-6:
                continue
            ncc = (wm * tm).sum() / denom
            if ncc > best_ncc:
                best_ncc, best_x = ncc, x0
    log(f"缺口检测: x={best_x} (NCC={best_ncc:.4f})")
    return best_x


def slider_challenge(dno: str):
    ch = api_post("/user/auth/slider/challenge", {}, dno)
    if ch.get("code") != 0:
        raise RuntimeError(f"challenge失败: {json.dumps(ch, ensure_ascii=False)[:150]}")
    d = ch.get("data") or {}
    token = d.get("token")
    if not token:
        raise RuntimeError(f"challenge响应缺少token: {json.dumps(d, ensure_ascii=False)[:150]}")
    bg = (d.get("backgroundImage") or d.get("slider_img") or "").replace("data:image/png;base64,", "")
    sl = (d.get("sliderImage") or d.get("slider_image") or "").replace("data:image/png;base64,", "")
    return token, bg, sl


def request_sms_code(phone: str, max_retry: int = 3) -> dict:
    """发送短信验证码（自动过滑块）。返回 {phone, smsToken, time}。"""
    if not (phone.isdigit() and len(phone) == 11):
        raise RuntimeError("手机号须为11位数字")
    st = load_login_state()
    dno = device_no_for_phone(phone, st)
    phone_aes = aes_phone(phone)
    last_err = "未知错误"
    for attempt in range(1, max_retry + 1):
        try:
            log(f"第{attempt}次尝试过滑块...")
            slider_token, bg, sl = slider_challenge(dno)
            gap_x = detect_gap_x(bg, sl)
            ts = int(time.time() * 1000)
            params = {
                "phone": phone_aes,
                "sliderToken": slider_token,
                "sliderX": gap_x,
                "timestamp": ts,
            }
            body = {**params, "sign": make_sign(params)}
            resp = api_post("/user/auth/sms/sendCodeWithSlider", body, dno)
            code, msg = resp.get("code"), resp.get("message") or ""
            log(f"sendCodeWithSlider -> code={code} msg={msg}")
            if code == 0:
                data = resp.get("data") or {}
                if isinstance(data, dict):
                    sms_token = data.get("smsToken") or data.get("token")
                else:
                    sms_token = data
                st.setdefault("sms", {})[phone] = {"smsToken": sms_token, "time": ts}
                save_login_state(st)
                return {"phone": phone, "smsToken": sms_token, "time": ts, "sent_at": time.strftime("%H:%M:%S")}
            if "滑块" in msg or "slider" in msg.lower():
                last_err = msg
                time.sleep(1)
                continue
            raise RuntimeError(msg or "发送失败")
        except RuntimeError as e:
            last_err = str(e)
            time.sleep(1)
    raise RuntimeError(f"发送验证码失败（已重试{max_retry}次）: {last_err}")


def login_with_sms(phone: str, sms_code: str) -> dict:
    """提交验证码，返回 {token, userId, get_time, phone}。"""
    if not (sms_code.isdigit() and len(sms_code) == 6):
        raise RuntimeError("验证码应为6位数字")
    st = load_login_state()
    dno = device_no_for_phone(phone, st)
    sms = (st.get("sms") or {}).get(phone) or {}
    sms_token = sms.get("smsToken")
    if not sms_token:
        raise RuntimeError("未找到该手机号的 smsToken，请先发送验证码")
    ts = int(time.time() * 1000)
    params = {
        "phone": aes_phone(phone),
        "smsCode": sms_code,
        "smsToken": sms_token,
        "timestamp": ts,
    }
    body = {**params, "sign": make_sign(params)}
    resp = api_post("/user/auth/sms/verify", body, dno)
    if resp.get("code") != 0:
        raise RuntimeError(resp.get("message") or "验证失败")
    data = resp.get("data") or {}
    token = data.get("token") or data.get("accessToken") or ""
    if not token:
        raise RuntimeError("响应无 token 字段")
    user_id = data.get("userId")
    get_time = time.strftime("%Y-%m-%d %H:%M:%S")
    rec = st.setdefault("phones", {}).setdefault(phone, {})
    rec.update({"access_token": token, "userId": user_id, "login_time": get_time})
    (st.get("sms") or {}).pop(phone, None)
    save_login_state(st)
    return {"token": token, "userId": user_id, "get_time": get_time, "phone": phone}


def check_token(token: str, phone: str = "") -> bool:
    st = load_login_state()
    dno = device_no_for_phone(phone, st) if phone else "".join(secrets.choice("0123456789abcdef") for _ in range(16))
    h = api_headers(dno)
    h["authorization"] = token
    try:
        r = requests.get(f"{BASE}/user/info", headers=h, timeout=20)
        return r.json().get("code") == 0
    except Exception:
        return False


# ---------- 领取 ----------

def claim_account(account: dict) -> dict:
    """对单个账号执行领取。返回 {status, message, account_id}。"""
    import yz_cloudphone_claim as core

    account_id = account.get("id") or new_account_id()
    token = (account.get("token") or "").strip()
    if not token:
        return {"status": "fail", "message": "token为空", "account_id": account_id}

    st = core.load_state()
    try:
        status, message = core.run_account(account_id, token, st)
    except Exception as e:
        status, message = "fail", f"运行异常: {e}"

    fields: dict[str, Any] = {
        "last_status": status,
        "last_message": message,
    }
    if status == "ok":
        fields["last_ok"] = time.strftime("%Y-%m-%d %H:%M:%S")
        fields["last_error"] = ""
    elif status == "expired":
        fields["last_error"] = message
        fields["enabled"] = False
    elif status == "fail":
        fields["last_error"] = message
    else:
        fields["last_error"] = ""

    try:
        update_account_fields(account_id, fields)
    except Exception:
        pass

    return {"status": status, "message": message, "account_id": account_id}


def push_wechat(pushplus_token: str, title: str, content: str) -> bool:
    tok = (pushplus_token or "").strip()
    if not tok:
        log("[!] 未配置 pushplus_token，跳过推送")
        return False
    try:
        r = requests.post(
            PUSHPLUS_API,
            timeout=15,
            json={"token": tok, "title": title, "content": content, "template": "txt"},
        )
        d = r.json()
        if d.get("code") == 200:
            log(f"[+] pushplus 推送成功: {title}")
            return True
        log(f"[!] pushplus 推送失败: {json.dumps(d, ensure_ascii=False)[:150]}")
        return False
    except Exception as e:
        log(f"[!] pushplus 推送异常: {e}")
        return False


def summarize_notify(results: list, settings: Optional[dict] = None) -> bool:
    """results: [(name, status, message), ...]"""
    if not results:
        return False
    settings = settings or settings_cfg()
    mode = (settings.get("notify") or "important").strip().lower()
    has_ok = any(s == "ok" for _, s, _ in results)
    has_exp = any(s == "expired" for _, s, _ in results)
    has_fail = any(s == "fail" for _, s, _ in results)
    if mode == "important" and not (has_ok or has_exp or has_fail):
        log("[*] notify=important 且无重要事件，跳过推送")
        return False
    label = {
        "ok": "领取成功",
        "done": "今日已领",
        "wait": "额度未刷新",
        "expired": "Token失效",
        "fail": "失败",
    }
    lines = [f"{n}: {label.get(s, s)} — {m}" for n, s, m in results]
    if has_ok:
        title = "云智手机云机空间领取成功"
    elif has_exp:
        title = "云智手机Token已失效"
    elif has_fail:
        title = "云智手机云机空间领取异常"
    else:
        title = "云智手机云机空间领取运行报告"
    return push_wechat(settings.get("pushplus_token") or "", title, "\n".join(lines))
