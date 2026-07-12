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
| 美股資料 | Yahoo Finance（非官方 chart API，免金鑰） |
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

   **config**（設定，key/value 形式，程式會自動補上找不到的 key）
   | key | value |

   目前會用到的 key：`cashier_channel_id`、`recovery_channel_id`、`notify_channel_id`（每日匯率／美股播報與每日提醒共用的頻道）、`sheet_id`、`jpy_report_enabled`（`on`/`off`）、`stock_report_enabled`（`on`/`off`）、`jpy_threshold`（數字）

   **members**（成員）
   | name | discord_id |

   **triggers**（觸發詞）
   | id | keyword | response_text | image_url | mention_name | match_type | reply_type | probability |

   **reminders**（每日提醒）
   | id | content | mention_name | hour | minute | created_by | created_by_name |

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
/管理者工具    # 選「成員管理」新增成員並設定對應的 Discord ID
/管理者工具    # 選「設定頻道」設定收銀台、餐盤回收區、每日播報／提醒三個頻道 ID
```

---

## 指令一覽

### 記帳

| 指令 | 說明 | 權限 |
|------|------|------|
| `/記帳` | 快速記帳，Tab 鍵填欄位（電腦端推薦） | 所有人 |
| `/表單記帳` | 下拉選單 + Modal 記帳（手機端推薦） | 所有人 |
| `/分帳` | 多人分帳，支援自訂比例（金額無條件進位） | 所有人 |
| `/修改帳目 <編號>` | 修改指定編號的帳目 | 所有人 |
| `/刪除帳目 <編號>` | 刪除指定編號的帳目 | 所有人 |

### 查帳與結算

| 指令 | 說明 | 權限 |
|------|------|------|
| `/結清 <人名1> <人名2>` | 結清兩人帳務並歸檔至 history | 所有人 |
| `/查帳` | 公開顯示某人的淨債務彙總 | 所有人 |
| `/藍白` | 公審本群淨欠款最高的成員（@ mention） | 所有人 |
| `/林佳龍` | 心疼本群淨被欠款最高的成員（@ mention） | 所有人 |
| `/年度回顧 [年份]` | 查詢指定年度（留空＝今年至今）每對成員的來往筆數、雙向金額、單筆最大交易與年度餘額 | 所有人 |

### 觸發詞

| 指令 | 說明 | 權限 |
|------|------|------|
| `/新增觸發詞` | 新增關鍵字觸發回應（兩段式 UI） | 管理員 |
| `/管理觸發詞` | 查看所有觸發詞，可選一筆進行編輯或刪除 | 查看：所有人 / 編輯・刪除：管理員 |

### 每日提醒

| 指令 | 說明 | 權限 |
|------|------|------|
| `/每日提醒` | 下拉選單選「新增／刪除／播報設定」：新增可設定每天提醒時間（精確到 15 分鐘）與要不要 tag 成員；刪除只列出自己建立的提醒（管理員可代刪任何人的）；播報設定可開關每日匯率／美股播報並調整匯率划算門檻 | 新增・刪除：所有人 / 播報設定：管理員 |

### 管理

| 指令 | 說明 | 權限 |
|------|------|------|
| `/管理者工具` | 下拉選單整合「成員管理／設定頻道／設定試算表」，非管理員只看得到成員管理選項 | 成員管理查看：所有人 / 其餘：管理員 |
| `/重新整理` | 強制重新讀取資料庫並更新收銀台/餐盤回收區表格、清除觸發詞快取 | 所有人 |
| `/清空收銀台` | 強制清空收銀台頻道內的所有訊息 | 管理員 |

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

### 編輯與刪除

`/管理觸發詞` 列表中的「編輯 / 刪除觸發詞」按鈕選單已整合成一套流程：選一筆觸發詞後，會再跳出「✏️ 編輯」「🗑️ 刪除」兩個選項。編輯會沿用新增時的兩段式 UI（Select 預選目前的觸發／回覆方式，Modal 預填目前的關鍵詞、回覆文字、@ 對象、機率）。新增因為需要直接上傳圖片附件，維持獨立的 `/新增觸發詞` 指令。

---

## 年度回顧

`/年度回顧 [年份]` 統計某一年度（留空＝今年至今）每一對有借貸往來的成員：
- 來往筆數
- 雙向金額（A 欠 B / B 欠 A 各自加總）
- 該年度該配對中單筆金額最大的一筆交易（含日期、項目名稱）
- 年度結束後的淨餘額

會同時讀取 `expense`（尚未結清）與 `history`（已結清歸檔）兩張表做聯集，所以不管那筆帳目後來有沒有 `/結清` 過都算得到。時間判斷會校正成台灣時區再取年份，避免午夜前後的交易被歸到錯的一年。

---

## 每日提醒

`/每日提醒` 用下拉選單整合三個功能：

- **➕ 新增每日提醒**：選擇提醒的小時（0–23）與分鐘（00/15/30/45），可選填要不要 tag 某位成員，最後在 Modal 填提醒內容
- **🗑️ 刪除每日提醒**：只列出自己建立的提醒（管理員會看到所有人的，可代刪）
- **⚙️ 播報設定**（管理員限定）：開關「每日匯率播報」「美股播報」，並調整匯率划算門檻（原本寫死在程式碼的 0.198，現在改存進 `config` 分頁的 `jpy_threshold`）

每分鐘會檢查一次是否有提醒到期，到期時發送到跟每日匯率／美股播報相同的頻道，可透過 `/管理者工具 → 設定頻道` 調整（`notify_channel_id`）。

---

## 自動任務

- **每日播報**：每天台灣時間 09:00 固定發送（不再像以前只有匯率低於門檻才發）：
  - 日幣匯率（低於 `jpy_threshold` 才會多附加碼文案）
  - 昨夜美股收盤：道瓊、那斯達克、標普500、費城半導體，任一指數單日漲跌超過 3% 會附上對應的吐槽文字
  - 兩個區塊都可以透過 `/每日提醒 → 播報設定` 個別關閉；查詢失敗時只有那一段會顯示「查詢失敗」，不會讓整則播報消失
- **每日提醒**：使用者自訂的每日固定時間提醒，見上方「每日提醒」章節
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
- `/查帳` 與 `/藍白` 的 @ 功能需先透過 `/管理者工具 → 成員管理 → 設定對應` 完成 Discord ID 綁定
- 觸發詞機率欄位僅在**同關鍵詞有多筆競爭時**作為權重使用；單筆觸發詞永遠 100% 觸發
- 美股資料來源（Yahoo Finance chart API）為非官方介面，沒有正式文件保證，未來若格式異動可能導致查詢失敗（僅該指數顯示「查詢失敗」，不影響匯率或其他指數）
- 每日提醒的時間精確度為 15 分鐘一格（00/15/30/45），無法設定到分鐘級的任意數字
- 一般成員在 `/每日提醒 → 刪除` 只看得到自己建立的提醒；`播報設定` 僅限管理員使用

---

*基於 discord.py 開發，自由使用與修改。*
