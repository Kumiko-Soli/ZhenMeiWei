# 臻美味自助餐 記帳機器人

> 適用於小型群組的 Discord 斜線指令記帳機器人，支援多人分帳、債務結算與歸檔、關鍵字觸發回應，資料儲存於 Google Sheets。

---

## 技術架構

| 項目 | 內容 |
|------|------|
| Runtime | Python 3.10+ |
| 主要套件 | discord.py 2.6+ |
| 資料儲存 | Google Sheets（gspread） |
| 圖片托管 | imgbb API |
| 保活機制 | Flask + UptimeRobot |
| 部署平台 | Render Web Service |

---

## 安裝套件

```bash
pip install -r requirements.txt
```

或手動安裝：

```bash
pip install discord.py gspread google-auth python-dotenv flask aiohttp pytz
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
   - Permissions：`Send Messages`、`Read Message History`、`Manage Messages`、`Mention Everyone`、`Pin Messages`

### 2. 設定 Google Sheets API

1. 前往 [Google Cloud Console](https://console.cloud.google.com) 建立專案
2. 啟用 **Google Sheets API** 與 **Google Drive API**
3. 建立**服務帳號**，下載 JSON 金鑰
4. 建立 Google 試算表，新增以下工作表（名稱必須完全一致）：

   **expense**（未結清帳目）
   | id | time | debtor | creditor | amount | rate | item |

   **history**（已結清歸檔）
   | id | record_time | clear_time | debtor | creditor | amount | rate | item |

   **config**（設定）
   | key | value |

   **members**（成員）
   | name | discord_id |

   **triggers**（觸發詞）
   | id | keyword | response_text | image_url | mention_name | match_type | reply_type | probability |

5. 將試算表共用給服務帳號的 `client_email`（編輯者權限）
6. 將 JSON 金鑰內容以 base64 編碼：
   ```bash
   base64 -i credentials.json
   ```
   或 Windows PowerShell：
   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
   ```

### 3. 取得 imgbb API Key（觸發詞圖片上傳用）

