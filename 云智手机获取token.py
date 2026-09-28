#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
new Env('云智手机获取token');
================================================================
 云智手机 token 获取脚本（手机号 + 短信验证码 → access_token）
================================================================
适用场景：
  - 云智手机 access_token 失效（约30天）后重新登录获取
  - 支持任意手机号（多账号），手机号可来自参数或 YZ_PHONE 环境变量
  - 可选：自动更新青龙环境变量 yz_token（配置 QL_CLIENT_ID/QL_CLIENT_SECRET）

【命令】
  python 云智手机获取token.py send  [手机号]            发送短信验证码（自动过滑块）
  python 云智手机获取token.py verify <验证码> [手机号]   提交验证码，获取并保存 token
  python 云智手机获取token.py login  [手机号]           一条龙：发短信 → 输验证码 → 取 token
  python 云智手机获取token.py check  [手机号]           检查已保存 token 是否有效
  python 云智手机获取token.py diag                       滑块诊断（只测挑战+缺口检测，不发短信）

【依赖】requests、pycryptodome、Pillow、numpy
  本地: pip install requests pycryptodome Pillow numpy
  青龙: 依赖管理 → Python3 → 依次添加以上四个

【环境变量】
  YZ_PHONE              默认手机号（可省略命令行里的手机号参数）
  QL_URL                青龙地址，默认 http://127.0.0.1:5700
  QL_CLIENT_ID          青龙 OpenApi client_id（可选，配置后自动更新青龙变量）
  QL_CLIENT_SECRET      青龙 OpenApi client_secret
  QL_ENV_NAME           要更新的青龙变量名，默认 yz_token

【token 保存位置】（verify 成功后全部执行）
  1. 状态文件 yz_login_state.json（按手机号分账号保存）
  2. 同步写入 Web 控制台 config.json（同手机号覆盖 token，推荐）
  3. 若 userId 与本地 yz_token.json 一致 → 同步覆盖 yz_token.json
  4. 屏幕始终明文打印 token

