import os
import math
import json
import base64
import random
import re
import asyncio
from datetime import datetime
from flask import Flask
from threading import Thread
import discord
from discord import app_commands
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from discord.ext import tasks
import pytz
from datetime import time as dtime

load_dotenv()

# ==================== 基礎設定 ====================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ==================== 保持在線用的 Web 伺服器 ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "機器人運行中！"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# ==================== Google Sheets 連線 ====================
_sheets_client = None
_spreadsheet = None
_config_cache = None
_members_cache = None
_triggers_cache = None
_purchases_cache = None
_trips_cache = None

IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "")

def get_sheets_client():
    global _sheets_client
    if _sheets_client is not None:
        return _sheets_client
    b64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")
    if b64:
        creds_json = json.loads(base64.b64decode(b64).decode("utf-8"))
        creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    _sheets_client = gspread.authorize(creds)
    return _sheets_client

def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is not None:
        return _spreadsheet
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise ValueError("尚未設定 GOOGLE_SHEET_ID 環境變數")
    _spreadsheet = get_sheets_client().open_by_key(sheet_id)
    return _spreadsheet

def get_worksheets():
    ss = get_spreadsheet()
    return ss.worksheet("expense"), ss.worksheet("history")

def get_config_ws():
    return get_spreadsheet().worksheet("config")

def get_members_ws():
    return get_spreadsheet().worksheet("members")

def get_triggers_ws():
    return get_spreadsheet().worksheet("triggers")

def get_reminders_ws():
    return get_spreadsheet().worksheet("reminders")

def get_purchases_ws():
    return get_spreadsheet().worksheet("purchases")

def get_trips_ws():
    return get_spreadsheet().worksheet("trips")

def get_trip_history_ws():
    return get_spreadsheet().worksheet("trip_history")

# ==================== 設定檔讀寫（Google Sheets）====================
def load_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        ws = get_config_ws()
        rows = ws.get_all_values()
        config = {}
        for row in rows[1:]:
            if row and len(row) >= 2:
                key, value = row[0], row[1]
                try:
                    config[key] = int(value)
                except ValueError:
                    config[key] = value
        _config_cache = config
        return _config_cache
    except Exception:
        return {"cashier_channel_id": 0, "recovery_channel_id": 0, "sheet_id": ""}

def save_config(key, value):
    global _config_cache
    ws = get_config_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == key:
            ws.update(f"B{i+1}", [[str(value)]])
            _config_cache = None  # 清除快取
            return
    ws.append_row([key, str(value)])
    _config_cache = None  # 清除快取

# ==================== 成員資料讀寫（Google Sheets）====================
def load_members_data():
    global _members_cache
    if _members_cache is not None:
        return _members_cache
    try:
        ws = get_members_ws()
        rows = ws.get_all_values()
        members = []
        discord_map = {}
        for row in rows[1:]:
            if row and row[0]:
                members.append(row[0])
                if len(row) >= 2 and row[1]:
                    discord_map[row[1]] = row[0]
        _members_cache = {"members": members, "discord_map": discord_map}
        return _members_cache
    except Exception:
        return {"members": [], "discord_map": {}}

def add_member_to_sheet(name):
    global _members_cache
    ws = get_members_ws()
    ws.append_row([name, ""])
    _members_cache = None  # 清除快取

def remove_member_from_sheet(name):
    global _members_cache
    ws = get_members_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == name:
            ws.delete_rows(i + 1)
            return
    _members_cache = None  # 清除快取

def set_discord_mapping(name, discord_id):
    global _members_cache
    ws = get_members_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == name:
            ws.update(f"B{i+1}", [[discord_id]])
            return
    _members_cache = None  # 清除快取

def rename_member_in_sheets(old_name, new_name):
    # 更新 members 工作表
    global _members_cache
    ws = get_members_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == old_name:
            ws.update(f"A{i+1}", [[new_name]])
            _members_cache = None  # 清除快取
    _members_cache = None  # 清除快取

    # 更新 expense 和 history 工作表
    expense_ws, history_ws = get_worksheets()
    for sheet_ws in [expense_ws, history_ws]:
        rows = sheet_ws.get_all_values()
        for i, row in enumerate(rows):
            if i == 0:
                continue
            updated = [new_name if cell == old_name else cell for cell in row]
            if updated != row:
                sheet_ws.update(f"A{i+1}:{chr(65+len(row)-1)}{i+1}", [updated])

def get_members():
    return load_members_data()["members"]

# ==================== 觸發詞資料讀寫（Google Sheets）====================
def load_triggers():
    global _triggers_cache
    if _triggers_cache is not None:
        return _triggers_cache
    try:
        ws = get_triggers_ws()
        rows = ws.get_all_values()
        triggers = []
        for row in rows[1:]:
            if row and row[0]:
                triggers.append({
                    "id":            int(row[0]),
                    "keyword":       row[1] if len(row) > 1 else "",
                    "response_text": row[2] if len(row) > 2 else "",
                    "image_url":     row[3] if len(row) > 3 else "",
                    "mention_name":  row[4] if len(row) > 4 else "",
                    "match_type":    row[5] if len(row) > 5 and row[5] else "contains",
                    "reply_type":    row[6] if len(row) > 6 and row[6] else "new",
                    "probability":   int(row[7]) if len(row) > 7 and row[7].isdigit() else 100,
                })
        _triggers_cache = triggers
        return _triggers_cache
    except Exception:
        return []

def get_trigger_next_id():
    ws = get_triggers_ws()
    rows = ws.get_all_values()
    ids = [int(row[0]) for row in rows[1:] if row and row[0].isdigit()]
    return max(ids) + 1 if ids else 1

def add_trigger(keyword, response_text, image_url, mention_name, match_type="contains", reply_type="new", probability=100):
    global _triggers_cache
    ws = get_triggers_ws()
    next_id = get_trigger_next_id()
    ws.append_row([next_id, keyword, response_text, image_url, mention_name, match_type, reply_type, probability])
    _triggers_cache = None
    return next_id

def delete_trigger_by_id(trigger_id):
    global _triggers_cache
    ws = get_triggers_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(trigger_id):
            ws.delete_rows(i + 1)
            _triggers_cache = None
            return True
    return False

def load_reminders():
    try:
        ws = get_reminders_ws()
        rows = ws.get_all_values()
        reminders = []
        for row in rows[1:]:
            if row and row[0]:
                reminders.append({
                    "id": int(row[0]),
                    "content": row[1] if len(row) > 1 else "",
                    "mention_name": row[2] if len(row) > 2 else "",
                    "hour": int(row[3]) if len(row) > 3 and row[3].isdigit() else 9,
                    "minute": int(row[4]) if len(row) > 4 and row[4].isdigit() else 0,
                    "created_by": row[5] if len(row) > 5 else "",
                    "created_by_name": row[6] if len(row) > 6 else "",
                })
        return reminders
    except Exception:
        return []

def get_reminder_next_id():
    ws = get_reminders_ws()
    rows = ws.get_all_values()
    ids = [int(row[0]) for row in rows[1:] if row and row[0].isdigit()]
    return max(ids) + 1 if ids else 1

def add_reminder(content, mention_name, hour, minute, created_by, created_by_name):
    ws = get_reminders_ws()
    next_id = get_reminder_next_id()
    ws.append_row([next_id, content, mention_name, hour, minute, created_by, created_by_name])
    return next_id

def delete_reminder_by_id(reminder_id):
    ws = get_reminders_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(reminder_id):
            ws.delete_rows(i + 1)
            return True
    return False

def update_trigger_by_id(trigger_id, keyword, response_text, image_url, mention_name, match_type, reply_type, probability):
    global _triggers_cache
    ws = get_triggers_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(trigger_id):
            ws.update(f"A{i+1}:H{i+1}", [[row[0], keyword, response_text, image_url, mention_name, match_type, reply_type, probability]])
            _triggers_cache = None
            return True
    return False

# ==================== 代購資料讀寫（Google Sheets）====================
def load_purchases():
    global _purchases_cache
    if _purchases_cache is not None:
        return _purchases_cache
    try:
        ws = get_purchases_ws()
        rows = ws.get_all_values()
        purchases = []
        for row in rows[1:]:
            if row and row[0]:
                purchases.append({
                    "id":                          int(row[0]),
                    "status":                      row[1] if len(row) > 1 and row[1] else "pending",
                    "agent_discord_id":            row[2] if len(row) > 2 else "",
                    "agent_display_name":          row[3] if len(row) > 3 else "",
                    "creation_source":             row[4] if len(row) > 4 else "self",
                    "requester_discord_id":        row[5] if len(row) > 5 else "",
                    "requester_display_name":      row[6] if len(row) > 6 else "",
                    "requester_line_id":           row[7] if len(row) > 7 else "",
                    "item_description":            row[8] if len(row) > 8 else "",
                    "image_url_1":                 row[9] if len(row) > 9 else "",
                    "image_url_2":                 row[10] if len(row) > 10 else "",
                    "image_url_3":                 row[11] if len(row) > 11 else "",
                    "store_location":              row[12] if len(row) > 12 else "",
                    "auto_approve_limit_jpy":       int(row[13]) if len(row) > 13 and row[13].isdigit() else 0,
                    "confirm_required_above_jpy":   int(row[14]) if len(row) > 14 and row[14].isdigit() else 0,
                    "spec_conditions":             row[15] if len(row) > 15 else "",
                    "item_nature":                 row[16] if len(row) > 16 else "",
                    "notes":                       row[17] if len(row) > 17 else "",
                    "acknowledged_risk":           row[18] if len(row) > 18 else "",
                    "created_by_discord_id":       row[19] if len(row) > 19 else "",
                    "created_at":                  row[20] if len(row) > 20 else "",
                    "updated_at":                  row[21] if len(row) > 21 else "",
                })
        _purchases_cache = purchases
        return _purchases_cache
    except Exception:
        return []

def get_purchase_next_id():
    ws = get_purchases_ws()
    rows = ws.get_all_values()
    ids = [int(row[0]) for row in rows[1:] if row and row[0].isdigit()]
    return max(ids) + 1 if ids else 1

def add_purchase(agent_discord_id, agent_display_name, creation_source,
                  requester_discord_id, requester_display_name, requester_line_id,
                  item_description, image_url_1, image_url_2, image_url_3,
                  store_location, auto_approve_limit_jpy, confirm_required_above_jpy,
                  spec_conditions, item_nature, notes, created_by_discord_id):
    global _purchases_cache
    ws = get_purchases_ws()
    next_id = get_purchase_next_id()
    now = get_taiwan_now().strftime("%Y-%m-%d %H:%M:%S")
    ws.append_row([
        next_id, "pending", agent_discord_id, agent_display_name, creation_source,
        requester_discord_id, requester_display_name, requester_line_id,
        item_description, image_url_1, image_url_2, image_url_3,
        store_location, auto_approve_limit_jpy, confirm_required_above_jpy,
        spec_conditions, item_nature, notes, "TRUE", created_by_discord_id,
        now, now,
    ])
    _purchases_cache = None
    return next_id

def update_purchase_status_by_id(purchase_id, new_status):
    global _purchases_cache
    ws = get_purchases_ws()
    rows = ws.get_all_values()
    now = get_taiwan_now().strftime("%Y-%m-%d %H:%M:%S")
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(purchase_id):
            ws.update(f"B{i+1}", [[new_status]])
            ws.update(f"V{i+1}", [[now]])
            _purchases_cache = None
            return True
    return False

def update_purchase_details_by_id(purchase_id, requester_line_id, item_description, store_location,
                                    auto_approve_limit_jpy, confirm_required_above_jpy,
                                    spec_conditions, item_nature, notes):
    global _purchases_cache
    ws = get_purchases_ws()
    rows = ws.get_all_values()
    now = get_taiwan_now().strftime("%Y-%m-%d %H:%M:%S")
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(purchase_id):
            ws.update(f"H{i+1}:I{i+1}", [[requester_line_id, item_description]])
            ws.update(f"M{i+1}:O{i+1}", [[store_location, auto_approve_limit_jpy, confirm_required_above_jpy]])
            ws.update(f"P{i+1}:R{i+1}", [[spec_conditions, item_nature, notes]])
            ws.update(f"V{i+1}", [[now]])
            _purchases_cache = None
            return True
    return False

def delete_purchase_by_id(purchase_id):
    global _purchases_cache
    ws = get_purchases_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(purchase_id):
            ws.delete_rows(i + 1)
            _purchases_cache = None
            return True
    return False

def delete_all_purchases_for_agent(agent_discord_id):
    global _purchases_cache
    ws = get_purchases_ws()
    rows = ws.get_all_values()
    row_numbers = [i + 1 for i, row in enumerate(rows) if i > 0 and row and len(row) > 2 and row[2] == str(agent_discord_id)]
    for row_number in reversed(row_numbers):
        ws.delete_rows(row_number)
    _purchases_cache = None
    return len(row_numbers)

# ==================== 代購行程資料讀寫（Google Sheets）====================
def load_trips():
    global _trips_cache
    if _trips_cache is not None:
        return _trips_cache
    try:
        ws = get_trips_ws()
        rows = ws.get_all_values()
        trips = []
        for row in rows[1:]:
            if row and row[0]:
                trips.append({
                    "agent_discord_id":   row[0],
                    "agent_display_name": row[1] if len(row) > 1 else "",
                    "itinerary_text":     row[2] if len(row) > 2 else "",
                    "started_at":         row[3] if len(row) > 3 else "",
                })
        _trips_cache = trips
        return _trips_cache
    except Exception:
        return []

def get_trip_for_agent(agent_discord_id):
    return next((t for t in load_trips() if t["agent_discord_id"] == str(agent_discord_id)), None)

def set_trip(agent_discord_id, agent_display_name, itinerary_text):
    global _trips_cache
    ws = get_trips_ws()
    rows = ws.get_all_values()
    now = get_taiwan_now().strftime("%Y-%m-%d %H:%M:%S")
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(agent_discord_id):
            ws.update(f"A{i+1}:D{i+1}", [[str(agent_discord_id), agent_display_name, itinerary_text, now]])
            _trips_cache = None
            return
    ws.append_row([str(agent_discord_id), agent_display_name, itinerary_text, now])
    _trips_cache = None

def clear_trip(agent_discord_id):
    global _trips_cache
    ws = get_trips_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(agent_discord_id):
            ws.delete_rows(i + 1)
            _trips_cache = None
            return True
    return False

def archive_purchases_to_history(purchases, trip):
    if not purchases:
        return
    ws = get_trip_history_ws()
    now = get_taiwan_now().strftime("%Y-%m-%d %H:%M:%S")
    itinerary_text = trip.get("itinerary_text", "") if trip else ""
    trip_started_at = trip.get("started_at", "") if trip else ""
    rows = [
        [
            p["id"], p["status"], p["agent_discord_id"], p["agent_display_name"], p["creation_source"],
            p["requester_discord_id"], p["requester_display_name"], p["requester_line_id"],
            p["item_description"], p["image_url_1"], p["image_url_2"], p["image_url_3"],
            p["store_location"], p["auto_approve_limit_jpy"], p["confirm_required_above_jpy"],
            p["spec_conditions"], p["item_nature"], p["notes"], p["acknowledged_risk"],
            p["created_by_discord_id"], p["created_at"], p["updated_at"],
            itinerary_text, trip_started_at, now,
        ]
        for p in purchases
    ]
    ws.append_rows(rows)