1. 前往 [imgbb.com](https://imgbb.com) 註冊帳號
2. 進入 [API 頁面](https://api.imgbb.com) 取得 API Key

### 4. 環境變數

在專案根目錄建立 `.env`：

```env
DISCORD_TOKEN=你的Bot Token
GOOGLE_SHEET_ID=你的試算表ID
GOOGLE_CREDENTIALS_BASE64=你的base64編碼金鑰
IMGBB_API_KEY=你的imgbb API Key
```

**請勿 commit `.env` 或 `credentials.json`，兩者已加入 `.gitignore`。**

### 5. 啟動

```bash
python bot.py
```

### 6. Discord 初始設定

機器人上線後，以管理員身份依序執行：

```
/成員管理      # 新增成員並設定對應的 Discord ID
/設定頻道      # 設定收銀台與餐盤回收區頻道 ID
```

---

## 指令一覽

### 記帳

| 指令 | 說明 | 權限 |
|------|------|------|
| `/記帳` | 快速記帳，Tab 鍵填欄位（電腦端推薦） | 所有人 |
| `/表單` | 下拉選單 + Modal 記帳（手機端推薦） | 所有人 |
| `/分帳` | 多人分帳，支援自訂比例（金額無條件進位） | 所有人 |
| `/修改 <編號>` | 修改指定編號的帳目 | 所有人 |
| `/刪除 <編號>` | 刪除指定編號的帳目 | 所有人 |

### 查帳與結算

| 指令 | 說明 | 權限 |
|------|------|------|
| `/結清 <人名1> <人名2>` | 結清兩人帳務並歸檔至 history | 所有人 |
| `/查帳` | 公開顯示某人的淨債務彙總 | 所有人 |
| `/藍白` | 公審本群淨欠款最高的成員（@ mention） | 所有人 |
| `/林佳龍` | 心疼本群淨被欠款最高的成員（@ mention） | 所有人 |

### 觸發詞

| 指令 | 說明 | 權限 |
|------|------|------|
| `/新增觸發詞` | 新增關鍵字觸發回應（兩段式 UI） | 管理員 |
| `/觸發詞` | 查看所有觸發詞，可刪除 | 查看：所有人 / 刪除：管理員 |
| `/刪除觸發詞 <編號>` | 依編號刪除觸發詞 | 管理員 |

### 管理

| 指令 | 說明 | 權限 |
|------|------|------|
| `/成員管理` | 查看、新增、移除、改名成員 | 查看：所有人 / 編輯：管理員 |
| `/設定頻道` | 設定收銀台與餐盤回收區頻道 ID | 管理員 |
| `/重新整理` | 強制重新繪製收銀台與回收區看板 | 所有人 |

---

## 觸發詞系統

`/新增觸發詞` 採兩段式 UI：

**第一段：選擇觸發與回覆方式**
- 可選填 `image` 附件（圖片自動上傳至 imgbb 取得永久連結）
- Bot 回應下拉選單，選擇觸發方式與回覆方式

**第二段：Modal 表單**
- 觸發關鍵詞
- 回覆文字（用 `<>` 分隔可設定多個隨機選項，例如：`彥丞道歉<>彰化人不行<>又是你`）
- 要 @ 的成員名（選填）
- 觸發機率 0–100（同關鍵詞多筆時作為競爭權重）

### 觸發方式

| 類型 | 說明 | 範例關鍵詞 | 觸發 | 不觸發 |
|------|------|-----------|------|--------|
| 包含 | 訊息中包含關鍵詞（不分大小寫） | `彰化` | 「路過彰化」 | — |
| 完全相符 | 整則訊息完全等於關鍵詞 | `彰化` | 「彰化」 | 「路過彰化」 |
| 正則 | 正規表達式（re.IGNORECASE） | `彰.+人` | 「彰化人」 | 「彰化」 |

### 回覆方式

| 類型 | 說明 |
|------|------|
| 新訊息 | Bot 發一則獨立訊息 |
| 回覆該則 | Bot 引用原訊息並回覆（有箭頭） |

---

## 自動任務

- **每日匯率通知**：每天台灣時間 09:00 查詢 JPY/TWD 匯率，若低於 0.198 自動在指定頻道發送通知
- **請客偵測**：訊息包含「我請大家」「我請客」「請大家吃」時，自動 @ 全體成員並釘選訊息

---

## 部署至 Render（Web Service 免費方案）

本機器人內建 Flask 輕量伺服器（`keep_alive()`），搭配 [UptimeRobot](https://uptimerobot.com) 防止 Render 免費方案休眠。

1. 將程式碼推上 GitHub（確認敏感檔案已被 gitignore）
2. 在 [Render](https://render.com) 建立 **Web Service**，連結 GitHub Repository
3. Start Command：
   ```
   python bot.py
   ```
4. 在 Render Dashboard → **Environment** 設定以下環境變數：

| Key | Value |
|-----|-------|
| `DISCORD_TOKEN` | Bot Token |
| `GOOGLE_SHEET_ID` | Google 試算表 ID |
| `GOOGLE_CREDENTIALS_BASE64` | base64 編碼的服務帳號 JSON |
| `IMGBB_API_KEY` | imgbb API Key |

5. 部署完成後在 UptimeRobot 新增 HTTP monitor，每 5–10 分鐘 ping 一次 Render 提供的 URL

---

## 專案結構

```
.
├── bot.py               # 機器人主程式
├── requirements.txt     # 套件清單
├── .env                 # 環境變數（gitignored）
└── .gitignore
```

---

## 注意事項

- 斜線指令同步最多需等待數分鐘
- 分帳金額採無條件進位（`math.ceil()`），實際總額可能略高於原始金額
- `/結清` 前需確認雙方間的帳目方向一致；否則會出現提示
- `/查帳` 與 `/藍白` 的 @ 功能需先透過 `/成員管理 → 設定對應` 完成 Discord ID 綁定
- 觸發詞機率欄位僅在**同關鍵詞有多筆競爭時**作為權重使用；單筆觸發詞永遠 100% 觸發

---

*基於 discord.py 開發，自由使用與修改。*