【推荐】日常用 Web：python web_app.py（页面短信登录，无需本脚本）
"""
import sys, os, json, time, base64, io, secrets, hashlib
import requests

# ---------------- 常量（逆向自前端 index-BBDDgKhY.js，已实测验证） ----------------
SIGN_KEY = "7f9e2d08c1b5a3709e4f6d2a8c0e1b3f"   # MD5 签名盐
AES_KEY  = "5f3a7d2b91c4e806275910ad3fc6b241"    # 手机号 AES-ECB 密钥
BASE = "https://yunzhi.new-gm.cn/yunzhi/api"
CHANNEL = "00000042"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("YZ_STATE_DIR") or HERE
STATE_FILE = os.path.join(STATE_DIR, "yz_login_state.json")
TOKEN_FILE = os.path.join(HERE, "yz_token.json")
QL_URL = (os.environ.get("QL_URL") or "http://127.0.0.1:5700").rstrip("/")
QL_ENV_NAME = os.environ.get("QL_ENV_NAME") or "yz_token"


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------- 状态文件 ----------------
def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"phones": {}, "sms": {}}


def save_state(st):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"[!] 状态保存失败: {e}")


def device_no(st, phone):
    dnos = st.setdefault("phones", {}).setdefault(phone, {})
    if not dnos.get("device_no"):
        dnos["device_no"] = "".join(secrets.choice("0123456789abcdef") for _ in range(16))
        save_state(st)
    return dnos["device_no"]


def resolve_phone(arg_phone):
    p = (arg_phone or os.environ.get("YZ_PHONE") or "").strip()
    if not p or not (p.isdigit() and len(p) == 11):
        log("[!] 未指定有效手机号（11位数字），请传参数或设置环境变量 YZ_PHONE")
        sys.exit(1)
    return p


def mask_phone(p):
    return f"{p[:3]}****{p[-4:]}" if len(p) == 11 else p


# ---------------- 签名与请求 ----------------
def make_sign(params: dict) -> str:
    s = "&".join(f"{k}={params[k]}" for k in sorted(params.keys())) + SIGN_KEY
    return hashlib.md5(s.encode()).hexdigest()


def aes_phone(phone: str) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    cipher = AES.new(AES_KEY.encode(), AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(phone.encode(), AES.block_size))).decode()


def api_headers(dno):
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


def post(path, body, dno):
    r = requests.post(f"{BASE}{path}", json=body, headers=api_headers(dno), timeout=20)
    return r.json()


# ---------------- 滑块缺口检测（NCC 模板匹配，与实测版一致） ----------------
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


def slider_challenge(dno):
    ch = post("/user/auth/slider/challenge", {}, dno)
    if ch.get("code") != 0:
        raise RuntimeError(f"challenge失败: {json.dumps(ch, ensure_ascii=False)[:150]}")
    d = ch.get("data") or {}
    token = d.get("token")
    if not token:
        raise RuntimeError(f"challenge响应缺少token: {json.dumps(d, ensure_ascii=False)[:150]}")
    bg = (d.get("backgroundImage") or d.get("slider_img") or "").replace("data:image/png;base64,", "")
    sl = (d.get("sliderImage") or d.get("slider_image") or "").replace("data:image/png;base64,", "")
    return token, bg, sl


# ---------------- send：发短信 ----------------
def cmd_send(phone):
    st = load_state()
    dno = device_no(st, phone)
    phone_aes = aes_phone(phone)
    for attempt in range(1, 4):
        try:
            log(f"第{attempt}次尝试过滑块...")
            slider_token, bg, sl = slider_challenge(dno)
            gap_x = detect_gap_x(bg, sl)
            ts = int(time.time() * 1000)
            params = {"phone": phone_aes, "sliderToken": slider_token,
                      "sliderX": gap_x, "timestamp": ts}
            body = {**params, "sign": make_sign(params)}
            resp = post("/user/auth/sms/sendCodeWithSlider", body, dno)
            code, msg = resp.get("code"), resp.get("message") or ""
            log(f"sendCodeWithSlider -> code={code} msg={msg}")
            if code == 0:
                data = resp.get("data") or {}
                sms_token = (data.get("smsToken") or data.get("token")) if isinstance(data, dict) else data
                st.setdefault("sms", {})[phone] = {"smsToken": sms_token, "time": ts}
                save_state(st)
                log(f"[+] 短信已发送到 {mask_phone(phone)}！smsToken已保存")
                log("[+] 下一步: python 云智手机获取token.py verify <6位验证码>")
                return
            if "滑块" in msg or "slider" in msg.lower():
                log("滑块验证未过，1秒后重试...")
                time.sleep(1)
                continue
            log(f"[!] 发送失败: {msg}")
            sys.exit(1)
        except RuntimeError as e:
            log(f"[!] {e}")
            time.sleep(1)
    log("[!] 3次尝试均失败，稍后再试或手动调整")
    sys.exit(1)


# ---------------- verify：提交验证码换token ----------------
def cmd_verify(code, phone):
    st = load_state()
    dno = device_no(st, phone)
    sms = (st.get("sms") or {}).get(phone) or {}
    sms_token = sms.get("smsToken")
    if not sms_token:
        log("[!] 未找到该手机号的 smsToken，请先执行 send")
        sys.exit(1)
    age_min = (time.time() * 1000 - (sms.get("time") or 0)) / 60000
    if age_min > 10:
        log(f"[!] smsToken 已生成 {age_min:.0f} 分钟，可能过期；若验证失败请重新 send")
    ts = int(time.time() * 1000)
    params = {"phone": aes_phone(phone), "smsCode": code,
              "smsToken": sms_token, "timestamp": ts}
    body = {**params, "sign": make_sign(params)}
    resp = post("/user/auth/sms/verify", body, dno)
    log(f"verify -> code={resp.get('code')} msg={resp.get('message')}")
    if resp.get("code") != 0:
        sys.exit(1)
    data = resp.get("data") or {}
    token = data.get("token") or data.get("accessToken") or ""
    if not token:
        log("[!] 响应无token字段: " + json.dumps(resp, ensure_ascii=False)[:300])
        sys.exit(1)

    # 1) 状态文件（分账号保存）
    rec = st.setdefault("phones", {}).setdefault(phone, {})
    rec.update({"access_token": token, "userId": data.get("userId"),
                "login_time": time.strftime("%Y-%m-%d %H:%M:%S")})
    st.get("sms", {}).pop(phone, None)
    save_state(st)

    print()
    log("=" * 60)
    log(f"[+] 登录成功！手机号 {mask_phone(phone)} userId={data.get('userId')}")
    log(f"[+] token（明文，可粘贴到青龙变量 yz_token）:")
    print(token)
    log("=" * 60)

    # 2) 同步 Web 控制台 config.json（同手机号覆盖）
    try:
        from yz_core import (
            get_account,
            list_accounts,
            load_config,
            new_account_id,
            update_account_fields,
            upsert_account,
        )
        cfg = load_config()
        existing = next((a for a in list_accounts(cfg) if a.get("phone") == phone), None)
        fields = {
            "token": token,
            "token_get_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": data.get("userId") or "",
            "phone": phone,
            "last_error": "",
            "enabled": True,
        }
        if existing:
            update_account_fields(existing["id"], fields)
            log(f"[+] 已更新 config.json 账号「{existing.get('name')}」")
        else:
            upsert_account({
                "id": new_account_id(),
                "name": f"云智{mask_phone(phone)}",
                **fields,
                "last_ok": "",
                "last_status": "",
                "last_message": "",
            })
            log("[+] 已写入 config.json 新账号")
    except Exception as e:
        log(f"[*] config.json 同步跳过: {e}")

    # 3) userId 一致时同步覆盖本地 yz_token.json
    try:
        old = json.load(open(TOKEN_FILE, encoding="utf-8"))
        if old.get("userId") == data.get("userId"):
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({"access_token": token, "userId": data.get("userId"),
                           "login_time": time.time(),
                           **{k: v for k, v in data.items() if isinstance(v, (str, int, float))}}, f,
                          ensure_ascii=False)
            log(f"[+] 已同步覆盖本地 {TOKEN_FILE}（userId一致）")
        else:
            log("[*] 本地 yz_token.json 属于其他账号，未覆盖")
    except FileNotFoundError:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"access_token": token, "userId": data.get("userId"), "login_time": time.time()}, f,
                      ensure_ascii=False)
        log(f"[+] 已写入本地 {TOKEN_FILE}")
    except Exception as e:
        log(f"[*] 本地 yz_token.json 处理跳过: {e}")

    # 4) 可选：仍支持青龙 OpenApi（若配置了 QL_CLIENT_ID）
    ql_msg = ql_update_env(token, phone)
    if ql_msg:
        log(ql_msg)


def ql_update_env(token, phone):
    """配置 QL_CLIENT_ID/SECRET 后，把 token 写入青龙变量（多账号 & 追加）"""
    cid = os.environ.get("QL_CLIENT_ID")
    csec = os.environ.get("QL_CLIENT_SECRET")
    if not (cid and csec):
        return None
    try:
        r = requests.post(f"{QL_URL}/open/auth/login",
                          json={"client_id": cid, "client_secret": csec}, timeout=15)
        qtoken = (r.json().get("data") or {}).get("token")
        if not qtoken:
            return f"[!] 青龙认证失败: {r.text[:120]}"
        hd = {"Authorization": f"Bearer {qtoken}", "Content-Type": "application/json"}
        r = requests.get(f"{QL_URL}/open/envs", params={"searchValue": QL_ENV_NAME},
                         headers=hd, timeout=15)
        envs = r.json().get("data") or []
        env = next((e for e in envs if e.get("name") == QL_ENV_NAME), None)
        if env:
            cur = (env.get("value") or "").strip()
            parts = [p for p in cur.split("&") if p]
            # 同手机号旧token替换，其他账号保留（凭token内 userId 无法区分，简单追加+去重）
            if token in parts:
                new_val = cur
            else:
                parts.append(token)
                new_val = "&".join(parts)
            r = requests.put(f"{QL_URL}/open/envs", headers=hd, timeout=15,
                             json={"id": env["id"], "name": QL_ENV_NAME,
                                   "value": new_val, "remarks": env.get("remarks") or "云智手机token"})
            if r.status_code != 200:
                return f"[!] 青龙变量更新失败: {r.text[:120]}"
            if env.get("status") == 0:
                requests.put(f"{QL_URL}/open/envs/enable", headers=hd,
                             json=[env["id"]], timeout=15)
        else:
            r = requests.post(f"{QL_URL}/open/envs", headers=hd, timeout=15,
                             json=[{"name": QL_ENV_NAME, "value": token,
                                    "remarks": "云智手机token"}])
            if r.status_code != 200:
                return f"[!] 青龙变量创建失败: {r.text[:120]}"
        return f"[+] 青龙变量 {QL_ENV_NAME} 已更新"
    except Exception as e:
        return f"[!] 青龙变量更新异常（可手动粘贴token）: {e}"


# ---------------- check：检查token ----------------
def cmd_check(phone):
    st = load_state()
    rec = (st.get("phones") or {}).get(phone) or {}
    token = rec.get("access_token")
    src = "状态文件"
    if not token and os.path.exists(TOKEN_FILE):
        try:
            token = json.load(open(TOKEN_FILE, encoding="utf-8")).get("access_token")
            src = "yz_token.json"
        except Exception:
            pass
    if not token:
        log("[!] 无已保存token，请先 send + verify")
        sys.exit(1)
    h = api_headers(device_no(st, phone))
    h["authorization"] = token
    try:
        r = requests.get(f"{BASE}/user/info", headers=h, timeout=20)
        d = r.json()
    except Exception as e:
        log(f"[!] 请求异常: {e}")
        sys.exit(1)
    if d.get("code") == 0:
        u = d.get("data") or {}
        log(f"[+] token有效（来源: {src}）| 手机号 {mask_phone(phone)} "
            f"| 云智昵称 {u.get('nickName') or u.get('userName') or '-'}")
    else:
        log(f"[-] token已失效（来源: {src}）: {d.get('message')}")
        log("[*] 请执行: send → verify 重新登录")
        sys.exit(2)


# ---------------- diag：滑块诊断（不发短信） ----------------
def cmd_diag(phone):
    st = load_state()
    dno = device_no(st, phone)
    log("滑块诊断：challenge → 缺口检测（不发送短信）")
    slider_token, bg, sl = slider_challenge(dno)
    log(f"challenge OK, token={slider_token[:8]}... bg={len(bg)}B slider={len(sl)}B")
    gap_x = detect_gap_x(bg, sl)
    log(f"[+] 诊断完成，缺口检测正常（x={gap_x}）。发短信流程应可正常执行。")


# ---------------- 入口 ----------------
def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        sys.exit(0)
    cmd, rest = argv[0], argv[1:]
    if cmd == "send":
        cmd_send(resolve_phone(rest[0] if rest else None))
    elif cmd == "verify" and rest:
        code = rest[0]
        if not (code.isdigit() and len(code) == 6):
            log("[!] 验证码应为6位数字")
            sys.exit(1)
        cmd_verify(code, resolve_phone(rest[1] if len(rest) > 1 else None))
    elif cmd == "login":
        phone = resolve_phone(rest[0] if rest else None)
        cmd_send(phone)
        try:
            code = input("请输入收到的6位短信验证码: ").strip()
        except EOFError:
            log("[!] 非交互环境无法输入验证码，请改用两步: send → verify <验证码>")
            sys.exit(1)
        cmd_verify(code, phone)
    elif cmd == "check":
        cmd_check(resolve_phone(rest[0] if rest else None))
    elif cmd == "diag":
        cmd_diag(resolve_phone(rest[0] if rest else None))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
