# 云智手机领取控制台 — Linux Web 部署说明
#
# 1) 本机 / 服务器（推荐，默认纯 API，无需 Chromium）
#    cd cty_phone
#    python3 -m venv .venv && source .venv/bin/activate
#    pip install -r requirements.txt
#    cp config.example.json config.json   # 改 username/password/secret_key
#    # settings.claim_mode 默认 api（纯接口）
#    # 若纯 API 领不到弹框，可改为 auto，并安装浏览器：
#    #   pip install playwright && playwright install chromium
#    bash restart.sh
#
# 2) Docker
#    cp config.example.json config.json
#    docker compose up -d --build
#
# 3) 使用
#    浏览器打开 http://服务器IP:8788
#    登录 → 添加账号（短信）→ 开启定时领取 → 可选配置 PushPlus
#
# claim_mode:
#    api     只用接口（推荐云服务器，无需装浏览器）
#    auto    先接口，失败再用 Playwright
#    browser 强制 Playwright