# ==================== Bot 設定 ====================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print(f"✅ 斜線指令同步請求已送出")

bot = MyBot()

# ==================== Imgur 圖片上傳 ====================
async def upload_to_imgbb(attachment: discord.Attachment) -> str:
    """下載 Discord 附件並上傳至 imgbb，回傳永久直連 URL。失敗時拋出例外。"""
    if not IMGBB_API_KEY:
        raise ValueError("尚未設定 IMGBB_API_KEY 環境變數")
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(attachment.url) as resp:
            image_data = await resp.read()
        encoded = base64.b64encode(image_data).decode("utf-8")
        payload = {"key": IMGBB_API_KEY, "image": encoded}
        async with session.post("https://api.imgbb.com/1/upload", data=payload) as resp:
            result = await resp.json()
            if not result.get("success"):
                raise RuntimeError(f"imgbb 上傳失敗：{result}")
            return result["data"]["url"]

# ==================== 核心資料處理函式 ====================
def get_next_id(expense_ws):
    records = expense_ws.get_all_values()
    if len(records) <= 1:
        return 1
    ids = [int(row[0]) for row in records[1:] if row and row[0].isdigit()]
    return max(ids) + 1 if ids else 1

def save_record(debtor, creditor, amount, rate, item):
    expense_ws, _ = get_worksheets()
    next_id = get_next_id(expense_ws)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    expense_ws.append_row([next_id, now, debtor, creditor, amount, rate, item])
    return next_id

def load_records():
    expense_ws, _ = get_worksheets()
    rows = expense_ws.get_all_values()
    records = []
    for row in rows[1:]:
        if row and row[0]:
            records.append({
                "id":       int(row[0]),
                "time":     row[1],
                "debtor":   row[2],
                "creditor": row[3],
                "amount":   float(row[4]),
                "rate":     float(row[5]),
                "item":     row[6]
            })
    return sorted(records, key=lambda x: x["id"])

def delete_record_by_id(record_id):
    expense_ws, _ = get_worksheets()
    rows = expense_ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(record_id):
            expense_ws.delete_rows(i + 1)
            return True
    return False

def update_record_by_id(record_id, debtor, creditor, amount, rate, item):
    expense_ws, _ = get_worksheets()
    rows = expense_ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == str(record_id):
            expense_ws.update(f"A{i+1}:G{i+1}", [[row[0], row[1], debtor, creditor, amount, rate, item]])
            return True
    return False

def has_pending_records(name):
    records = load_records()
    return any(r["debtor"] == name or r["creditor"] == name for r in records)

# ==================== 年度回顧統計 ====================
TAIPEI_TZ = pytz.timezone("Asia/Taipei")

def get_taiwan_now():
    return datetime.now(pytz.utc).astimezone(TAIPEI_TZ)

def taiwan_datetime_of(time_str: str):
    naive = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    return pytz.utc.localize(naive).astimezone(TAIPEI_TZ)

def taiwan_year_of(time_str: str) -> int:
    return taiwan_datetime_of(time_str).year

def load_history_records():
    _, history_ws = get_worksheets()
    rows = history_ws.get_all_values()
    records = []
    for row in rows[1:]:
        if row and row[0]:
            records.append({
                "id": int(row[0]), "time": row[1], "clear_time": row[2],
                "debtor": row[3], "creditor": row[4],
                "amount": float(row[5]), "rate": float(row[6]), "item": row[7]
            })
    return records

def load_records_by_year(year: int):
    combined = [r for r in load_records() if taiwan_year_of(r["time"]) == year]
    combined += [r for r in load_history_records() if taiwan_year_of(r["time"]) == year]
    return combined

def build_annual_review_chunks(year: int, records: list) -> list:
    pair_groups = {}
    for r in records:
        p1, p2 = sorted([r["debtor"], r["creditor"]])
        pair_groups.setdefault((p1, p2), []).append(r)

    total_count = len(records)
    total_amount = sum(r["amount"] * r["rate"] for r in records)
    header = (f"📅 **{year} 年度回顧**\n" + "─" * 24 +
              f"\n📈 全年總覽：共 {total_count} 筆交易，總金額 {round(total_amount)} NTD\n" + "─" * 24 + "\n")

    blocks = []
    for (p1, p2), group in sorted(pair_groups.items()):
        owe = {p1: 0.0, p2: 0.0}
        for r in group:
            owe[r["debtor"]] += r["amount"] * r["rate"]
        diff = owe[p1] - owe[p2]
        if round(diff) == 0:
            net_desc = "雙方年度打平 ⚖️"
        elif diff > 0:
            net_desc = f"+ {p1} 還欠 {p2} {round(diff)} NTD"
        else:
            net_desc = f"+ {p2} 還欠 {p1} {round(abs(diff))} NTD"

        top = max(group, key=lambda r: r["amount"] * r["rate"])
        top_date = taiwan_datetime_of(top["time"]).strftime("%Y/%m/%d")
        top_amount = round(top["amount"] * top["rate"])

        info_block = (
            "```\n"
            f"👥 {p1} vs {p2}\n"
            f"來往筆數：{len(group)} 筆\n"
            f"最大單筆金額是{top_date}的\"{top['item']}\"，該筆{top['debtor']}欠{top['creditor']}:{top_amount}元\n"
            f"{p1} 欠 {p2}：{round(owe[p1])} NTD\n"
            f"{p2} 欠 {p1}：{round(owe[p2])} NTD\n"
            "```\n"
        )
        diff_block = f"```diff\n{net_desc}\n```\n"
        blocks.append(info_block + diff_block)

    chunks, current = [], header
    for block in blocks:
        if len(current) + len(block) > 1800:
            chunks.append(current)
            current = block
        else:
            current += block
    chunks.append(current)
    return chunks

# ==================== 中文完美對齊演算法 ====================
def get_display_width(text):
    width = 0
    for char in str(text):
        if ord(char) > 127:
            width += 2
        else:
            width += 1
    return width

def align_text(text, width, align):
    text = str(text)
    text_width = get_display_width(text)
    padding = max(0, width - text_width)
    if align == 'left':
        return text + " " * padding
    elif align == 'right':
        return " " * padding + text
    elif align == 'center':
        left_pad = padding // 2
        right_pad = padding - left_pad
        return " " * left_pad + text + " " * right_pad

def format_rate(rate):
    int_digits = len(str(int(rate)))
    decimal_places = max(0, 4 - int_digits)
    return f"{rate:.{decimal_places}f}"

# ==================== 餐盤回收區排版繪製邏輯 ====================
async def update_recovery_board():
    config = load_config()
    channel = bot.get_channel(config.get("recovery_channel_id", 0))
    if not channel:
        print(f"❌ 找不到餐盤回收區頻道")
        return

    try:
        await channel.purge(limit=100)
    except Exception as e:
        print(f"⚠️ 自動清空失敗: {e}")
        async for message in channel.history(limit=50):
            try: await message.delete()
            except: pass

    try:
        history_ws = get_spreadsheet().worksheet("history")
        rows = history_ws.get_all_values()
        records = []
        for row in rows[1:]:
            if row and row[0]:
                records.append({
                    "id":           int(row[0]),
                    "record_time":  row[1],
                    "clear_time":   row[2],
                    "debtor":       row[3],
                    "creditor":     row[4],
                    "amount":       float(row[5]),
                    "rate":         float(row[6]),
                    "item":         row[7]
                })
    except Exception as e:
        await channel.send(f"❌ 讀取歷史資料失敗：{e}")
        return

    if not records:
        await channel.send("✨ 目前尚無任何結清紀錄！")
        return

    records = sorted(records, key=lambda x: x["id"])

    pair_groups = {}
    for r in records:
        p1, p2 = sorted([r["debtor"], r["creditor"]])
        pair_key = f"{p1} vs {p2}"
        if pair_key not in pair_groups: pair_groups[pair_key] = []
        pair_groups[pair_key].append(r)

    for pair_key, group_records in pair_groups.items():
        p1, p2 = pair_key.split(" vs ")

        data_lines = []
        for r in group_records:
            ntd = math.ceil(r['amount'] * r['rate'])
            ntd_str = str(ntd)
            clear_date = r['clear_time'].replace("-", "").replace(" ", "")[2:8]

            c_id       = align_text(f"{r['id']:03d}", 6, 'center')
            c_debtor   = align_text(r['debtor'],      6, 'center')
            c_creditor = align_text(r['creditor'],    8, 'center')
            c_ntd      = " " + align_text(ntd_str[:5], 6, 'left')
            c_date     = f"{clear_date}"

            raw_item = r["item"][:12] + ".." if len(r["item"]) > 12 else r["item"]
            c_item   = f" {raw_item}"
            data_lines.append(f"{c_id}{c_debtor}{c_creditor}{c_ntd}{c_date}{c_item}")

        # 分批發送
        header = "```\n" + pair_key + "\n" + "-" * 62 + "\n" + "|編號||欠債人||債權人||料金| |時間| |項目|\n"
        current_msg = header

        for line in data_lines:
            test_msg = current_msg + line + "\n" + "-" * 62 + "```"
            if len(test_msg) > 1800:
                await channel.send(current_msg + "-" * 62 + "```")
                current_msg = header
            current_msg += line + "\n"

        await channel.send(current_msg + "-" * 62 + "```")

# ==================== 收銀台排版繪製邏輯 ====================
async def update_cashier_board():
    config = load_config()
    channel = bot.get_channel(config.get("cashier_channel_id", 0))
    if not channel:
        print(f"❌ 找不到收銀台頻道，請用 /設定頻道 設定")
        return

    try:
        await channel.purge(limit=100)
    except Exception as e:
        print(f"⚠️ 自動清空失敗: {e}")
        async for message in channel.history(limit=50):
            try: await message.delete()
            except: pass

    try:
        records = load_records()
    except Exception as e:
        await channel.send(f"❌ 讀取資料失敗：{e}")
        return

    if not records:
        await channel.send("✨ 目前尚無任何借貸紀錄，收銀台空空如也！")
        return

    pair_groups = {}
    for r in records:
        p1, p2 = sorted([r["debtor"], r["creditor"]])
        pair_key = f"{p1} vs {p2}"
        if pair_key not in pair_groups: pair_groups[pair_key] = []
        pair_groups[pair_key].append(r)

    for pair_key, group_records in pair_groups.items():
        p1, p2 = pair_key.split(" vs ")

        table_lines = []
        table_lines.append(f"```\n{pair_key}")
        table_lines.append("-" * 62)
        table_lines.append("|編號|  |欠債人|  |債權人|  |金額|  |匯率|   |項目|")

        net_balances = {p1: 0.0, p2: 0.0}
        for r in group_records:
            amt_str  = str(int(r['amount'])) if r['amount'].is_integer() else str(r['amount'])
            rate_str = format_rate(r['rate'])

            c_id       = align_text(f"{r['id']:03d}", 6, 'center')
            c_debtor   = align_text(r['debtor'],      6, 'center')
            c_creditor = "  " + align_text(r['creditor'], 6, 'center')
            c_amount   = " " + align_text(amt_str[:4], 5, 'left')
            c_rate     = align_text(rate_str[:5],     6, 'left')

            raw_item = r["item"][:12] + ".." if len(r["item"]) > 12 else r["item"]
            c_item   = " " + raw_item

            table_lines.append(f"{c_id}  {c_debtor}  {c_creditor}  {c_amount}  {c_rate}  {c_item}")
            net_balances[r["debtor"]] += (r["amount"] * r["rate"])

        table_lines.append("-" * 62 + "```")

        diff = net_balances[p1] - net_balances[p2]
        if round(diff) == 0:
            subtotal_text = " 雙方帳務剛好平手 ⚖️"
        elif diff > 0:
            subtotal_text = f"- {p1} 要給 {p2} ： {round(diff)} NTD"
        else:
            subtotal_text = f"- {p2} 要給 {p1} ： {round(abs(diff))} NTD"

        subtotal_block = f"```diff\n{subtotal_text}\n```"
        await channel.send("\n".join(table_lines) + "\n" + subtotal_block)

# ==================== 查帳彙總邏輯 ====================
def build_account_summary(name):
    records = load_records()
    # 計算與每個人之間的淨額（正數代表 name 欠對方，負數代表對方欠 name）
    net_with = {}

    for r in records:
        amount_ntd = r["amount"] * r["rate"]
        if r["debtor"] == name:
            other = r["creditor"]
            net_with[other] = net_with.get(other, 0) + amount_ntd
        elif r["creditor"] == name:
            other = r["debtor"]
            net_with[other] = net_with.get(other, 0) - amount_ntd

    owe_others = {p: amt for p, amt in net_with.items() if amt > 0}
    others_owe = {p: -amt for p, amt in net_with.items() if amt < 0}

    lines = [f"📊 **{name} 的帳務彙總**", "─" * 24]

    if owe_others:
        lines.append("💸 **欠別人的錢：**")
        for person, amt in owe_others.items():
            lines.append(f"　• 欠 {person}：{round(amt)} NTD")
        lines.append(f"　小計：**{round(sum(owe_others.values()))} NTD**")
    else:
        lines.append("💸 **欠別人的錢：** 無")

    lines.append("")

    if others_owe:
        lines.append("💰 **別人欠他的錢：**")
        for person, amt in others_owe.items():
            lines.append(f"　• {person} 欠他：{round(amt)} NTD")
        lines.append(f"　小計：**{round(sum(others_owe.values()))} NTD**")
    else:
        lines.append("💰 **別人欠他的錢：** 無")

    lines.append("")
    total_owe  = round(sum(owe_others.values()))
    total_owed = round(sum(others_owe.values()))
    net = total_owe - total_owed

    if net > 0:
        lines.append(f"🔢 **淨額：{name} 還需支付 {net} NTD**")
    elif net < 0:
        lines.append(f"🔢 **淨額：{name} 還可以收回 {abs(net)} NTD**")
    else:
        lines.append(f"🔢 **淨額：{name} 帳務平衡 ⚖️**")

    lines.append("─" * 24)
    return "\n".join(lines)

# ==================== 成員管理介面 ====================
def build_members_display():
    data = load_members_data()
    members = data["members"]
    discord_map = data["discord_map"]
    name_to_id = {v: k for k, v in discord_map.items()}

    lines = ["👥 **成員對應表**", "─" * 24]
    for i, name in enumerate(members, 1):
        discord_id = name_to_id.get(name, None)
        if discord_id:
            lines.append(f"{i}. {name}　→　<@{discord_id}>")
        else:
            lines.append(f"{i}. {name}　→　（未設定）")
    if not members:
        lines.append("（尚無成員）")
    return "\n".join(lines)

