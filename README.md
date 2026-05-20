# 臻美味自助餐 記帳機器人

> 適用於小型群組的 Discord 斜線指令記帳機器人，支援多人分帳、債務結算與歸檔，資料儲存於 Google Sheets。

---

## 技術架構

| 項目 | 內容 |
|------|------|
| Runtime | Python 3.10+ |
| 主要套件 | discord.py 2.6+ |
| 資料儲存 | Google Sheets（gspread） |
| 設定管理 | `.env` + `members.json` + `config.json` |

---

## 安裝套件

```bash
pip install discord.py gspread google-auth python-dotenv flask
```

---

## 部署步驟

### 1. 建立 Discord 機器人

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications) 建立 Application
2. **Bot** 頁面開啟以下 Intent：
   - `Message Content Intent`
   - `Server Members Intent`
3. 複製 Bot Token
4. **OAuth2 → URL Generator** 邀請機器人：
   - Scopes：`bot`、`applications.commands`
   - Permissions：`Send Messages`、`Read Message History`、`Manage Messages`

### 2. 設定 Google Sheets API

1. 前往 [Google Cloud Console](https://console.cloud.google.com) 建立專案
2. 啟用 **Google Sheets API** 與 **Google Drive API**
3. 建立**服務帳號**，下載 JSON 金鑰，重新命名為 `credentials.json`
4. 建立 Google 試算表，新增兩個工作表，名稱必須完全一致：
   - `expense`：欄位為 `編號 / 時間 / 欠債人 / 債權人 / 金額 / 匯率 / 項目`
   - `history`：欄位為 `編號 / 記帳時間 / 結清時間 / 欠債人 / 債權人 / 金額 / 匯率 / 項目`
5. 將試算表共用給服務帳號的 `client_email`（編輯者權限）

### 3. 環境變數

在專案根目錄建立 `.env`：

```env
DISCORD_TOKEN=你的Token
```

`credentials.json` 同樣放置於專案根目錄。兩個檔案均已加入 `.gitignore`，**請勿 commit。**

### 4. 啟動

```bash
py bot.py
```

首次啟動時會自動產生空白的 `members.json` 與 `config.json`。

### 5. Discord 初始設定

機器人上線後，依序執行以下指令：

```
/設定頻道      # 設定收銀台與餐盤回收區頻道 ID
/設定試算表    # 設定 Google 試算表 ID
/成員管理      # 新增成員（需管理員權限）
```

---

## 指令一覽

| 指令 | 說明 | 權限 |
|------|------|------|
| `/記帳` | 快速記帳，Tab 鍵填寫（電腦端推薦） | 所有人 |
| `/表單` | 下拉選單 + Modal 記帳（手機端推薦） | 所有人 |
| `/分帳` | 多人分帳，支援自訂比例 | 所有人 |
| `/修改 <編號>` | 修改指定編號的帳目 | 所有人 |
| `/刪除 <編號>` | 刪除指定編號的帳目 | 所有人 |
| `/結清 <人名1> <人名2>` | 結清兩人帳務並歸檔至 history | 所有人 |
| `/查帳` | 公開顯示某人的淨債務彙總 | 所有人 |
| `/藍白` | 公審本群淨欠款最高的成員 | 所有人 |
| `/重新整理` | 強制重新繪製收銀台表格 | 所有人 |
| `/清空收銀台` | 清空收銀台頻道所有訊息 | 所有人 |
| `/成員管理` | 查看、新增、移除、改名成員 | 查看：所有人 / 編輯：管理員 |
| `/設定頻道` | 設定收銀台與餐盤回收區頻道 ID | 管理員 |
| `/設定試算表` | 設定 Google 試算表 ID | 管理員 |

---

## 專案結構

```
.
├── bot.py               # 機器人主程式
├── credentials.json     # Google 服務帳號金鑰（gitignored）
├── .env                 # Discord Token（gitignored）
├── members.json         # 成員名單與 Discord ID 對應表（自動產生）
├── config.json          # 頻道 ID 與試算表 ID（自動產生）
├── requirements.txt     # 套件清單
└── .gitignore
```

---

## 部署至 Render（Web Service 免費方案）

本機器人內建 Flask 輕量伺服器（`keep_alive()`），可搭配 [UptimeRobot](https://uptimerobot.com) 防止 Render 免費方案休眠。

1. 將程式碼推上 GitHub（確認 `credentials.json` 與 `.env` 已被 gitignore）
2. 在 [Render](https://render.com) 建立 **Web Service**，連結 GitHub Repository
3. 在 Render Dashboard 設定環境變數：

| Key | Value |
|-----|-------|
| `DISCORD_TOKEN` | Bot Token |

4. `credentials.json` 無法直接上傳，建議將其內容 base64 編碼後存為環境變數，於啟動時解碼還原
5. Start Command 設定為：
   ```
   python bot.py
   ```
6. 部署完成後在 Discord 執行 `/設定頻道` 與 `/設定試算表` 完成初始化
7. 在 UptimeRobot 新增 HTTP monitor，每 10 分鐘 ping 一次 Render 提供的 URL，防止休眠

---

## 注意事項

- 斜線指令同步最多需等待 5 分鐘
- 分帳金額採無條件進位（`math.ceil()`），實際總額可能略高於原始金額
- `/結清` 前需確認雙方無未結帳目；`/成員管理 → 移除成員` 同樣會檢查未結帳目
- `/查帳` 與 `/藍白` 的 @ 功能需先透過 `/成員管理 → 設定對應` 完成 Discord ID 綁定

---

*基於 discord.py 開發，自由使用與修改。*
