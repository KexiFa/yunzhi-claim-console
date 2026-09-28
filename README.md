# Yunzhi Claim Console

云智手机「云机空间」每日权益自动领取 Web 控制台。

多账号管理 · 短信登录 · 定时领取 · PushPlus 通知。默认纯 API，云服务器无需安装 Chromium。

## 功能

- Web 面板：添加 / 续期账号、手动领取、开关定时
- 后台调度：按间隔自动领取（当日已领自动跳过）
- Token 失效提醒（PushPlus）
- 领取模式可选：`api` / `auto` / `browser`

## 快速开始

```bash
git clone git@github.com:KexiFa/yunzhi-claim-console.git
cd yunzhi-claim-console

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config.example.json config.json # 修改 web 账号密码与 secret_key
bash restart.sh                    # 或: python web_app.py
```

浏览器打开：`http://服务器IP:8788`

### Docker

```bash
cp config.example.json config.json
docker compose up -d --build
```

### systemd

```bash
sudo cp deploy/yunzhi-phone.service /etc/systemd/system/
# 按实际路径修改 WorkingDirectory / ExecStart
sudo systemctl enable --now yunzhi-phone
```

## 配置说明

复制 `config.example.json` → `config.json`（已 gitignore，勿提交）。

| 字段 | 说明 |
|------|------|
| `web.username` / `password` | 控制台登录账号 |
| `web.secret_key` | Session 密钥，请改成随机长串 |
| `web.port` | 默认 `8788` |
| `settings.pushplus_token` | PushPlus 推送 Token（可选） |
| `settings.notify` | `important`（仅成功/失效/失败）或 `all` |
| `settings.claim_interval_min` | 领取间隔（分钟，≥30） |
| `settings.claim_mode` | 见下表 |

### claim_mode

| 值 | 说明 |
|----|------|
| `api` | 纯接口（推荐，无需浏览器） |
| `auto` | 先接口，失败再用 Playwright |
| `browser` | 强制 Playwright |

`auto` / `browser` 需额外安装：

```bash
pip install playwright
playwright install chromium
# Linux 无头环境: playwright install-deps chromium
```

## 使用

1. 登录控制台
2. 「添加账号」→ 短信验证码登录
3. 开启「定时领取」
4. （可选）系统设置里填 PushPlus

CLI（可选）：

```bash
python 云机权益定时领取器.py          # 立即领取全部启用账号
python 云智手机获取token.py login     # 命令行短信登录
```

## 目录结构

```
yunzhi-claim-console/
├── web_app.py                 # Web 入口
├── yz_core.py                 # 配置 / 短信登录 / 通知
├── yz_cloudphone_claim.py     # 领取核心
├── config.example.json
├── templates/  static/
├── Dockerfile  docker-compose.yml
└── deploy/yunzhi-phone.service
```

## 注意

- 仅供个人账号自用，请遵守平台服务协议
- Token 约 30 天有效，失效后在控制台短信重新登录即可
- 切勿将 `config.json` 或含 Token 的状态文件提交到 Git

## License

MIT