class MembersManageView(discord.ui.View):
    def __init__(self, is_admin: bool):
        super().__init__(timeout=120)
        self.is_admin = is_admin
        if not is_admin:
            self.remove_item(self.set_mapping)
            self.remove_item(self.rename_member)
            self.remove_item(self.add_member)
            self.remove_item(self.remove_member)

    @discord.ui.button(label="設定對應", style=discord.ButtonStyle.primary)
    async def set_mapping(self, interaction: discord.Interaction, button: discord.ui.Button):
        members = get_members()
        if not members:
            await interaction.response.send_message("⚠️ 尚無成員，請先新增成員！", ephemeral=True)
            return
        await interaction.response.send_message("請選擇要設定對應的成員：", view=MappingSelectView(members), ephemeral=True)

    @discord.ui.button(label="改名", style=discord.ButtonStyle.secondary)
    async def rename_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        members = get_members()
        if not members:
            await interaction.response.send_message("⚠️ 尚無成員！", ephemeral=True)
            return
        await interaction.response.send_message("請選擇要改名的成員：", view=RenameSelectView(members), ephemeral=True)

    @discord.ui.button(label="新增成員", style=discord.ButtonStyle.success)
    async def add_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddMemberModal())

    @discord.ui.button(label="移除成員", style=discord.ButtonStyle.danger)
    async def remove_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        members = get_members()
        if not members:
            await interaction.response.send_message("⚠️ 尚無成員！", ephemeral=True)
            return
        await interaction.response.send_message("請選擇要移除的成員：", view=RemoveSelectView(members), ephemeral=True)

# ==================== 設定對應 ====================
class MappingSelectView(discord.ui.View):
    def __init__(self, members):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="選擇成員",
            options=[discord.SelectOption(label=m, value=m) for m in members]
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SetMappingModal(interaction.data["values"][0]))

class SetMappingModal(discord.ui.Modal, title="設定 Discord 對應"):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.id_input = discord.ui.TextInput(
            label=f"{name} 的 Discord 用戶 ID",
            placeholder="右鍵點擊用戶 → 複製用戶 ID"
        )
        self.add_item(self.id_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = self.id_input.value.strip()
        if not discord_id.isdigit():
            await interaction.followup.send("❌ Discord ID 必須是純數字", ephemeral=True)
            return
        set_discord_mapping(self.name, discord_id)
        await interaction.followup.send(f"✅ 已設定 {self.name} 對應到 <@{discord_id}>", ephemeral=True)

# ==================== 改名 ====================
class RenameSelectView(discord.ui.View):
    def __init__(self, members):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="選擇要改名的成員",
            options=[discord.SelectOption(label=m, value=m) for m in members]
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RenameModal(interaction.data["values"][0]))

class RenameModal(discord.ui.Modal, title="改名"):
    def __init__(self, old_name):
        super().__init__()
        self.old_name = old_name
        self.new_name_input = discord.ui.TextInput(
            label=f"將「{old_name}」改為",
            placeholder="輸入新名字"
        )
        self.add_item(self.new_name_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new_name = self.new_name_input.value.strip()
        if not new_name:
            await interaction.followup.send("❌ 名字不能為空", ephemeral=True)
            return
        data = load_members_data()
        if new_name in data["members"]:
            await interaction.followup.send(f"❌ 「{new_name}」已經存在於成員名單中", ephemeral=True)
            return
        rename_member_in_sheets(self.old_name, new_name)
        await interaction.followup.send(f"✅ 已將「{self.old_name}」改名為「{new_name}」", ephemeral=True)
        await update_cashier_board()

# ==================== 新增成員 ====================
class AddMemberModal(discord.ui.Modal, title="新增成員"):
    def __init__(self):
        super().__init__()
        self.name_input = discord.ui.TextInput(label="新成員名字", placeholder="輸入名字")
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new_name = self.name_input.value.strip()
        if not new_name:
            await interaction.followup.send("❌ 名字不能為空", ephemeral=True)
            return
        data = load_members_data()
        if new_name in data["members"]:
            await interaction.followup.send(f"❌ 「{new_name}」已經存在於成員名單中", ephemeral=True)
            return
        add_member_to_sheet(new_name)
        await interaction.followup.send(f"✅ 已新增成員「{new_name}」", ephemeral=True)

# ==================== 移除成員 ====================
class RemoveSelectView(discord.ui.View):
    def __init__(self, members):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="選擇要移除的成員",
            options=[discord.SelectOption(label=m, value=m) for m in members]
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        name = interaction.data["values"][0]
        if has_pending_records(name):
            await interaction.response.send_message(
                f"❌ **{name}** 還有未結帳目，請先結清後再移除。", ephemeral=True)
            return
        await interaction.response.send_message(
            f"⚠️ 確定要移除成員「{name}」嗎？此操作無法復原。",
            view=ConfirmRemoveView(name), ephemeral=True)

class ConfirmRemoveView(discord.ui.View):
    def __init__(self, name):
        super().__init__(timeout=30)
        self.name = name

    @discord.ui.button(label="確認移除", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        remove_member_from_sheet(self.name)
        await interaction.edit_original_response(content=f"✅ 已移除成員「{self.name}」", view=None)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="已取消移除操作", view=None)

# ==================== 分帳狀態物件 ====================
class SplitState:
    def __init__(self, payer, total_amount, item):
        self.payer        = payer
        self.total_amount = float(total_amount)
        self.item         = item
        self.splits       = {}
        self.message      = None

def build_split_summary(state, hint=""):
    lines = [
        f"💰 **付款人：{state.payer}　總金額：{int(state.total_amount)} NTD　項目：{state.item}**",
        "─" * 24,
    ]
    if state.splits:
        lines.append("📋 目前分帳名單：")
        for name, ratio in state.splits.items():
            amt = math.ceil(state.total_amount * ratio)
            lines.append(f"　• {name}：比例 {ratio} → **{amt} NTD**")
    else:
        lines.append("（尚未加入任何人）")
    if hint:
        lines.append(f"\n{hint}")
    return "\n".join(lines)

# ==================== 分帳流程 ====================
class SplitPayerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.payer = None
        members = get_members()
        if members:
            payer_select = discord.ui.Select(
                placeholder="選擇付錢的人",
                options=[discord.SelectOption(label=m, value=m) for m in members]
            )
            payer_select.callback = self.payer_callback
            self.add_item(payer_select)

    async def payer_callback(self, interaction: discord.Interaction):
        self.payer = interaction.data["values"][0]
        await interaction.response.defer()

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary, row=1)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.payer:
            await interaction.response.send_message("⚠️ 請先選擇付錢的人！", ephemeral=True)
            return
        await interaction.response.send_modal(SplitAmountModal(self.payer))

