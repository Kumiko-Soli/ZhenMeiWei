import os
import math
import json
import base64
from datetime import datetime
from flask import Flask
from threading import Thread
import discord
from discord import app_commands
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

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

# ==================== 設定檔讀寫（Google Sheets）====================
def load_config():
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
        return config
    except Exception:
        return {"cashier_channel_id": 0, "recovery_channel_id": 0, "sheet_id": ""}

def save_config(key, value):
    ws = get_config_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == key:
            ws.update(f"B{i+1}", [[str(value)]])
            return
    ws.append_row([key, str(value)])

# ==================== 成員資料讀寫（Google Sheets）====================
def load_members_data():
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
        return {"members": members, "discord_map": discord_map}
    except Exception:
        return {"members": [], "discord_map": {}}

def add_member_to_sheet(name):
    ws = get_members_ws()
    ws.append_row([name, ""])

def remove_member_from_sheet(name):
    ws = get_members_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == name:
            ws.delete_rows(i + 1)
            return

def set_discord_mapping(name, discord_id):
    ws = get_members_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == name:
            ws.update(f"B{i+1}", [[discord_id]])
            return

def rename_member_in_sheets(old_name, new_name):
    # 更新 members 工作表
    ws = get_members_ws()
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row and row[0] == old_name:
            ws.update(f"A{i+1}", [[new_name]])

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
    owe_others = {}
    others_owe = {}

    for r in records:
        amount_ntd = r["amount"] * r["rate"]
        if r["debtor"] == name:
            owe_others[r["creditor"]] = owe_others.get(r["creditor"], 0) + amount_ntd
        elif r["creditor"] == name:
            others_owe[r["debtor"]] = others_owe.get(r["debtor"], 0) + amount_ntd

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
        self.add_item(self.cashier_input)
        self.add_item(self.recovery_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cashier_id  = int(self.cashier_input.value.strip())
            recovery_id = int(self.recovery_input.value.strip())
            save_config("cashier_channel_id", cashier_id)
            save_config("recovery_channel_id", recovery_id)
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
@bot.tree.command(name="記帳", description="快速新增記帳 (直接按 Tab 填寫，電腦端推薦)")
@app_commands.describe(debtor="欠債人", creditor="債權人", amount="金額", rate="匯率", item="項目")
async def slash_record(interaction: discord.Interaction, debtor: str, creditor: str, amount: float, rate: float, item: str):
    members = get_members()
    if debtor not in members or creditor not in members:
        await interaction.response.send_message(f"❌ 名字錯誤！合法名單：{', '.join(members)}", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    new_id = save_record(debtor, creditor, amount, rate, item)
    await interaction.followup.send(f"✅ [編號 {new_id:03d}] 快速紀錄成功！", ephemeral=True)
    await update_cashier_board()

@bot.tree.command(name="表單", description="開啟下拉選單選人，再填寫金額 (手機端推薦)")
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

@bot.tree.command(name="成員管理", description="查看或管理成員名單與 Discord 對應")
async def slash_members(interaction: discord.Interaction):
    is_admin = interaction.user.guild_permissions.administrator
    content  = build_members_display()
    view     = MembersManageView(is_admin)
    await interaction.response.send_message(content=content, view=view, ephemeral=True)

@bot.tree.command(name="設定頻道", description="設定收銀台與餐盤回收區頻道（管理員）")
async def slash_set_channels(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 只有管理員才能使用此指令", ephemeral=True)
        return
    await interaction.response.send_modal(SetChannelsModal())

@bot.tree.command(name="設定試算表", description="設定 Google Sheet ID（管理員）")
async def slash_set_sheet(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 只有管理員才能使用此指令", ephemeral=True)
        return
    await interaction.response.send_modal(SetSheetModal())

@bot.tree.command(name="修改", description="修改特定編號的帳目內容")
async def slash_modify(interaction: discord.Interaction, 編號: int):
    await interaction.response.defer(ephemeral=True)
    records = load_records()
    old_data = next((r for r in records if r['id'] == 編號), None)
    if not old_data:
        await interaction.followup.send(f"❌ 找不到編號 {編號} 的紀錄。", ephemeral=True)
        return
    await interaction.followup.send("請填寫修改內容：", ephemeral=True)
    await interaction.response.send_modal(ModifyModal(編號, old_data))

@bot.tree.command(name="刪除", description="依編號刪除紀錄")
async def slash_delete(interaction: discord.Interaction, 編號: int):
    await interaction.response.defer()
    if delete_record_by_id(編號):
        await interaction.followup.send(f"🗑️ 已刪除編號 {編號:03d}。")
        await update_cashier_board()
    else:
        await interaction.followup.send(f"❌ 找不到編號 {編號}", ephemeral=True)

@bot.tree.command(name="清空收銀台", description="強制清空收銀台頻道內的所有訊息")
async def slash_purge(interaction: discord.Interaction):
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

@bot.tree.command(name="重新整理", description="重新讀取資料庫並更新收銀台表格")
async def slash_refresh(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await update_cashier_board()
    await interaction.followup.send("✅ 收銀台已更新完畢！", ephemeral=True)

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
        await interaction.channel.send("🎉 本群目前人人清白，無人欠債！")
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

@bot.tree.command(name="結清", description="結清兩人之間的帳務並歸檔")
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

    config = load_config()
    recv_channel = bot.get_channel(config.get("recovery_channel_id", 0))
    if recv_channel: await recv_channel.send("\n".join(recovery))

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

    await interaction.followup.send(f"🤝 {n1} 與 {n2} 帳務已結清並歸檔。")
    await update_cashier_board()

@bot.event
async def on_ready():
    print(f"成功登入！機器人：{bot.user}")

keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))