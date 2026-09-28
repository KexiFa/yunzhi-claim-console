#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云智手机「云机空间服务」CLI 领取入口（兼容旧用法）
================================================================
推荐日常使用 Web 控制台：python web_app.py

本脚本仍可用于 crontab 手动触发：
  python 云机权益定时领取器.py            领取全部启用账号 + PushPlus
  python 云机权益定时领取器.py list       查看账号
  python 云机权益定时领取器.py test-push  测试推送

账号与通知配置统一在 config.json（由 Web 或复制 config.example.json 生成）。
若仅有旧版 yz_accounts.json，首次加载会自动迁移。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from yz_core import (  # noqa: E402
    claim_account,
    list_accounts,
    load_config,
    log,
    mask_phone,
    public_account,
    push_wechat,
    settings_cfg,
    summarize_notify,
)


def cmd_run():
    cfg = load_config()
    accounts = list_accounts(cfg)
    if not accounts:
        log("[!] 配置中无账号，请用 web_app.py 添加")
        sys.exit(1)
    results = []
    for acc in accounts:
        if not acc.get("enabled", True):
            continue
        name = acc.get("name") or mask_phone(acc.get("phone") or "") or acc.get("id")
        log(f"===== {name} =====")
        r = claim_account(acc)
        s, m = r.get("status"), r.get("message") or ""
        log(f"[{name}] {s}: {m}")
        results.append((name, s, m))
    if not results:
        log("[!] 无启用账号")
        sys.exit(1)
    summarize_notify(results, settings_cfg(cfg))
    states = {s for _, s, _ in results}
    if "expired" in states:
        sys.exit(2)
    if "fail" in states:
        sys.exit(1)
    sys.exit(0)


def cmd_list():
    cfg = load_config()
    s = settings_cfg(cfg)
    log(f"pushplus: {'已配置' if s.get('pushplus_token') else '未配置'} | notify={s.get('notify')} | 间隔={s.get('claim_interval_min')}分")
    accounts = list_accounts(cfg)
    log(f"账号数: {len(accounts)}")
    for i, a in enumerate(accounts, 1):
        p = public_account(a)
        log(f"  {i}. {p['name']} | {p['phone_mask']} | {p['token_status']} | enabled={p['enabled']}")


def cmd_test_push():
    cfg = load_config()
    tok = settings_cfg(cfg).get("pushplus_token") or ""
    ok = push_wechat(tok, "云机领取器-测试推送", "配置成功！这是一条测试消息。")
    sys.exit(0 if ok else 1)


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] == "run":
        cmd_run()
    elif argv[0] == "list":
        cmd_list()
    elif argv[0] == "test-push":
        cmd_test_push()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