class SplitAmountModal(discord.ui.Modal, title="填寫分帳資訊"):
    def __init__(self, payer):
        super().__init__()
        self.payer = payer
        self.amount_input = discord.ui.TextInput(label="總金額", placeholder="請填寫數字，例如：3000")
        self.item_input   = discord.ui.TextInput(label="項目名稱", placeholder="例如：晚餐")
        self.add_item(self.amount_input)
        self.add_item(self.item_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            total = float(self.amount_input.value)
            if total <= 0:
                await interaction.response.send_message("❌ 金額必須大於 0", ephemeral=True)
                return
            item    = self.item_input.value.strip()
            state   = SplitState(self.payer, total, item)
            view    = SplitPersonView(state)
            content = build_split_summary(state)
            await interaction.response.send_message(content=content, view=view, ephemeral=True)
            state.message = await interaction.original_response()
        except ValueError:
            await interaction.response.send_message("❌ 金額必須是數字", ephemeral=True)

class SplitPersonView(discord.ui.View):
    def __init__(self, state: SplitState):
        super().__init__(timeout=300)
        self.state           = state
        self.selected_person = None
        options = [discord.SelectOption(label=m, value=m) for m in get_members() if m != state.payer]
        if options:
            self.person_select = discord.ui.Select(placeholder="選擇要加入的分帳人", options=options)
            self.person_select.callback = self.person_callback
            self.add_item(self.person_select)

    async def person_callback(self, interaction: discord.Interaction):
        self.selected_person = interaction.data["values"][0]
        if self.selected_person in self.state.splits:
            hint = (
                f"👤 現在選擇的欠債者是：**{self.selected_person}**\n"
                f"⚠️ 此人已填寫過，目前比例為 **{self.state.splits[self.selected_person]}**，送出後將覆蓋。"
            )
        else:
            hint = f"👤 現在選擇的欠債者是：**{self.selected_person}**"
        await interaction.response.edit_message(content=build_split_summary(self.state, hint), view=self)

    @discord.ui.button(label="加入此人 →", style=discord.ButtonStyle.primary, row=1)
    async def add_person(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_person:
            await interaction.response.send_message("⚠️ 請先選擇分帳人！", ephemeral=True)
            return
        await interaction.response.send_modal(SplitRatioModal(self.state, self.selected_person))

    @discord.ui.button(label="✅ 完成分帳", style=discord.ButtonStyle.success, row=1)
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.state.splits:
            await interaction.response.send_message("⚠️ 請至少加入一位分帳人！", ephemeral=True)
            return
        await finalize_split(interaction, self.state)

class SplitRatioModal(discord.ui.Modal, title="填寫分帳比例"):
    def __init__(self, state: SplitState, person: str):
        super().__init__()
        self.state  = state
        self.person = person
        old_ratio = str(self.state.splits[person]) if person in self.state.splits else ""
        self.ratio_input = discord.ui.TextInput(
            label=f"{person} 的比例（例：0.2 代表 20%）",
            placeholder="請填寫小數，例如 0.2",
            default=old_ratio
        )
        self.add_item(self.ratio_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            ratio = float(self.ratio_input.value)
            if ratio <= 0:
                await interaction.followup.send("❌ 比例必須大於 0", ephemeral=True)
                return
            self.state.splits[self.person] = ratio
            await self.state.message.edit(content=build_split_summary(self.state), view=SplitPersonView(self.state))
        except ValueError:
            await interaction.followup.send("❌ 比例必須是數字", ephemeral=True)

async def finalize_split(interaction: discord.Interaction, state: SplitState):
    await interaction.response.defer()
    added_lines = []
    for name, ratio in state.splits.items():
        amount = math.ceil(state.total_amount * ratio)
        new_id = save_record(name, state.payer, amount, 1, state.item)
        added_lines.append(f"　[編號 {new_id:03d}] {name} 欠 {state.payer} **{amount} NTD**")
    result = "✅ **分帳完成！已新增以下記錄：**\n" + "\n".join(added_lines)
    await interaction.edit_original_response(content=result, view=None)
    await update_cashier_board()

# ==================== 兩段式記帳流程 ====================
class RecordModal(discord.ui.Modal, title="新增記帳資料"):
    def __init__(self, debtor: str, creditor: str):
        super().__init__()
        self.debtor   = debtor
        self.creditor = creditor
        self.item_input   = discord.ui.TextInput(label="項目名稱", placeholder="例如：晚餐")
        self.amount_input = discord.ui.TextInput(label="金額", placeholder="請填寫數字")
        self.rate_input   = discord.ui.TextInput(label="匯率", placeholder="台幣請填 1", default="1")
        self.add_item(self.item_input)
        self.add_item(self.amount_input)
        self.add_item(self.rate_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            amt = float(self.amount_input.value)
            rt  = float(self.rate_input.value)
            new_id = save_record(self.debtor, self.creditor, amt, rt, self.item_input.value)
            await interaction.followup.send(f"✅ [編號 {new_id:03d}] 紀錄成功！", ephemeral=True)
            await update_cashier_board()
        except ValueError:
            await interaction.followup.send("❌ 金額與匯率必須是數字", ephemeral=True)

class NameSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.debtor   = None
        self.creditor = None
        members = get_members()
        if members:
            debtor_select = discord.ui.Select(
                placeholder="① 選擇欠債人（誰要付錢）",
                options=[discord.SelectOption(label=m, value=m) for m in members],
                custom_id="debtor"
            )
            debtor_select.callback = self.debtor_callback
            self.add_item(debtor_select)
            creditor_select = discord.ui.Select(
                placeholder="② 選擇債權人（誰先墊錢）",
                options=[discord.SelectOption(label=m, value=m) for m in members],
                custom_id="creditor"
            )
            creditor_select.callback = self.creditor_callback
            self.add_item(creditor_select)

    async def debtor_callback(self, interaction: discord.Interaction):
        self.debtor = interaction.data["values"][0]
        await interaction.response.defer()

    async def creditor_callback(self, interaction: discord.Interaction):
        self.creditor = interaction.data["values"][0]
        await interaction.response.defer()

    @discord.ui.button(label="確認，開啟記帳表單 →", style=discord.ButtonStyle.primary, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.debtor or not self.creditor:
            await interaction.response.send_message("⚠️ 請先選擇欠債人和債權人！", ephemeral=True)
            return
        if self.debtor == self.creditor:
            await interaction.response.send_message("⚠️ 欠債人和債權人不能是同一個人！", ephemeral=True)
            return
        await interaction.response.send_modal(RecordModal(self.debtor, self.creditor))

# ==================== 修改用 Modal ====================
class ModifyModal(discord.ui.Modal):
    def __init__(self, record_id, old_data):
        super().__init__(title=f"修改帳目 - 編號 {record_id:03d}")
        self.record_id = record_id
        amt_val  = str(int(old_data['amount'])) if old_data['amount'].is_integer() else str(old_data['amount'])
        rate_val = str(int(old_data['rate']))   if old_data['rate'].is_integer()   else str(old_data['rate'])
        self.debtor_input   = discord.ui.TextInput(label=f"欠債人（目前：{old_data['debtor']}）", default=old_data['debtor'])
        self.creditor_input = discord.ui.TextInput(label=f"債權人（目前：{old_data['creditor']}）", default=old_data['creditor'])
        self.item_input     = discord.ui.TextInput(label="項目名稱", default=old_data['item'])
        self.amount_input   = discord.ui.TextInput(label="金額", default=amt_val)
        self.rate_input     = discord.ui.TextInput(label="匯率", default=rate_val)
        self.add_item(self.debtor_input)
        self.add_item(self.creditor_input)
        self.add_item(self.item_input)
        self.add_item(self.amount_input)
        self.add_item(self.rate_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        d = self.debtor_input.value.strip()
        c = self.creditor_input.value.strip()
        members = get_members()
        if d not in members or c not in members:
            await interaction.followup.send(f"❌ 名字錯誤！合法名單：{', '.join(members)}", ephemeral=True)
            return
        try:
            amt, rt = float(self.amount_input.value), float(self.rate_input.value)
            update_record_by_id(self.record_id, d, c, amt, rt, self.item_input.value)
            await interaction.followup.send(f"✏️ 編號 {self.record_id:03d} 已修改成功！", ephemeral=True)
            await update_cashier_board()
        except ValueError:
            await interaction.followup.send("❌ 金額與匯率必須是數字", ephemeral=True)

# ==================== 查帳 View ====================
class AccountSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        members = get_members()
        if members:
            select = discord.ui.Select(
                placeholder="選擇要查帳的人",
                options=[discord.SelectOption(label=m, value=m) for m in members]
            )
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        name = interaction.data["values"][0]
        summary = build_account_summary(name)
        await interaction.response.edit_message(content="✅ 查詢完成！", view=None)
        await interaction.channel.send(summary)

# ==================== 設定頻道 Modal ====================
class SetChannelsModal(discord.ui.Modal, title="設定頻道 ID"):
    def __init__(self):
        super().__init__()
        config = load_config()
        self.cashier_input = discord.ui.TextInput(
            label="收銀台頻道 ID",
            placeholder="右鍵點擊頻道 → 複製頻道 ID",
            default=str(config.get("cashier_channel_id", "")) if config.get("cashier_channel_id") else ""
        )
        self.recovery_input = discord.ui.TextInput(
            label="餐盤回收區頻道 ID",
            placeholder="右鍵點擊頻道 → 複製頻道 ID",
            default=str(config.get("recovery_channel_id", "")) if config.get("recovery_channel_id") else ""
        )
        self.notify_input = discord.ui.TextInput(
            label="每日播報／提醒頻道 ID",
            placeholder="右鍵點擊頻道 → 複製頻道 ID",
            default=str(config.get("notify_channel_id", NOTIFY_CHANNEL_ID))
        )
        self.add_item(self.cashier_input)
        self.add_item(self.recovery_input)
        self.add_item(self.notify_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cashier_id  = int(self.cashier_input.value.strip())
            recovery_id = int(self.recovery_input.value.strip())
            notify_id   = int(self.notify_input.value.strip())
            save_config("cashier_channel_id", cashier_id)
            save_config("recovery_channel_id", recovery_id)
            save_config("notify_channel_id", notify_id)
            await interaction.followup.send("✅ 頻道設定完成！", ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ 頻道 ID 必須是純數字", ephemeral=True)

# ==================== 設定試算表 Modal ====================
class SetSheetModal(discord.ui.Modal, title="設定 Google Sheet ID"):
    def __init__(self):
        super().__init__()
        self.sheet_input = discord.ui.TextInput(
            label="Google Sheet ID",
            placeholder="試算表網址中間那串字"
        )
        self.add_item(self.sheet_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sheet_id = self.sheet_input.value.strip()
        save_config("sheet_id", sheet_id)
        await interaction.followup.send("✅ 試算表 ID 設定完成！", ephemeral=True)

# ==================== 斜線指令系統 ====================
async def member_autocomplete(interaction: discord.Interaction, current: str):
    members = get_members()
    return [
        app_commands.Choice(name=m, value=m)
        for m in members if current.lower() in m.lower()
    ][:25]

@bot.tree.command(name="記帳", description="快速新增記帳 (直接按 Tab 填寫，電腦端推薦)")
@app_commands.describe(debtor="欠債人", creditor="債權人", amount="金額", rate="匯率", item="項目")
@app_commands.autocomplete(debtor=member_autocomplete, creditor=member_autocomplete)
async def slash_record(interaction: discord.Interaction, debtor: str, creditor: str, amount: float, rate: float, item: str):
    members = get_members()
    if debtor not in members or creditor not in members:
        await interaction.response.send_message(f"❌ 名字錯誤！合法名單：{', '.join(members)}", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    new_id = save_record(debtor, creditor, amount, rate, item)
    await interaction.followup.send(f"✅ [編號 {new_id:03d}] 快速紀錄成功！", ephemeral=True)
    await update_cashier_board()

@bot.tree.command(name="表單記帳", description="開啟下拉選單選人，再填寫金額 (手機端推薦)")
async def slash_modal(interaction: discord.Interaction):
    members = get_members()
    if not members:
        await interaction.response.send_message("⚠️ 尚無成員，請管理員先執行 /成員管理 新增成員！", ephemeral=True)
        return
    await interaction.response.send_message("📋 **步驟一：選擇欠債人和債權人**", view=NameSelectView(), ephemeral=True)

@bot.tree.command(name="分帳", description="多人分帳，支援自訂比例")
async def slash_split(interaction: discord.Interaction):
    members = get_members()
    if not members:
        await interaction.response.send_message("⚠️ 尚無成員，請管理員先執行 /成員管理 新增成員！", ephemeral=True)
        return
    await interaction.response.send_message("💸 **分帳 — 步驟一：選擇付錢的人**", view=SplitPayerView(), ephemeral=True)

class AdminToolsView(discord.ui.View):
    def __init__(self, is_admin: bool):
        super().__init__(timeout=120)
        self.is_admin = is_admin
        self.choice = "members"
        options = [discord.SelectOption(label="👥 成員管理", value="members", default=True)]
        if is_admin:
            options.append(discord.SelectOption(label="📺 設定頻道", value="channels"))
            options.append(discord.SelectOption(label="📊 設定試算表", value="sheet"))
        select = discord.ui.Select(placeholder="你想要...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        self.choice = interaction.data["values"][0]
        await interaction.response.defer()

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary, row=1)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.choice == "members":
            await interaction.response.edit_message(content=build_members_display(), view=MembersManageView(self.is_admin))
        elif self.choice == "channels":
            await interaction.response.send_modal(SetChannelsModal())
        elif self.choice == "sheet":
            await interaction.response.send_modal(SetSheetModal())

@bot.tree.command(name="管理者工具", description="成員管理／設定頻道／設定試算表")
async def slash_admin_tools(interaction: discord.Interaction):
    is_admin = interaction.user.guild_permissions.administrator
    await interaction.response.send_message("🛠️ **管理者工具** — 請選擇要做什麼：", view=AdminToolsView(is_admin), ephemeral=True)

@bot.tree.command(name="修改帳目", description="修改特定編號的帳目內容")
async def slash_modify(interaction: discord.Interaction, 編號: int):
    await interaction.response.defer(ephemeral=True)
    records = load_records()
    old_data = next((r for r in records if r['id'] == 編號), None)
    if not old_data:
        await interaction.followup.send(f"❌ 找不到編號 {編號} 的紀錄。", ephemeral=True)
        return
    await interaction.followup.send("請填寫修改內容：", ephemeral=True)
    await interaction.response.send_modal(ModifyModal(編號, old_data))

@bot.tree.command(name="刪除帳目", description="依編號刪除紀錄")
async def slash_delete(interaction: discord.Interaction, 編號: int):
    await interaction.response.defer()
    if delete_record_by_id(編號):
        await interaction.followup.send(f"🗑️ 已刪除編號 {編號:03d}。")
        await update_cashier_board()
    else:
        await interaction.followup.send(f"❌ 找不到編號 {編號}", ephemeral=True)

@bot.tree.command(name="清空收銀台", description="強制清空收銀台頻道內的所有訊息")
async def slash_purge(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 只有管理員才能使用此指令", ephemeral=True)
        return
    config = load_config()
    channel = bot.get_channel(config.get("cashier_channel_id", 0))
    if not channel:
        await interaction.response.send_message("❌ 找不到收銀台頻道，請先用 /設定頻道 設定。", ephemeral=True)
        return
    await interaction.response.send_message("🚀 正在嘗試清空收銀台...", ephemeral=True)
    try:
        deleted = await channel.purge(limit=100)
        await interaction.followup.send(f"✅ 已清理完畢。共刪除 {len(deleted)} 則訊息。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 清空失敗。原因：{e}", ephemeral=True)

@bot.tree.command(name="重新整理", description="重新讀取資料庫並更新收銀台/餐盤回收區表格")
async def slash_refresh(interaction: discord.Interaction):
    global _triggers_cache, _purchases_cache, _trips_cache
    await interaction.response.defer(ephemeral=True)
    _triggers_cache = None
    _purchases_cache = None
    _trips_cache = None
    await update_cashier_board()
    await update_recovery_board()
    await interaction.followup.send("✅ 收銀台與餐盤回收區已更新完畢！觸發詞、代購、行程快取也已清除。", ephemeral=True)

@bot.tree.command(name="查帳", description="查詢某人的帳務彙總（公開顯示）")
async def slash_check(interaction: discord.Interaction):
    members = get_members()
    if not members:
        await interaction.response.send_message("⚠️ 尚無成員！", ephemeral=True)
        return
    await interaction.response.send_message("🔍 請選擇要查帳的人：", view=AccountSelectView(), ephemeral=True)

@bot.tree.command(name="藍白", description="公審本群最大欠債人！")
async def slash_blue_white(interaction: discord.Interaction):
    await interaction.response.defer()
    records = load_records()
    members = get_members()
    net = {m: 0.0 for m in members}
    for r in records:
        amount_ntd = r["amount"] * r["rate"]
        if r["debtor"] in net:
            net[r["debtor"]] += amount_ntd
        if r["creditor"] in net:
            net[r["creditor"]] -= amount_ntd

    max_debt = max(net.values()) if net else 0
    if max_debt <= 0:
        await interaction.channel.send("🎉 本群目前清清白白，無人收錢！")
        await interaction.followup.send("✅ 已發出公告", ephemeral=True)
        return

    top_debtors = [name for name, amt in net.items() if amt == max_debt]
    data = load_members_data()
    name_to_id = {v: k for k, v in data["discord_map"].items()}
    mentions = []
    for name in top_debtors:
        discord_id = name_to_id.get(name)
        mentions.append(f"<@{discord_id}>（{name}）" if discord_id else name)

    message = (
        f"⚖️ **本群目前最大欠債人是 {'、'.join(mentions)}**，"
        f"淨欠款 **{round(max_debt)} NTD**\n"
        f"請盡快通過預算！勿債留子孫！！禍延中華民國台灣 🇹🇼"
    )
    await interaction.channel.send(message)
    await interaction.followup.send("✅ 已發出公告", ephemeral=True)

@bot.tree.command(name="林佳龍", description="心疼本群最大債主！")
async def slash_lin(interaction: discord.Interaction):
    await interaction.response.defer()
    records = load_records()
    members = get_members()
    net = {m: 0.0 for m in members}
    for r in records:
        amount_ntd = r["amount"] * r["rate"]
        if r["debtor"] in net:
            net[r["debtor"]] += amount_ntd
        if r["creditor"] in net:
            net[r["creditor"]] -= amount_ntd

    # 最大債主 = 淨額最小（最負）的人
    min_debt = min(net.values()) if net else 0
    if min_debt >= 0:
        await interaction.channel.send("🤷 本群目前沒有人是債主！")
        await interaction.followup.send("✅ 已發出公告", ephemeral=True)
        return

    top_creditors = [name for name, amt in net.items() if amt == min_debt]
    data = load_members_data()
    name_to_id = {v: k for k, v in data["discord_map"].items()}
    mentions = []
    for name in top_creditors:
        discord_id = name_to_id.get(name)
        mentions.append(f"<@{discord_id}>（{name}）" if discord_id else name)

    target_str = "、".join(mentions)
    message = (
        f"💰 **本群最大債主是 {target_str}**，"
        f"淨借出 **{round(abs(min_debt))} NTD**\n"
        f"心疼 {target_str}，大家都欠他一個道歉，台中人不值得他 🥲"
    )
    await interaction.channel.send(message)
    await interaction.followup.send("✅ 已發出公告", ephemeral=True)

@bot.tree.command(name="結清", description="結清兩人之間的帳務並歸檔")
@app_commands.describe(name1="第一個人", name2="第二個人")
@app_commands.autocomplete(name1=member_autocomplete, name2=member_autocomplete)
async def slash_clear(interaction: discord.Interaction, name1: str, name2: str):
    await interaction.response.defer()
    members = get_members()
    if name1 not in members or name2 not in members:
        await interaction.followup.send(f"❌ 名字錯誤！合法名單：{', '.join(members)}", ephemeral=True)
        return
    n1, n2 = sorted([name1, name2])
    records = load_records()
    target, remaining = [], []
    for r in records:
        rp1, rp2 = sorted([r["debtor"], r["creditor"]])
        if rp1 == n1 and rp2 == n2: target.append(r)
        else: remaining.append(r)

    if not target:
        await interaction.followup.send("🔍 找不到這兩人之間的未結帳目", ephemeral=True)
        return

    net = {n1: 0.0, n2: 0.0}
    recovery = [f"**🟢 {n1} VS {n2} 結清歸檔**", "-" * 30]
    for r in target:
        net[r["debtor"]] += (r["amount"] * r["rate"])
        recovery.append(f"• [編號{r['id']:03d}] {r['debtor']} 欠 {r['creditor']} : {int(r['amount'])} - {r['item']}")

    diff = net[n1] - net[n2]
    subtotal = "雙方平手" if round(diff)==0 else (f"{n1} 支付 {round(diff)} NTD 給 {n2}" if diff>0 else f"{n2} 支付 {round(abs(diff))} NTD 給 {n1}")
    recovery.extend(["-" * 30, f"📊 **小計：{subtotal}**"])


    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _, history_ws = get_worksheets()
    for r in target:
        history_ws.append_row([r["id"], r["time"], now_str, r["debtor"], r["creditor"], r["amount"], r["rate"], r["item"]])

    expense_ws, _ = get_worksheets()
    target_ids = {r["id"] for r in target}
    all_rows = expense_ws.get_all_values()
    rows_to_keep = [all_rows[0]] + [row for row in all_rows[1:] if row and int(row[0]) not in target_ids]
    expense_ws.clear()
    expense_ws.update("A1", rows_to_keep)

    config = load_config()
    recv_channel = bot.get_channel(config.get("recovery_channel_id", 0))
    if recv_channel:
        now_date = datetime.now().strftime("%y%m%d")
        table_lines = []
        table_lines.append(f"```\n{n1} vs {n2}")
        table_lines.append("-" * 62)
        table_lines.append("|編號||欠債人||債權人||料金||時間|    |項目|")
        for r in target:
            amt_str  = str(int(r['amount'])) if r['amount'].is_integer() else str(r['amount'])
            rate_str = format_rate(r['rate'])
            c_id       = align_text(f"{r['id']:03d}", 6, 'center')
            c_debtor   = align_text(r['debtor'],      6, 'center')
            c_creditor = align_text(r['creditor'],    6, 'center')
            ntd = math.ceil(r['amount'] * r['rate'])
            ntd_str = str(ntd)
            c_ntd      = " " + align_text(ntd_str[:5], 5, 'left')
            c_date     = f" {now_date}"
            raw_item = r["item"][:12] + ".." if len(r["item"]) > 12 else r["item"]
            c_item   = f"    {raw_item}"
            table_lines.append(f"{c_id}{c_debtor}{c_creditor}{c_ntd}{c_date}{c_item}")
        table_lines.append("-" * 62 + "```")
        
        # 分批發送，每批不超過 1800 字元
        header = f"```\n{n1} vs {n2}\n" + "-" * 62 + "\n" + "|編號||欠債人||債權人||料金||時間|    |項目|\n"
        current_msg = header
        
        for line in table_lines[3:-1]:
            test_msg = current_msg + line + "\n"
            if len(test_msg) > 1800:
                await recv_channel.send(current_msg + "-" * 62 + "```")
                current_msg = header
            current_msg += line + "\n"
        
        await recv_channel.send(current_msg + "-" * 62 + "```")

    await interaction.followup.send(f"🤝 {n1} 與 {n2} 帳務已結清並歸檔。")
    await update_cashier_board()

@bot.tree.command(name="年度回顧", description="查詢指定年度的雙方借貸總覽（預設今年至今）")
@app_commands.describe(年份="要查詢的西元年份，例如 2025（留空則查詢今年至今）")
async def slash_annual_review(interaction: discord.Interaction, 年份: int = None):
    await interaction.response.defer()
    year = 年份 or get_taiwan_now().year
    records = load_records_by_year(year)
    if not records:
        await interaction.followup.send(f"📅 {year} 年度目前沒有任何借貸紀錄。")
        return
    for chunk in build_annual_review_chunks(year, records):
        await interaction.followup.send(chunk)

# ==================== 新增觸發詞 View + Modal ====================
class AddTriggerView(discord.ui.View):
    def __init__(self, image_url: str = ""):
        super().__init__(timeout=120)
        self.image_url  = image_url
        self.match_type = "contains"
        self.reply_type = "new"

        match_select = discord.ui.Select(
            placeholder="觸發方式",
            options=[
                discord.SelectOption(label="包含（訊息包含關鍵詞）",       value="contains", default=True),
                discord.SelectOption(label="完全相符（整句等於關鍵詞）",   value="exact"),
                discord.SelectOption(label="正則 Regex（如 彰.+人）",       value="regex"),
            ]
        )
        match_select.callback = self.match_callback
        self.add_item(match_select)

        reply_select = discord.ui.Select(
            placeholder="回覆方式",
            options=[
                discord.SelectOption(label="新訊息（獨立發送）",         value="new", default=True),
                discord.SelectOption(label="回覆該則（引用箭頭）",       value="reply"),
            ]
        )
        reply_select.callback = self.reply_callback
        self.add_item(reply_select)

    async def match_callback(self, interaction: discord.Interaction):
        self.match_type = interaction.data["values"][0]
        await interaction.response.defer()

    async def reply_callback(self, interaction: discord.Interaction):
        self.reply_type = interaction.data["values"][0]
        await interaction.response.defer()

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary, row=2)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            AddTriggerModal(self.match_type, self.reply_type, self.image_url)
        )

class AddTriggerModal(discord.ui.Modal, title="新增觸發詞"):
    def __init__(self, match_type: str, reply_type: str, image_url: str):
        super().__init__()
        self.match_type = match_type
        self.reply_type = reply_type
        self.image_url  = image_url
        self.keyword_input  = discord.ui.TextInput(label="觸發關鍵詞", placeholder="例：彰化　或 Regex：彰.+人")
        self.response_input = discord.ui.TextInput(
            label="回覆文字（<> 分隔多個選項，選填）",
            placeholder="例：彥丞道歉<>彰化人不行<>又是你",
            required=False, style=discord.TextStyle.paragraph
        )
        self.mention_input  = discord.ui.TextInput(label="要 @ 的成員名（選填）", placeholder="例：彥丞", required=False)
        self.prob_input     = discord.ui.TextInput(
            label="觸發機率 0–100（多觸發詞競爭時的權重）",
            placeholder="預設 100", default="100", required=False
        )
        self.add_item(self.keyword_input)
        self.add_item(self.response_input)
        self.add_item(self.mention_input)
        self.add_item(self.prob_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        keyword      = self.keyword_input.value.strip()
        response_text = self.response_input.value.strip()
        mention      = self.mention_input.value.strip()
        try:
            prob = int(self.prob_input.value.strip() or "100")
            if not 0 <= prob <= 100: raise ValueError
        except ValueError:
            await interaction.followup.send("❌ 機率必須是 0–100 的整數", ephemeral=True)
            return
        members = get_members()
        if mention and mention not in members:
            await interaction.followup.send(f"❌ 找不到成員「{mention}」，合法名單：{', '.join(members)}", ephemeral=True)
            return
        if not response_text and not self.image_url and not mention:
            await interaction.followup.send("❌ 請至少填寫回覆文字、圖片或 @ 成員其中一項", ephemeral=True)
            return
        new_id = add_trigger(keyword, response_text, self.image_url, mention, self.match_type, self.reply_type, prob)
        ml = {"contains": "包含", "exact": "完全相符", "regex": "正則"}.get(self.match_type, self.match_type)
        rl = {"new": "新訊息", "reply": "回覆該則"}.get(self.reply_type, self.reply_type)
        parts = [f"✅ **[{new_id:03d}] 觸發詞「{keyword}」新增成功！**",
                 f"　觸發方式：{ml}　回覆方式：{rl}　機率：{prob}"]
        if mention:        parts.append(f"　@ {mention}")
        if response_text:  parts.append(f"　文字：{response_text[:50]}{'...' if len(response_text) > 50 else ''}")
        if self.image_url: parts.append(f"　圖片：{self.image_url}")
        await interaction.followup.send("\n".join(parts), ephemeral=True)

# ==================== 編輯觸發詞 View + Modal ====================
class EditTriggerView(discord.ui.View):
    def __init__(self, trigger: dict):
        super().__init__(timeout=120)
        self.trigger    = trigger
        self.trigger_id = trigger["id"]
        self.image_url  = trigger["image_url"]
        self.match_type = trigger["match_type"]
        self.reply_type = trigger["reply_type"]

        match_select = discord.ui.Select(
            placeholder="觸發方式",
            options=[
                discord.SelectOption(label="包含（訊息包含關鍵詞）",       value="contains", default=(trigger["match_type"] == "contains")),
                discord.SelectOption(label="完全相符（整句等於關鍵詞）",   value="exact",    default=(trigger["match_type"] == "exact")),
                discord.SelectOption(label="正則 Regex（如 彰.+人）",       value="regex",    default=(trigger["match_type"] == "regex")),
            ]
        )
        match_select.callback = self.match_callback
        self.add_item(match_select)

        reply_select = discord.ui.Select(
            placeholder="回覆方式",
            options=[
                discord.SelectOption(label="新訊息（獨立發送）",         value="new",   default=(trigger["reply_type"] == "new")),
                discord.SelectOption(label="回覆該則（引用箭頭）",       value="reply", default=(trigger["reply_type"] == "reply")),
            ]
        )
        reply_select.callback = self.reply_callback
        self.add_item(reply_select)

    async def match_callback(self, interaction: discord.Interaction):
        self.match_type = interaction.data["values"][0]
        await interaction.response.defer()

    async def reply_callback(self, interaction: discord.Interaction):
        self.reply_type = interaction.data["values"][0]
        await interaction.response.defer()

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary, row=2)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            EditTriggerModal(self.trigger_id, self.match_type, self.reply_type, self.image_url, self.trigger)
        )

class EditTriggerModal(discord.ui.Modal):
    def __init__(self, trigger_id: int, match_type: str, reply_type: str, image_url: str, existing: dict):
        super().__init__(title=f"編輯觸發詞 - 編號 {trigger_id:03d}")
        self.trigger_id = trigger_id
        self.match_type = match_type
        self.reply_type = reply_type
        self.image_url  = image_url
        self.keyword_input  = discord.ui.TextInput(label="觸發關鍵詞", default=existing["keyword"])
        self.response_input = discord.ui.TextInput(
            label="回覆文字（<> 分隔多個選項，選填）",
            default=existing["response_text"],
            required=False, style=discord.TextStyle.paragraph
        )
        self.mention_input  = discord.ui.TextInput(label="要 @ 的成員名（選填）", default=existing["mention_name"], required=False)
        self.prob_input     = discord.ui.TextInput(
            label="觸發機率 0–100（多觸發詞競爭時的權重）",
            default=str(existing["probability"]), required=False
        )
        self.add_item(self.keyword_input)
        self.add_item(self.response_input)
        self.add_item(self.mention_input)
        self.add_item(self.prob_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        keyword      = self.keyword_input.value.strip()
        response_text = self.response_input.value.strip()
        mention      = self.mention_input.value.strip()
        try:
            prob = int(self.prob_input.value.strip() or "100")
            if not 0 <= prob <= 100: raise ValueError
        except ValueError:
            await interaction.followup.send("❌ 機率必須是 0–100 的整數", ephemeral=True)
            return
        members = get_members()
        if mention and mention not in members:
            await interaction.followup.send(f"❌ 找不到成員「{mention}」，合法名單：{', '.join(members)}", ephemeral=True)
            return
        if not response_text and not self.image_url and not mention:
            await interaction.followup.send("❌ 請至少填寫回覆文字、圖片或 @ 成員其中一項", ephemeral=True)
            return
        ok = update_trigger_by_id(self.trigger_id, keyword, response_text, self.image_url, mention, self.match_type, self.reply_type, prob)
        if not ok:
            await interaction.followup.send(f"❌ 找不到編號 {self.trigger_id:03d}，可能已被刪除", ephemeral=True)
            return
        ml = {"contains": "包含", "exact": "完全相符", "regex": "正則"}.get(self.match_type, self.match_type)
        rl = {"new": "新訊息", "reply": "回覆該則"}.get(self.reply_type, self.reply_type)
        parts = [f"✏️ **[{self.trigger_id:03d}] 觸發詞「{keyword}」修改成功！**",
                 f"　觸發方式：{ml}　回覆方式：{rl}　機率：{prob}"]
        if mention:        parts.append(f"　@ {mention}")
        if response_text:  parts.append(f"　文字：{response_text[:50]}{'...' if len(response_text) > 50 else ''}")
        if self.image_url: parts.append(f"　圖片：{self.image_url}")
        await interaction.followup.send("\n".join(parts), ephemeral=True)

# ==================== 觸發詞管理指令 ====================
def build_trigger_line(t: dict) -> str:
    mt_labels = {"contains": "包含", "exact": "完全相符", "regex": "正則"}
    rt_labels = {"new": "新訊息", "reply": "回覆該則"}
    preview_text    = f"　文字：{t['response_text'][:20]}{'...' if len(t['response_text']) > 20 else ''}" if t["response_text"] else ""
    preview_img     = "　🖼️ 含圖片" if t["image_url"] else ""
    preview_mention = f"　@ {t['mention_name']}" if t["mention_name"] else ""
    ml = mt_labels.get(t["match_type"], t["match_type"])
    rl = rt_labels.get(t["reply_type"], t["reply_type"])
    meta = f"　[{ml} / {rl} / {t['probability']}%]"
    return f"**[{t['id']:03d}]** `{t['keyword']}`{preview_mention}{preview_text}{preview_img}{meta}"

def build_triggers_display():
    triggers = load_triggers()
    if not triggers:
        return "（尚無任何觸發詞）"
    lines = ["⚡ **觸發詞列表**", "─" * 24]
    lines.extend(build_trigger_line(t) for t in triggers)
    return "\n".join(lines)

class TriggerManageView(discord.ui.View):
    def __init__(self, is_admin: bool):
        super().__init__(timeout=120)
        if not is_admin:
            self.remove_item(self.manage_trigger)

    @discord.ui.button(label="編輯 / 刪除觸發詞", style=discord.ButtonStyle.primary)
    async def manage_trigger(self, interaction: discord.Interaction, button: discord.ui.Button):
        triggers = load_triggers()
        if not triggers:
            await interaction.response.send_message("⚠️ 目前沒有觸發詞可管理。", ephemeral=True)
            return
        options = [
            discord.SelectOption(label=f"[{t['id']:03d}] {t['keyword']}", value=str(t["id"]))
            for t in triggers
        ][:25]
        await interaction.response.send_message(
            "請選擇要編輯或刪除的觸發詞：",
            view=TriggerSelectView(options),
            ephemeral=True
        )

class TriggerSelectView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=60)
        select = discord.ui.Select(placeholder="選擇觸發詞", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        tid = int(interaction.data["values"][0])
        trigger = next((t for t in load_triggers() if t["id"] == tid), None)
        if not trigger:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {tid:03d}，可能已被刪除", view=None)
            return
        await interaction.response.edit_message(
            content=f"已選擇：\n{build_trigger_line(trigger)}\n\n請選擇操作：",
            view=TriggerActionView(trigger)
        )

class TriggerActionView(discord.ui.View):
    def __init__(self, trigger: dict):
        super().__init__(timeout=60)
        self.trigger = trigger

    @discord.ui.button(label="✏️ 編輯", style=discord.ButtonStyle.primary)
    async def edit_trigger(self, interaction: discord.Interaction, button: discord.ui.Button):
        fresh = next((t for t in load_triggers() if t["id"] == self.trigger["id"]), None)
        if not fresh:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {self.trigger['id']:03d}，可能已被刪除", view=None)
            return
        await interaction.response.edit_message(
            content=f"✏️ **編輯觸發詞 [{fresh['id']:03d}]** — 請選擇觸發方式與回覆方式，再按下一步：",
            view=EditTriggerView(fresh)
        )

    @discord.ui.button(label="🗑️ 刪除", style=discord.ButtonStyle.danger)
    async def delete_trigger(self, interaction: discord.Interaction, button: discord.ui.Button):
        tid = self.trigger["id"]
        if delete_trigger_by_id(tid):
            await interaction.response.edit_message(content=f"✅ 已刪除觸發詞 [{tid:03d}]", view=None)
        else:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {tid:03d}，可能已被刪除", view=None)

@bot.tree.command(name="管理觸發詞", description="查看所有觸發詞，管理員可編輯或刪除")
async def slash_triggers(interaction: discord.Interaction):
    is_admin = interaction.user.guild_permissions.administrator
    content  = build_triggers_display()
    view     = TriggerManageView(is_admin)
    await interaction.response.send_message(content=content, view=view, ephemeral=True)

@bot.tree.command(name="新增觸發詞", description="新增關鍵字觸發回應（管理員）")
@app_commands.describe(image="附上圖片自動上傳 imgbb（選填）")
async def slash_add_trigger(interaction: discord.Interaction, image: discord.Attachment = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 只有管理員才能使用此指令", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    image_url = ""
    if image:
        try:
            image_url = await upload_to_imgbb(image)
        except Exception as e:
            await interaction.followup.send(f"❌ 圖片上傳失敗：{e}", ephemeral=True)
            return
    await interaction.followup.send("⚡ **新增觸發詞** — 請選擇觸發方式與回覆方式，再按下一步：",
                                    view=AddTriggerView(image_url), ephemeral=True)

# ==================== 每日提醒 ====================
class ReminderActionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.action = "add"
        select = discord.ui.Select(placeholder="你想要...", options=[
            discord.SelectOption(label="➕ 新增每日提醒", value="add", default=True),
            discord.SelectOption(label="🗑️ 刪除每日提醒", value="delete"),
            discord.SelectOption(label="⚙️ 播報設定（美股/匯率開關與門檻，管理員限定）", value="settings"),
        ])
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        self.action = interaction.data["values"][0]
        await interaction.response.defer()

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary, row=1)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.action == "add":
            await interaction.response.edit_message(
                content="⏰ **新增每日提醒** — 請選擇提醒時間，需要 tag 人可一併選擇：",
                view=AddReminderView()
            )
        elif self.action == "delete":
            reminders = load_reminders()
            if not interaction.user.guild_permissions.administrator:
                reminders = [r for r in reminders if r["created_by"] == str(interaction.user.id)]
            if not reminders:
                await interaction.response.edit_message(content="⚠️ 目前沒有可以刪除的每日提醒。", view=None)
                return
            options = [
                discord.SelectOption(label=f"[{r['id']:03d}] {r['hour']:02d}:{r['minute']:02d} {r['content'][:30]}", value=str(r["id"]))
                for r in reminders
            ][:25]
            await interaction.response.edit_message(content="🗑️ 請選擇要刪除的每日提醒：", view=ReminderDeleteSelectView(options))
        else:  # settings
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.edit_message(content="❌ 只有管理員能修改播報設定", view=None)
                return
            config = load_config()
            await interaction.response.edit_message(
                content="⚙️ **播報設定** — 請選擇美股/匯率播報開關，再按下一步設定匯率門檻：",
                view=BroadcastSettingsView(config)
            )

class BroadcastSettingsView(discord.ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=120)
        self.stock_enabled = config.get("stock_report_enabled", "on") == "on"
        self.jpy_enabled = config.get("jpy_report_enabled", "on") == "on"

        stock_select = discord.ui.Select(placeholder="美股播報", options=[
            discord.SelectOption(label="開啟", value="on", default=self.stock_enabled),
            discord.SelectOption(label="關閉", value="off", default=not self.stock_enabled),
        ])
        stock_select.callback = self.stock_callback
        self.add_item(stock_select)

        jpy_select = discord.ui.Select(placeholder="日幣匯率播報", options=[
            discord.SelectOption(label="開啟", value="on", default=self.jpy_enabled),
            discord.SelectOption(label="關閉", value="off", default=not self.jpy_enabled),
        ])
        jpy_select.callback = self.jpy_callback
        self.add_item(jpy_select)

    async def stock_callback(self, interaction: discord.Interaction):
        self.stock_enabled = interaction.data["values"][0] == "on"
        await interaction.response.defer()

    async def jpy_callback(self, interaction: discord.Interaction):
        self.jpy_enabled = interaction.data["values"][0] == "on"
        await interaction.response.defer()

    @discord.ui.button(label="下一步 →（設定匯率門檻）", style=discord.ButtonStyle.primary, row=2)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BroadcastSettingsModal(self.stock_enabled, self.jpy_enabled))

class BroadcastSettingsModal(discord.ui.Modal, title="播報設定"):
    def __init__(self, stock_enabled: bool, jpy_enabled: bool):
        super().__init__()
        self.stock_enabled = stock_enabled
        self.jpy_enabled = jpy_enabled
        config = load_config()
        self.threshold_input = discord.ui.TextInput(
            label="匯率划算門檻（低於這個值才顯示划算文案）",
            default=str(config.get("jpy_threshold", JPY_THRESHOLD)),
        )
        self.add_item(self.threshold_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            threshold = float(self.threshold_input.value.strip())
        except ValueError:
            await interaction.followup.send("❌ 匯率門檻必須是數字", ephemeral=True)
            return
        save_config("stock_report_enabled", "on" if self.stock_enabled else "off")
        save_config("jpy_report_enabled", "on" if self.jpy_enabled else "off")
        save_config("jpy_threshold", threshold)
        await interaction.followup.send(
            f"✅ **播報設定已更新**\n"
            f"　美股播報：{'開啟' if self.stock_enabled else '關閉'}\n"
            f"　匯率播報：{'開啟' if self.jpy_enabled else '關閉'}\n"
            f"　匯率划算門檻：{threshold}",
            ephemeral=True
        )

class ReminderDeleteSelectView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=60)
        select = discord.ui.Select(placeholder="選擇要刪除的提醒", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        rid = int(interaction.data["values"][0])
        if delete_reminder_by_id(rid):
            await interaction.response.edit_message(content=f"🗑️ 已刪除每日提醒 [{rid:03d}]", view=None)
        else:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {rid:03d}，可能已被刪除", view=None)

class AddReminderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.hour = 9
        self.minute = 0
        self.mention_name = ""

        hour_select = discord.ui.Select(placeholder="小時 (0-23)",
            options=[discord.SelectOption(label=f"{h:02d} 時", value=str(h)) for h in range(24)])
        hour_select.callback = self.hour_callback
        self.add_item(hour_select)

        minute_select = discord.ui.Select(placeholder="分鐘",
            options=[discord.SelectOption(label=f"{m:02d} 分", value=str(m)) for m in range(0, 60, 5)])
        minute_select.callback = self.minute_callback
        self.add_item(minute_select)

        mention_options = [discord.SelectOption(label="不需要 tag", value="none", default=True)] + \
                           [discord.SelectOption(label=m, value=m) for m in get_members()]
        mention_select = discord.ui.Select(placeholder="要 tag 的對象（選填）", options=mention_options[:25])
        mention_select.callback = self.mention_callback
        self.add_item(mention_select)

    async def hour_callback(self, interaction):
        self.hour = int(interaction.data["values"][0]); await interaction.response.defer()

    async def minute_callback(self, interaction):
        self.minute = int(interaction.data["values"][0]); await interaction.response.defer()

    async def mention_callback(self, interaction):
        value = interaction.data["values"][0]
        self.mention_name = "" if value == "none" else value
        await interaction.response.defer()

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary, row=3)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddReminderModal(self.hour, self.minute, self.mention_name))

class AddReminderModal(discord.ui.Modal, title="新增每日提醒"):
    def __init__(self, hour: int, minute: int, mention_name: str):
        super().__init__()
        self.hour = hour
        self.minute = minute
        self.mention_name = mention_name
        self.content_input = discord.ui.TextInput(label="提醒的內容", style=discord.TextStyle.paragraph)
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        content = self.content_input.value.strip()
        if not content:
            await interaction.followup.send("❌ 提醒內容不能空白", ephemeral=True)
            return
        new_id = add_reminder(content, self.mention_name, self.hour, self.minute,
                               str(interaction.user.id), interaction.user.display_name)
        mention_str = f"，並會 tag {self.mention_name}" if self.mention_name else ""
        await interaction.followup.send(
            f"✅ **每日提醒 [{new_id:03d}] 新增成功！**\n"
            f"　時間：每天 {self.hour:02d}:{self.minute:02d}\n"
            f"　內容：{content}{mention_str}",
            ephemeral=True
        )

@bot.tree.command(name="每日提醒", description="新增或刪除你的每日固定提醒")
async def slash_daily_reminder(interaction: discord.Interaction):
    await interaction.response.send_message("⏰ 你想要新增還是刪除每日提醒？", view=ReminderActionView(), ephemeral=True)

# ==================== 代購系統 ====================
STATUS_LABELS = {
    "pending": "⏳ 待處理",
    "confirmed": "✅ 已確認可買",
    "needs_confirmation": "❓ 需再確認",
    "purchased": "🛍️ 已購買",
    "cancelled": "❌ 已取消",
}
STATUS_COLORS = {
    "pending": discord.Color.light_grey(),
    "confirmed": discord.Color.blue(),
    "needs_confirmation": discord.Color.orange(),
    "purchased": discord.Color.green(),
    "cancelled": discord.Color.red(),
}
_ACTIVE_PURCHASE_STATUSES = {"pending", "confirmed", "needs_confirmation"}

def sort_purchases_for_queue(purchases):
    return sorted(purchases, key=lambda p: (p["status"] not in _ACTIVE_PURCHASE_STATUSES, p["id"]))

def _parse_jpy_amount(text, field_label):
    text = text.strip()
    if not text:
        return 0, None
    if not text.isdigit():
        return None, f"「{field_label}」請輸入整數金額（日圓），你輸入的是：{text}"
    return int(text), None

def _format_location(location: str) -> str:
    location = (location or "").strip()
    if not location:
        return "（未填寫）"
    if location.lower().startswith(("http://", "https://")):
        return f"[📍 開啟地圖／連結]({location})"
    return location[:1024]

def build_purchase_embed(p: dict) -> discord.Embed:
    status = p.get("status", "pending")
    embed = discord.Embed(
        title=f"[{p['id']:03d}] {p['item_description'][:60]}",
        color=STATUS_COLORS.get(status, discord.Color.light_grey()),
    )
    # 買到了沒、誰委託的、物品、規格、地點、價格區間放最前面，其餘次要資訊在後
    embed.add_field(name="狀態", value=STATUS_LABELS.get(status, status), inline=True)
    requester_line = p.get("requester_display_name") or "-"
    if p.get("requester_line_id"):
        requester_line += f"（LINE：{p['requester_line_id']}）"
    embed.add_field(name="委託人", value=requester_line[:1024], inline=True)
    embed.add_field(name="物品", value=(p.get("item_description") or "（未填寫）")[:1024], inline=False)
    if p.get("spec_conditions"):
        embed.add_field(name="尺寸/規格/顏色條件", value=p["spec_conditions"][:1024], inline=False)
    embed.add_field(name="購買地點", value=_format_location(p.get("store_location")), inline=False)
    threshold_lines = []
    if p.get("auto_approve_limit_jpy"):
        threshold_lines.append(f"低於 ¥{p['auto_approve_limit_jpy']:,} 可直接買")
    if p.get("confirm_required_above_jpy"):
        threshold_lines.append(f"超過 ¥{p['confirm_required_above_jpy']:,} 需先確認")
    embed.add_field(name="金額門檻", value=("\n".join(threshold_lines) or "（未填寫）")[:1024], inline=False)
    if p.get("item_nature"):
        embed.add_field(name="物品性質", value=p["item_nature"][:1024], inline=False)
    agent_value = f"<@{p['agent_discord_id']}>" if p.get("agent_discord_id") else p.get("agent_display_name", "-")
    embed.add_field(name="代購人", value=agent_value, inline=True)
    if p.get("notes"):
        embed.add_field(name="備註", value=p["notes"][:1024], inline=False)
    images = [u for u in [p.get("image_url_1"), p.get("image_url_2"), p.get("image_url_3")] if u]
    if images:
        embed.set_image(url=images[0])
        if len(images) > 1:
            embed.set_footer(text=f"🖼️ 尚有 {len(images) - 1} 張圖片，可用下方按鈕切換")
    return embed

class PurchaseDisclaimerView(discord.ui.View):
    def __init__(self, context: dict):
        super().__init__(timeout=120)
        self.context = context

    @discord.ui.button(label="✅ 我了解，繼續填寫 →", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PurchaseModalA(self.context))

class PurchaseModalA(discord.ui.Modal, title="代購委託 (1/2) - 物品資訊"):
    def __init__(self, context: dict):
        super().__init__()
        self.context = context
        self.item_input = discord.ui.TextInput(
            label="物品網址／圖片說明／名稱",
            placeholder="例：https://example.com/item 或 手搖飲聯名杯",
            style=discord.TextStyle.paragraph,
        )
        self.location_input = discord.ui.TextInput(
            label="在哪裡買得到？",
            placeholder="店名或 Google Map 連結",
        )
        self.spec_input = discord.ui.TextInput(
            label="尺寸/規格/顏色—符合可直接買的條件（選填）",
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.nature_input = discord.ui.TextInput(
            label="物品性質（大件/重/液體等，選填）",
            required=False,
        )
        self.add_item(self.item_input)
        self.add_item(self.location_input)
        self.add_item(self.spec_input)
        self.add_item(self.nature_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.context["item_description"] = self.item_input.value.strip()
        self.context["store_location"] = self.location_input.value.strip()
        self.context["spec_conditions"] = self.spec_input.value.strip()
        self.context["item_nature"] = self.nature_input.value.strip()
        await interaction.response.send_message(
            "✅ 第一段已記錄，請按下方按鈕繼續填寫第二段（金額與聯絡方式）：",
            view=PurchaseModalBLauncherView(self.context),
            ephemeral=True,
        )

class PurchaseModalBLauncherView(discord.ui.View):
    def __init__(self, context: dict):
        super().__init__(timeout=120)
        self.context = context

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PurchaseModalB(self.context))

class PurchaseModalB(discord.ui.Modal, title="代購委託 (2/2) - 金額與聯絡方式"):
    def __init__(self, context: dict):
        super().__init__()
        self.context = context
        self.no_confirm_input = discord.ui.TextInput(
            label="不需確認、可直接買的金額上限（日圓，選填）",
            placeholder="例：3000",
            required=False,
        )
        self.confirm_input = discord.ui.TextInput(
            label="超過多少日圓需要先問過才能買（選填）",
            placeholder="例：10000",
            required=False,
        )
        self.line_input = discord.ui.TextInput(
            label=context["identity_label"],
            placeholder="代購人知道你的LINE可留白；不知道的話請填入讓對方能聯絡你",
            required=False,
        )
        self.notes_input = discord.ui.TextInput(
            label="備註（選填）",
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.add_item(self.no_confirm_input)
        self.add_item(self.confirm_input)
        self.add_item(self.line_input)
        self.add_item(self.notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ctx = self.context
        no_confirm_amount, err = _parse_jpy_amount(self.no_confirm_input.value, "不需確認金額上限")
        if err:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return
        confirm_amount, err = _parse_jpy_amount(self.confirm_input.value, "需確認門檻")
        if err:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return
        line_id = self.line_input.value.strip()
        notes = self.notes_input.value.strip()

        image_urls = await collect_purchase_images_via_dm(interaction)

        new_id = add_purchase(
            agent_discord_id=ctx["agent_discord_id"],
            agent_display_name=ctx["agent_display_name"],
            creation_source=ctx["creation_source"],
            requester_discord_id=ctx["requester_discord_id"],
            requester_display_name=ctx["requester_display_name"],
            requester_line_id=line_id,
            item_description=ctx["item_description"],
            image_url_1=image_urls[0] if len(image_urls) > 0 else "",
            image_url_2=image_urls[1] if len(image_urls) > 1 else "",
            image_url_3=image_urls[2] if len(image_urls) > 2 else "",
            store_location=ctx["store_location"],
            auto_approve_limit_jpy=no_confirm_amount,
            confirm_required_above_jpy=confirm_amount,
            spec_conditions=ctx["spec_conditions"],
            item_nature=ctx["item_nature"],
            notes=notes,
            created_by_discord_id=ctx["created_by_discord_id"],
        )
        preview = {
            "id": new_id, "status": "pending",
            "agent_discord_id": ctx["agent_discord_id"], "agent_display_name": ctx["agent_display_name"],
            "requester_display_name": ctx["requester_display_name"], "requester_line_id": line_id,
            "item_description": ctx["item_description"],
            "image_url_1": image_urls[0] if len(image_urls) > 0 else "",
            "image_url_2": image_urls[1] if len(image_urls) > 1 else "",
            "image_url_3": image_urls[2] if len(image_urls) > 2 else "",
            "store_location": ctx["store_location"],
            "auto_approve_limit_jpy": no_confirm_amount, "confirm_required_above_jpy": confirm_amount,
            "spec_conditions": ctx["spec_conditions"], "item_nature": ctx["item_nature"], "notes": notes,
        }
        await interaction.followup.send(
            f"✅ **委託 [{new_id:03d}] 已送出給 {ctx['agent_display_name']}！**",
            embed=build_purchase_embed(preview),
            ephemeral=True,
        )

class SkipImagesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=90)

    @discord.ui.button(label="🚀 不用附圖，直接送出", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="好的，直接送出目前的委託內容。", view=None)
        self.stop()

async def collect_purchase_images_via_dm(interaction: discord.Interaction, max_images: int = 3, timeout: int = 90) -> list:
    skip_view = SkipImagesView()
    await interaction.followup.send(
        f"📷 要附上圖片嗎？請直接私訊（DM）我並附上圖片，最多 {max_images} 張（電腦可直接 Ctrl+V 貼上截圖，手機可選相簿），"
        f"{timeout} 秒內傳送即可，可以分多則訊息傳送。不需要圖片的話直接按下方按鈕送出委託。",
        view=skip_view,
        ephemeral=True,
    )
    collected = []

    def check_dm(m: discord.Message) -> bool:
        return m.author.id == interaction.user.id and isinstance(m.channel, discord.DMChannel) and bool(m.attachments)

    while len(collected) < max_images:
        msg_task = asyncio.create_task(bot.wait_for("message", check=check_dm, timeout=timeout))
        skip_task = asyncio.create_task(skip_view.wait())
        done, pending = await asyncio.wait({msg_task, skip_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if skip_task in done:
            break
        try:
            message = msg_task.result()
        except asyncio.TimeoutError:
            break
        for att in message.attachments:
            if len(collected) >= max_images:
                break
            try:
                collected.append(await upload_to_imgbb(att))
            except Exception:
                pass
        try:
            await message.add_reaction("✅")
        except discord.HTTPException:
            pass
    return collected

class PurchaseActionView(discord.ui.View):
    def __init__(self, purchase: dict):
        super().__init__(timeout=180)
        self.purchase = purchase
        self.image_urls = [u for u in [purchase.get("image_url_1"), purchase.get("image_url_2"), purchase.get("image_url_3")] if u]
        self.image_index = 0
        if len(self.image_urls) <= 1:
            self.remove_item(self.cycle_image)

    def _can_act(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == int(self.purchase["agent_discord_id"] or 0)

    async def _set_status(self, interaction: discord.Interaction, status: str, label: str):
        if not self._can_act(interaction):
            await interaction.response.send_message("❌ 只有指定的代購人能操作，這份委託只有你自己看得到", ephemeral=True)
            return
        if not update_purchase_status_by_id(self.purchase["id"], status):
            await interaction.response.edit_message(content=f"❌ 找不到編號 {self.purchase['id']:03d}，可能已被刪除", embed=None, view=None)
            return
        self.purchase["status"] = status
        await interaction.response.edit_message(content=label, embed=build_purchase_embed(self.purchase), view=self)

    @discord.ui.button(label="✅ 確認可買", style=discord.ButtonStyle.primary, row=0)
    async def mark_confirmed(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_status(interaction, "confirmed", f"✅ [{self.purchase['id']:03d}] 已標記為確認可買")

    @discord.ui.button(label="❓ 需再確認", style=discord.ButtonStyle.secondary, row=0)
    async def mark_needs_confirmation(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_status(interaction, "needs_confirmation", f"❓ [{self.purchase['id']:03d}] 已標記為需再確認")

    @discord.ui.button(label="🛍️ 已購買", style=discord.ButtonStyle.success, row=0)
    async def mark_purchased(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_status(interaction, "purchased", f"🛍️ [{self.purchase['id']:03d}] 已標記為已購買")

    @discord.ui.button(label="❌ 取消", style=discord.ButtonStyle.danger, row=0)
    async def mark_cancelled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_status(interaction, "cancelled", f"❌ [{self.purchase['id']:03d}] 已標記為取消")

    @discord.ui.button(label="🗑️ 刪除紀錄", style=discord.ButtonStyle.danger, row=1)
    async def delete_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_act(interaction):
            await interaction.response.send_message("❌ 只有指定的代購人能操作，這份委託只有你自己看得到", ephemeral=True)
            return
        if delete_purchase_by_id(self.purchase["id"]):
            await interaction.response.edit_message(content=f"🗑️ 已刪除委託 [{self.purchase['id']:03d}]", embed=None, view=None)
        else:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {self.purchase['id']:03d}，可能已被刪除", embed=None, view=None)

    @discord.ui.button(label="🖼️ 換圖", style=discord.ButtonStyle.secondary, row=1)
    async def cycle_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.image_index = (self.image_index + 1) % len(self.image_urls)
        embed = build_purchase_embed(self.purchase)
        embed.set_image(url=self.image_urls[self.image_index])
        embed.set_footer(text=f"🖼️ 第 {self.image_index + 1}/{len(self.image_urls)} 張圖片")
        await interaction.response.edit_message(embed=embed, view=self)

PURCHASE_PAGE_SIZE = 25

class PurchaseQueueView(discord.ui.View):
    def __init__(self, purchases: list, page: int = 0):
        super().__init__(timeout=180)
        self.purchases = purchases
        self.page = page
        self.max_page = max(0, (len(purchases) - 1) // PURCHASE_PAGE_SIZE)

        page_items = purchases[page * PURCHASE_PAGE_SIZE:(page + 1) * PURCHASE_PAGE_SIZE]
        select = discord.ui.Select(
            placeholder=f"選擇委託查看詳情（第 {page + 1}/{self.max_page + 1} 頁）",
            options=[
                discord.SelectOption(
                    label=f"[{p['id']:03d}] {p['item_description'][:80]}",
                    description=f"{STATUS_LABELS.get(p['status'], p['status'])}｜{p['requester_display_name'][:40]}",
                    value=str(p["id"]),
                )
                for p in page_items
            ],
        )
        select.callback = self.select_callback
        self.add_item(select)

        prev_button = discord.ui.Button(label="⬅ 上一頁", style=discord.ButtonStyle.secondary, disabled=(page <= 0))
        prev_button.callback = self.prev_page
        self.add_item(prev_button)

        next_button = discord.ui.Button(label="➡ 下一頁", style=discord.ButtonStyle.secondary, disabled=(page >= self.max_page))
        next_button.callback = self.next_page
        self.add_item(next_button)

    async def select_callback(self, interaction: discord.Interaction):
        pid = int(interaction.data["values"][0])
        purchase = next((p for p in self.purchases if p["id"] == pid), None)
        if not purchase:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {pid:03d}，可能已被刪除", embed=None, view=None)
            return
        await interaction.response.edit_message(
            content=None,
            embed=build_purchase_embed(purchase),
            view=PurchaseActionView(purchase),
        )

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=PurchaseQueueView(self.purchases, self.page - 1))

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=PurchaseQueueView(self.purchases, self.page + 1))

@bot.tree.command(name="代購委託", description="委託代購：可以委託給自己，也能幫別人登記")
@app_commands.describe(
    代購人="要委託的代購人",
    委託人稱呼="留空＝幫自己委託；填寫＝幫別人登記（不論在不在群組內）",
)
async def slash_purchase_request(
    interaction: discord.Interaction,
    代購人: discord.Member,
    委託人稱呼: str = None,
):
    is_proxy = bool(委託人稱呼 and 委託人稱呼.strip())
    requester_name = 委託人稱呼.strip() if is_proxy else interaction.user.display_name
    identity_label = f"{requester_name[:15]} 的 LINE ID" if is_proxy else "你的 LINE ID"
    context = {
        "agent_discord_id": str(代購人.id),
        "agent_display_name": 代購人.display_name,
        "creation_source": "proxy" if is_proxy else "self",
        "requester_discord_id": "" if is_proxy else str(interaction.user.id),
        "requester_display_name": requester_name,
        "identity_label": identity_label,
        "created_by_discord_id": str(interaction.user.id),
    }
    trip = get_trip_for_agent(代購人.id)
    trip_line = f"📍 {代購人.display_name} 目前行程：\n{trip['itinerary_text']}\n" if trip else f"⚠️ {代購人.display_name} 目前沒有登記行程，請自行確認對方是否方便代購。\n"
    await interaction.response.send_message(
        f"📝 **代購委託 — 委託給 {代購人.mention}**\n"
        f"　委託人：{requester_name}\n"
        f"{trip_line}"
        "⚠️ 代購人會盡力幫忙，但不一定能買到，請見諒！\n"
        "請按下方按鈕繼續填寫委託內容：",
        view=PurchaseDisclaimerView(context),
        ephemeral=True,
    )

class EditPurchaseModalA(discord.ui.Modal, title="修改委託 (1/2) - 物品資訊"):
    def __init__(self, purchase: dict):
        super().__init__()
        self.purchase_id = purchase["id"]
        self.existing = purchase
        self.item_input = discord.ui.TextInput(
            label="物品網址／圖片說明／名稱",
            default=purchase["item_description"],
            style=discord.TextStyle.paragraph,
        )
        self.location_input = discord.ui.TextInput(
            label="在哪裡買得到？",
            default=purchase["store_location"],
        )
        self.spec_input = discord.ui.TextInput(
            label="尺寸/規格/顏色—符合可直接買的條件（選填）",
            default=purchase["spec_conditions"],
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.nature_input = discord.ui.TextInput(
            label="物品性質（大件/重/液體等，選填）",
            default=purchase["item_nature"],
            required=False,
        )
        self.add_item(self.item_input)
        self.add_item(self.location_input)
        self.add_item(self.spec_input)
        self.add_item(self.nature_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.existing["item_description"] = self.item_input.value.strip()
        self.existing["store_location"] = self.location_input.value.strip()
        self.existing["spec_conditions"] = self.spec_input.value.strip()
        self.existing["item_nature"] = self.nature_input.value.strip()
        await interaction.response.send_message(
            "✅ 第一段已記錄，請按下方按鈕繼續填寫第二段（金額與聯絡方式）：",
            view=EditPurchaseModalBLauncherView(self.existing),
            ephemeral=True,
        )

class EditPurchaseModalBLauncherView(discord.ui.View):
    def __init__(self, purchase: dict):
        super().__init__(timeout=120)
        self.purchase = purchase

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditPurchaseModalB(self.purchase))

class EditPurchaseModalB(discord.ui.Modal, title="修改委託 (2/2) - 金額與聯絡方式"):
    def __init__(self, purchase: dict):
        super().__init__()
        self.purchase_id = purchase["id"]
        self.existing = purchase
        self.no_confirm_input = discord.ui.TextInput(
            label="不需確認、可直接買的金額上限（日圓，選填）",
            default=str(purchase.get("auto_approve_limit_jpy") or ""),
            required=False,
        )
        self.confirm_input = discord.ui.TextInput(
            label="超過多少日圓需要先問過才能買（選填）",
            default=str(purchase.get("confirm_required_above_jpy") or ""),
            required=False,
        )
        self.line_input = discord.ui.TextInput(
            label="LINE ID（選填）",
            default=purchase.get("requester_line_id", ""),
            placeholder="代購人知道你的LINE可留白；不知道的話請填入讓對方能聯絡你",
            required=False,
        )
        self.notes_input = discord.ui.TextInput(
            label="備註（選填）",
            default=purchase.get("notes", ""),
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.add_item(self.no_confirm_input)
        self.add_item(self.confirm_input)
        self.add_item(self.line_input)
        self.add_item(self.notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        no_confirm_amount, err = _parse_jpy_amount(self.no_confirm_input.value, "不需確認金額上限")
        if err:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return
        confirm_amount, err = _parse_jpy_amount(self.confirm_input.value, "需確認門檻")
        if err:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return
        line_id = self.line_input.value.strip()
        notes = self.notes_input.value.strip()
        ok = update_purchase_details_by_id(
            self.purchase_id,
            requester_line_id=line_id,
            item_description=self.existing["item_description"],
            store_location=self.existing["store_location"],
            auto_approve_limit_jpy=no_confirm_amount,
            confirm_required_above_jpy=confirm_amount,
            spec_conditions=self.existing["spec_conditions"],
            item_nature=self.existing["item_nature"],
            notes=notes,
        )
        if not ok:
            await interaction.followup.send(f"❌ 找不到編號 {self.purchase_id:03d}，可能已被刪除", ephemeral=True)
            return
        preview = dict(self.existing)
        preview["requester_line_id"] = line_id
        preview["notes"] = notes
        preview["auto_approve_limit_jpy"] = no_confirm_amount
        preview["confirm_required_above_jpy"] = confirm_amount
        await interaction.followup.send(
            f"✏️ **委託 [{self.purchase_id:03d}] 已修改**",
            embed=build_purchase_embed(preview),
            ephemeral=True,
        )

class RequesterActionView(discord.ui.View):
    def __init__(self, purchase: dict):
        super().__init__(timeout=180)
        self.purchase = purchase

    def _can_act(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == int(self.purchase["requester_discord_id"] or 0)

    @discord.ui.button(label="✏️ 修改", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_act(interaction):
            await interaction.response.send_message("❌ 只有委託人本人能修改，這份委託只有你自己看得到", ephemeral=True)
            return
        fresh = next((p for p in load_purchases() if p["id"] == self.purchase["id"]), None)
        if not fresh:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {self.purchase['id']:03d}，可能已被刪除", embed=None, view=None)
            return
        await interaction.response.send_modal(EditPurchaseModalA(fresh))

    @discord.ui.button(label="🗑️ 刪除委託", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_act(interaction):
            await interaction.response.send_message("❌ 只有委託人本人能刪除，這份委託只有你自己看得到", ephemeral=True)
            return
        if delete_purchase_by_id(self.purchase["id"]):
            await interaction.response.edit_message(content=f"🗑️ 已刪除委託 [{self.purchase['id']:03d}]", embed=None, view=None)
        else:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {self.purchase['id']:03d}，可能已被刪除", embed=None, view=None)

REQUESTER_PAGE_SIZE = 25

class RequesterQueueView(discord.ui.View):
    def __init__(self, purchases: list, page: int = 0):
        super().__init__(timeout=180)
        self.purchases = purchases
        self.page = page
        self.max_page = max(0, (len(purchases) - 1) // REQUESTER_PAGE_SIZE)

        page_items = purchases[page * REQUESTER_PAGE_SIZE:(page + 1) * REQUESTER_PAGE_SIZE]
        select = discord.ui.Select(
            placeholder=f"選擇委託查看詳情（第 {page + 1}/{self.max_page + 1} 頁）",
            options=[
                discord.SelectOption(
                    label=f"[{p['id']:03d}] {p['item_description'][:80]}",
                    description=f"{STATUS_LABELS.get(p['status'], p['status'])}｜代購人：{p['agent_display_name'][:40]}",
                    value=str(p["id"]),
                )
                for p in page_items
            ],
        )
        select.callback = self.select_callback
        self.add_item(select)

        prev_button = discord.ui.Button(label="⬅ 上一頁", style=discord.ButtonStyle.secondary, disabled=(page <= 0))
        prev_button.callback = self.prev_page
        self.add_item(prev_button)

        next_button = discord.ui.Button(label="➡ 下一頁", style=discord.ButtonStyle.secondary, disabled=(page >= self.max_page))
        next_button.callback = self.next_page
        self.add_item(next_button)

    async def select_callback(self, interaction: discord.Interaction):
        pid = int(interaction.data["values"][0])
        purchase = next((p for p in self.purchases if p["id"] == pid), None)
        if not purchase:
            await interaction.response.edit_message(content=f"❌ 找不到編號 {pid:03d}，可能已被刪除", embed=None, view=None)
            return
        await interaction.response.edit_message(
            content=None,
            embed=build_purchase_embed(purchase),
            view=RequesterActionView(purchase),
        )

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=RequesterQueueView(self.purchases, self.page - 1))

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=RequesterQueueView(self.purchases, self.page + 1))

class PurchaseProgressModeView(discord.ui.View):
    def __init__(self, as_agent: list, as_requester: list):
        super().__init__(timeout=120)
        self.as_agent = as_agent
        self.as_requester = as_requester

    @discord.ui.button(label="🛍️ 別人請我買的", style=discord.ButtonStyle.primary)
    async def show_agent(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"🛍️ **你的代購清單**（別人請你買的，共 {len(self.as_agent)} 筆，只有你自己看得到）",
            view=PurchaseQueueView(self.as_agent),
        )

    @discord.ui.button(label="📋 我請別人買的", style=discord.ButtonStyle.secondary)
    async def show_requester(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"📋 **你委託出去的代購**（你請別人買的，共 {len(self.as_requester)} 筆，只有你自己看得到）",
            view=RequesterQueueView(self.as_requester),
        )

@bot.tree.command(name="代購進度", description="查看代購相關委託：別人請你買的、或你請別人買的都在這裡")
async def slash_purchase_progress(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    all_purchases = load_purchases()
    as_agent = sort_purchases_for_queue([p for p in all_purchases if p["agent_discord_id"] == str(interaction.user.id)])
    as_requester = sort_purchases_for_queue([p for p in all_purchases if p["requester_discord_id"] == str(interaction.user.id)])

    if not as_agent and not as_requester:
        await interaction.followup.send("📭 你目前沒有任何代購相關紀錄。", ephemeral=True)
        return
    if as_agent and not as_requester:
        await interaction.followup.send(
            f"🛍️ **你的代購清單**（別人請你買的，共 {len(as_agent)} 筆，只有你自己看得到）",
            view=PurchaseQueueView(as_agent),
            ephemeral=True,
        )
        return
    if as_requester and not as_agent:
        await interaction.followup.send(
            f"📋 **你委託出去的代購**（你請別人買的，共 {len(as_requester)} 筆，只有你自己看得到）",
            view=RequesterQueueView(as_requester),
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        "📦 你同時有兩種代購紀錄，想先看哪一種？（幫別人代填登記的委託不會出現在「我請別人買的」，因為系統沒有那個人的 Discord 身份可以核對）",
        view=PurchaseProgressModeView(as_agent, as_requester),
        ephemeral=True,
    )

class TripSetModal(discord.ui.Modal, title="設定我的代購行程"):
    def __init__(self, existing_text: str = ""):
        super().__init__()
        self.itinerary_input = discord.ui.TextInput(
            label="行程說明（讓委託人知道你要去哪）",
            placeholder="例：7/20-7/25 大阪、京都",
            style=discord.TextStyle.paragraph,
            default=existing_text,
        )
        self.add_item(self.itinerary_input)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.itinerary_input.value.strip()
        set_trip(str(interaction.user.id), interaction.user.display_name, text)
        await interaction.response.send_message(
            f"📍 行程已更新：{text}\n之後別人委託你代購時就會看到這段說明。",
            ephemeral=True,
        )

class TripEndConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="⚠️ 確認結束並清空，無法復原", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        agent_id = str(interaction.user.id)
        trip = get_trip_for_agent(agent_id)
        purchases = [p for p in load_purchases() if p["agent_discord_id"] == agent_id]
        archive_purchases_to_history(purchases, trip)
        deleted = delete_all_purchases_for_agent(agent_id)
        clear_trip(agent_id)
        await interaction.response.edit_message(
            content=f"🏁 行程已結束，共 {deleted} 筆委託已歸檔至歷史紀錄並從你的清單移除（不會再顯示在 `/代購進度`）。",
            view=None,
        )

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="已取消，行程與委託維持不變。", view=None)

class TripActionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.action = "start"
        select = discord.ui.Select(placeholder="你想要...", options=[
            discord.SelectOption(label="📍 開始／更新行程", value="start", default=True),
            discord.SelectOption(label="🏁 結束行程並清空這趟所有委託", value="end"),
        ])
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        self.action = interaction.data["values"][0]
        await interaction.response.defer()

    @discord.ui.button(label="下一步 →", style=discord.ButtonStyle.primary, row=1)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.action == "start":
            existing = get_trip_for_agent(interaction.user.id)
            await interaction.response.send_modal(TripSetModal(existing["itinerary_text"] if existing else ""))
        else:
            trip = get_trip_for_agent(interaction.user.id)
            if not trip:
                await interaction.response.edit_message(content="⚠️ 你目前沒有登記中的行程。", view=None)
                return
            count = len([p for p in load_purchases() if p["agent_discord_id"] == str(interaction.user.id)])
            await interaction.response.edit_message(
                content=(
                    f"⚠️ **確定要結束行程嗎？**\n"
                    f"目前行程：\n{trip['itinerary_text'][:200]}\n"
                    f"這趟共 {count} 筆委託（不分狀態）將會從 `/代購進度` 移除、歸檔到歷史紀錄，之後不會再顯示在清單裡。"
                ),
                view=TripEndConfirmView(),
            )

@bot.tree.command(name="代購行程", description="設定你目前的代購行程，或結束行程並清空這趟所有委託")
async def slash_trip(interaction: discord.Interaction):
    await interaction.response.send_message("📍 你想要開始／更新行程，還是結束行程？", view=TripActionView(), ephemeral=True)

@bot.tree.command(name="代購強制刪除", description="管理員緊急清空某位代購人名下所有委託（不會顯示物品內容，僅供代購人失聯等情況使用）")
@app_commands.describe(代購人id="代購人的 Discord 使用者 ID（純數字，開發者模式右鍵複製，即使對方已離開群組也能用）")
async def slash_purchase_force_delete(interaction: discord.Interaction, 代購人id: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 只有管理員才能使用此指令", ephemeral=True)
        return
    agent_id = 代購人id.strip()
    if not agent_id.isdigit():
        await interaction.response.send_message("❌ 代購人ID 必須是純數字的 Discord 使用者 ID", ephemeral=True)
        return
    count = len([p for p in load_purchases() if p["agent_discord_id"] == agent_id])
    if count == 0:
        await interaction.response.send_message(f"📭 ID `{agent_id}` 名下目前沒有任何委託。", ephemeral=True)
        return
    try:
        user = await bot.fetch_user(int(agent_id))
        name_hint = f"（{user.display_name}）"
    except (discord.NotFound, discord.HTTPException, ValueError):
        name_hint = "（無法取得使用者名稱）"
    await interaction.response.send_message(
        f"⚠️ 確定要強制清空 ID `{agent_id}` {name_hint} 名下**所有 {count} 筆**委託嗎？\n"
        "這裡不會顯示委託內容，此動作僅供代購人失聯等緊急情況使用，會直接刪除、不會歸檔到歷史紀錄，無法復原。",
        view=PurchaseForceDeleteConfirmView(agent_id),
        ephemeral=True,
    )

class PurchaseForceDeleteConfirmView(discord.ui.View):
    def __init__(self, agent_id: str):
        super().__init__(timeout=60)
        self.agent_id = agent_id

    @discord.ui.button(label="⚠️ 確認強制清空", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        deleted = delete_all_purchases_for_agent(self.agent_id)
        clear_trip(self.agent_id)
        await interaction.response.edit_message(content=f"🗑️ 已強制清空 ID `{self.agent_id}` 名下共 {deleted} 筆委託。", view=None)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="已取消。", view=None)

# ==================== 每日播報（匯率 + 美股）====================
NOTIFY_CHANNEL_ID = 472053843367297026
JPY_THRESHOLD = 0.198

US_INDICES = [
    ("^DJI", "道瓊工業"),
    ("^IXIC", "那斯達克"),
    ("^GSPC", "標普500"),
    ("^SOX", "費城半導體"),
]

async def fetch_us_market_summary():
    import aiohttp
    lines = []
    any_up = any_down = False
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        for symbol, label in US_INDICES:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
                async with session.get(url) as resp:
                    data = await resp.json()
                closes = [c for c in data["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
                latest, prev = closes[-1], closes[-2]
                change = latest - prev
                pct = change / prev * 100
                arrow = "🔺" if change >= 0 else "🔻"
                lines.append(f"　{label}：{latest:,.2f}（{arrow}{change:+,.2f}，{pct:+.2f}%）")
                if pct > 3: any_up = True
                if pct < -3: any_down = True
            except Exception:
                lines.append(f"　{label}：查詢失敗")
    return lines, any_up, any_down

@tasks.loop(time=dtime(1, 0, 0))  # UTC 01:00 = 台灣時間 09:00
async def daily_rate_check():
    config = load_config()
    channel = bot.get_channel(config.get("notify_channel_id", NOTIFY_CHANNEL_ID))
    if not channel:
        return

    jpy_enabled = config.get("jpy_report_enabled", "on") == "on"
    stock_enabled = config.get("stock_report_enabled", "on") == "on"
    try:
        threshold = float(config.get("jpy_threshold", JPY_THRESHOLD))
    except (TypeError, ValueError):
        threshold = JPY_THRESHOLD

    lines = []
    if jpy_enabled:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("https://open.er-api.com/v6/latest/JPY") as resp:
                    data = await resp.json()
                    rate = data["rates"]["TWD"]
            lines.append(f"💴 **現在日幣匯率：1 JPY = {rate:.4f} TWD**")
            if rate < threshold:
                lines.append("現在日幣正划算，老師們快換日幣吧，替老師精打細算也是我的工作呢 📊")
        except Exception as e:
            print(f"⚠️ 匯率查詢失敗：{e}")
            lines.append("💴 匯率查詢失敗")

    if stock_enabled:
        try:
            stock_lines, any_up, any_down = await fetch_us_market_summary()
            if lines:
                lines.append("")
            lines.append("📈 **昨夜美股收盤**")
            lines.extend(stock_lines)
            if any_up:
                lines.append("")
                lines.append("今天又是睡醒數錢的日子呢哼哼😆，優香會繼續賺我跟老師的結婚基金的💖")
            if any_down:
                lines.append("")
                lines.append("嗚嗚嗚，又是睡公園的日子，今天要跟SRT小隊搶公園廁所了")
        except Exception as e:
            print(f"⚠️ 美股資料查詢失敗：{e}")
            lines.append("📈 美股資料查詢失敗")

    if lines:
        await channel.send("\n".join(lines))

@tasks.loop(minutes=1)
async def reminder_check():
    config = load_config()
    channel = bot.get_channel(config.get("notify_channel_id", NOTIFY_CHANNEL_ID))
    if not channel:
        return
    now = get_taiwan_now()
    reminders = [r for r in load_reminders() if r["hour"] == now.hour and r["minute"] == now.minute]
    if not reminders:
        return
    data = load_members_data()
    name_to_id = {v: k for k, v in data["discord_map"].items()}
    for r in reminders:
        mention = ""
        if r["mention_name"]:
            discord_id = name_to_id.get(r["mention_name"])
            mention = f"<@{discord_id}> " if discord_id else f"{r['mention_name']} "
        await channel.send(f"⏰ {mention}{r['content']}")

# ==================== 關鍵字偵測 ====================
ROLE_ID = 937360224493396018
KEYWORDS = ["我請大家", "我請客", "請大家吃"]

@bot.event
async def on_message(message: discord.Message):
    # 忽略機器人自己的訊息
    if message.author.bot:
        return

    # 檢查是否包含關鍵字（私訊沒有 guild，略過）
    if message.guild and any(kw in message.content for kw in KEYWORDS):
        role = message.guild.get_role(ROLE_ID)
        if role:
            await message.channel.send(f"🍽️ 有人要請客！{role.mention} 快來！")
        try:
            await message.pin()
        except Exception as e:
            print(f"⚠️ 釘選失敗：{e}")

    # 動態觸發詞偵測
    try:
        triggers = load_triggers()
        data = load_members_data()
        name_to_id = {v: k for k, v in data["discord_map"].items()}
        content_lower = message.content.lower()

        matched = {}
        for trigger in triggers:
            mt = trigger["match_type"]
            kw = trigger["keyword"]
            if mt == "exact":
                hit = content_lower.strip() == kw.lower().strip()
            elif mt == "regex":
                try: hit = bool(re.search(kw, message.content, re.IGNORECASE))
                except re.error: hit = False
            else:
                hit = kw.lower() in content_lower
            if hit:
                matched.setdefault(f"{kw}|{mt}", []).append(trigger)

        for candidates in matched.values():
            weights = [t["probability"] for t in candidates]
            trigger = random.choices(candidates, weights=weights, k=1)[0]
            parts = []
            if trigger["mention_name"]:
                discord_id = name_to_id.get(trigger["mention_name"])
                parts.append(f"<@{discord_id}>" if discord_id else trigger["mention_name"])
            if trigger["response_text"]:
                options = [o.strip() for o in trigger["response_text"].split("<>") if o.strip()]
                if options:
                    parts.append(random.choice(options))
            if trigger["image_url"]:
                parts.append(trigger["image_url"])
            if parts:
                content = "\n".join(parts)
                if trigger["reply_type"] == "reply":
                    await message.reply(content)
                else:
                    await message.channel.send(content)
    except Exception as e:
        print(f"⚠️ 觸發詞偵測失敗：{e}")

    # 確保其他指令正常運作
    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"成功登入！機器人：{bot.user}")
    daily_rate_check.start()
    reminder_check.start()

keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))