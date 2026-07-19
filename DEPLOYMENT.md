# VPS 自動部署設定

推送到 `main` 後，GitHub Actions 會透過 SSH 登入 VPS，將部署目錄更新到該次 push 的 commit，並執行：

```sh
docker compose up -d --build --remove-orphans
```

## 1. 第一次設定 VPS

VPS 需先安裝 Git、Docker Engine 與 Docker Compose plugin，並讓部署使用者可以執行 `docker compose`。接著在 VPS 執行：

```sh
git clone git@github.com:huanattw/tcb_monitor.git /opt/tcb_monitor
cd /opt/tcb_monitor
docker compose up -d --build
```

商家與市場清單設定在 `monitoring_config.py`；輪詢間隔、歷史筆數與服務
port 設定在 `topcashbackDE.py`。

新增 merchant 時，只需在對應市場的 `merchants` 加一筆：

```python
{"slug": "merchant-url-slug", "name": "顯示名稱"}
```

`name` 可省略，省略時會直接顯示 `slug`。不同網站的抓取邏輯集中在
`cashback_clients.py`，目前支援 TopCashback 與 ShopBack。

## Telegram 變動通知

在 VPS 的部署目錄建立 `.env`（此檔案已被 Git 忽略）：

```env
SESSION_SECRET=replace_with_a_long_random_value
TELEGRAM_BOT_TOKEN=123456789:your_bot_token
TELEGRAM_CHAT_ID=123456789
```

`SESSION_SECRET` 用來簽署瀏覽器的 HttpOnly session cookie，可用
`openssl rand -hex 32` 產生。使用者正常開啟首頁後，網頁會自動取得 session；
未先開啟首頁而直接呼叫 `/api/*` 會收到 `401 Unauthorized`。

用 BotFather 建立 Bot 並取得 token，再先傳一則訊息給 Bot，取得個人或群組的 Chat ID。設定後重建容器：

```sh
docker compose up -d --build
```

程式會在每次輪詢後比對上一筆有效回饋率；只有回饋率上漲且新回饋率達
100% 以上時才會通知，首次沒有歷史資料時不通知。

如果 GitHub repository 是 private，VPS 也需要一把具有此 repository 唯讀權限的 GitHub deploy key，才能在部署時執行 `git fetch`。

## 2. 建立 CI 專用 SSH key

在自己的電腦執行：

```sh
ssh-keygen -t ed25519 -C github-actions-tcb-monitor -f ./tcb_monitor_ci
```

把 `tcb_monitor_ci.pub` 加到 VPS 部署使用者的 `~/.ssh/authorized_keys`，私鑰內容則放進下一步的 GitHub Secret。不要提交這兩個 key 檔案。

## 3. 設定 GitHub Environment secrets

在 GitHub repository 的 **Settings → Environments → production → Environment secrets** 新增：

| Secret | 內容 |
| --- | --- |
| `VPS_HOST` | VPS IP 或網域名稱 |
| `VPS_PORT` | SSH port，例如 `22` |
| `VPS_USER` | VPS 部署使用者 |
| `VPS_DEPLOY_PATH` | repository 在 VPS 的絕對路徑，例如 `/opt/tcb_monitor` |
| `VPS_SSH_KEY` | 上一步產生的完整私鑰內容 |
| `VPS_KNOWN_HOSTS` | VPS 的 SSH host key 紀錄 |

在可信任的電腦取得 `VPS_KNOWN_HOSTS`：

```sh
ssh-keyscan -p 22 your-vps.example.com
```

加入 Secret 前，應透過 VPS 控制台或其他可信管道核對輸出的 host key fingerprint，避免第一次連線遭到攔截。

設定完成後，push 到 `main`，或到 GitHub 的 **Actions → Deploy to VPS → Run workflow** 手動測試。

> 部署流程會對 VPS 部署目錄執行 `git reset --hard`，所以不要直接在該目錄修改已被 Git 追蹤的檔案。持久化資料保存在掛載的 `history.db`。
