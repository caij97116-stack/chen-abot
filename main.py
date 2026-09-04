import os
import io
import json
import random
import secrets
import asyncio
import zipfile
import discord
from discord.ext import commands
from discord import app_commands
from aiohttp import web
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("chen-abot")

# Bot 配置
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix=None, intents=intents)

# ─── 数据文件路径 ───
DATA_FILE = "file_records.json"
QUESTIONS_FILE = "questions.json"
STORAGE_CHANNEL_FILE = "storage_channel.json"
FAQ_FILE = "faq.json"
POINTS_FILE = "points.json"
CHECKIN_CHANNEL_FILE = "checkin_channels.json"
REPORT_FILE = "report_data.json"
REPORT_COUNTER_FILE = "report_counter.json"
REPORT_CHANNEL_KEYWORD = "间谍"          # 举报入口频道关键词
REPORT_REVIEW_CHANNEL_NAME = "举报审核"   # 审核工单频道名称
BLACKLIST_CHANNEL_KEYWORD = "黑户"       # 结案公示频道关键词
BLACKLIST_CHANNEL_NAME = "黑户地带"
OPEN_REPORT_STATUSES = {"draft", "pending", "reviewing"}
GUIDE_CHANNEL_KEYWORD = "指路"           # 指路频道关键词
GUIDE_CHANNEL_FILE = "guide_channels.json"
DOWNLOAD_LOGS_FILE = "download_logs.json"
PUBLISHED_FILE = "channel_published.json"

# ─── 文件记录存储 ───
# 结构: { "file_id": { "name": str, "uploader_id": int, "status": "draft|published", "description": str,
#                      "attachments": [{ "original_name": str, "custom_name": str, "storage_msg_id": str, "size": int }],
#                      "conditions": { ... }, "published_msg_id": str, "source_channel_id": int, "guild_id": int,
#                      "upload_time": str, "resource_code": str, "updates": list, "storage_card_msg_id": str } }
file_records: dict = {}

# ─── 频道已发布消息追踪 ───
# 结构: { "channel_id": { "message_id": str, "file_id": str } }
channel_published: dict = {}

def load_channel_published():
    global channel_published
    try:
        if os.path.exists(PUBLISHED_FILE):
            with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
                channel_published = json.load(f)
    except Exception:
        channel_published = {}

def save_channel_published():
    try:
        with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
            json.dump(channel_published, f)
    except Exception as e:
        logger.error(f"保存频道发布记录失败: {e}")

# ─── 存储频道管理 ───
# 结构: { "guild_id": "channel_id" }
storage_channels: dict = {}

def load_storage_channels():
    global storage_channels
    try:
        if os.path.exists(STORAGE_CHANNEL_FILE):
            with open(STORAGE_CHANNEL_FILE, "r", encoding="utf-8") as f:
                storage_channels = json.load(f)
    except Exception:
        storage_channels = {}

def save_storage_channels():
    try:
        with open(STORAGE_CHANNEL_FILE, "w", encoding="utf-8") as f:
            json.dump(storage_channels, f)
    except Exception as e:
        logger.error(f"保存存储频道信息失败: {e}")

async def get_or_create_storage_channel(guild: discord.Guild) -> discord.TextChannel:
    """获取或创建固定存储频道（仅最高权限者和 bot 可见）"""
    guild_id = str(guild.id)
    channel_id = storage_channels.get(guild_id)

    if channel_id:
        channel = guild.get_channel(int(channel_id))
        if channel:
            return channel

    # 创建新频道：仅 guild owner 和 bot 可见
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
        ),
    }
    if guild.owner:
        overwrites[guild.owner] = discord.PermissionOverwrite(read_messages=True)

    try:
        channel = await guild.create_text_channel(
            name="📁-文件存储",
            overwrites=overwrites,
            reason="Chen-Abot 文件存储频道",
        )
        storage_channels[guild_id] = str(channel.id)
        save_storage_channels()
        logger.info(f"已创建存储频道: #{channel.name} in {guild.name}")
        return channel
    except Exception as e:
        logger.error(f"创建存储频道失败: {e}")
        raise


def _new_resource_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    existing = {rec.get("resource_code") for rec in file_records.values()}
    for _ in range(32):
        code = "R-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if code not in existing:
            return code
    return "R-" + secrets.token_hex(4).upper()


def _ensure_resource_code(record: dict) -> str:
    code = record.get("resource_code")
    if code:
        return code
    code = _new_resource_code()
    record["resource_code"] = code
    return code


def _format_beijing_minute(raw) -> str:
    if not raw:
        return "未知"
    try:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BEIJING_TZ)
        else:
            dt = dt.astimezone(BEIJING_TZ)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        text = str(raw)
        return text[:16] if len(text) >= 16 else text


def _is_island_owner(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    return bool(guild and interaction.user.id == guild.owner_id)


def _find_record_by_storage_card(message_id) -> tuple:
    target = str(message_id)
    for fid, rec in file_records.items():
        if str(rec.get("storage_card_msg_id") or "") == target:
            return fid, rec
    return None, None


def _build_storage_card_embed(file_id: str, record: dict) -> discord.Embed:
    code = _ensure_resource_code(record)
    attachments = record.get("attachments") or []
    files_lines = []
    for i, att in enumerate(attachments[:30], 1):
        files_lines.append(f"{i}. {att.get('custom_name', '?')} ({_format_size(att.get('size', 0))})")
    if len(attachments) > 30:
        files_lines.append(f"... 另有 {len(attachments) - 30} 个文件")
    files_text = "\n".join(files_lines) if files_lines else "（无文件）"
    updates = record.get("updates") or []
    update_lines = []
    for item in updates[-8:]:
        names = "、".join(item.get("files") or [])
        update_lines.append(f"{_format_beijing_minute(item.get('time'))} 追加 {names}")
    updates_text = "\n".join(update_lines) if update_lines else "暂无追加"
    status = "已发布" if record.get("status") == "published" else "草稿"
    source = record.get("source_channel_id")
    source_txt = f"<#{source}>" if source else "未知"
    desc = (
        f"**资源回溯码:** `{code}`\n"
        f"**标题:** {record.get('name', '?')}\n"
        f"**作者:** <@{record.get('uploader_id', 0)}> `{record.get('uploader_id', '?')}`\n"
        f"**来源:** {source_txt}\n"
        f"**上传时间:** {_format_beijing_minute(record.get('upload_time'))}\n"
        f"**状态:** {status} | **总大小:** {_format_size(record.get('size', 0))}\n\n"
        f"**文件清单**\n{files_text}\n\n"
        f"**追加更新**\n{updates_text}"
    )
    embed = discord.Embed(
        title="存储记录卡",
        description=desc[:4000],
        color=discord.Color.dark_gold(),
    )
    embed.set_footer(text="仅岛主可见 | 点回溯查看领取记录")
    return embed


async def _upsert_storage_card(guild: discord.Guild, file_id: str, record: dict):
    if not guild:
        return
    channel = await get_or_create_storage_channel(guild)
    embed = _build_storage_card_embed(file_id, record)
    view = PersistentStorageCardView()
    msg_id = record.get("storage_card_msg_id")
    if msg_id:
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(embed=embed, view=view)
            return
        except Exception:
            pass
    msg = await channel.send(embed=embed, view=view)
    record["storage_card_msg_id"] = str(msg.id)
    save_records()


def _resource_download_logs(file_id: str, record: dict) -> list:
    code = record.get("resource_code")
    name = record.get("name")
    hits = []
    for log in download_logs:
        if str(log.get("record_id") or "") == str(file_id):
            hits.append(log)
            continue
        if code and log.get("resource_code") == code:
            hits.append(log)
            continue
        if name and log.get("file_name") == name and str(log.get("uploader_id")) == str(record.get("uploader_id")):
            hits.append(log)
    return hits[-20:]


def _build_resource_trace_embed(file_id: str, record: dict) -> discord.Embed:
    code = _ensure_resource_code(record)
    logs = _resource_download_logs(file_id, record)
    if logs:
        lines = []
        for log in logs:
            lines.append(
                f"{_format_beijing_minute(log.get('timestamp'))} "
                f"<@{log.get('downloader_id', 0)}> `{log.get('downloader_id', '?')}` "
                f"{log.get('file_label') or log.get('file_name') or '?'}"
            )
        log_text = "\n".join(lines)
    else:
        log_text = "暂无领取记录"
    embed = discord.Embed(
        title="资源回溯",
        description=(
            f"**资源回溯码:** `{code}`\n"
            f"**标题:** {record.get('name', '?')}\n"
            f"**作者:** <@{record.get('uploader_id', 0)}>\n"
            f"**上传时间:** {_format_beijing_minute(record.get('upload_time'))}\n\n"
            f"**最近领取**\n{log_text}"
        )[:4000],
        color=discord.Color.orange(),
    )
    return embed


class PersistentStorageCardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="回溯",
        style=discord.ButtonStyle.primary,
        custom_id="storage_card_trace",
    )
    async def trace_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_island_owner(interaction):
            await interaction.response.send_message("仅岛主可回溯。", ephemeral=True)
            return
        file_id, record = _find_record_by_storage_card(interaction.message.id)
        if not record:
            await interaction.response.send_message("找不到对应资源。", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=_build_resource_trace_embed(file_id, record),
            ephemeral=True,
        )


# ─── FAQ 常见问题 ───
# 结构: { "keyword": "answer", ... }
faq_data: dict = {}

def load_faq():
    global faq_data
    try:
        if os.path.exists(FAQ_FILE):
            with open(FAQ_FILE, "r", encoding="utf-8") as f:
                faq_data = json.load(f)
            logger.info(f"已加载 {len(faq_data)} 条 FAQ")
    except Exception:
        faq_data = {}

def save_faq():
    try:
        with open(FAQ_FILE, "w", encoding="utf-8") as f:
            json.dump(faq_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存 FAQ 失败: {e}")

# ─── 签到系统 ───
# 积分: { "guild_id": { "user_id": {"points": int, "last_checkin": "YYYY-MM-DD",
#          "total_days": int, "streak": int, "max_streak": int} } }
points_data: dict = {}
# 签到频道消息: { "channel_id": "message_id" }
checkin_channel_messages: dict = {}
CHECKIN_CHANNEL_KEYWORD = "签到"  # 签到频道名称关键词
BEIJING_TZ = timezone(timedelta(hours=8))


def _beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def load_points():
    global points_data
    try:
        if os.path.exists(POINTS_FILE):
            with open(POINTS_FILE, "r", encoding="utf-8") as f:
                points_data = json.load(f)
    except Exception:
        points_data = {}

def save_points():
    try:
        with open(POINTS_FILE, "w", encoding="utf-8") as f:
            json.dump(points_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存积分失败: {e}")

def load_checkin_channels():
    global checkin_channel_messages
    try:
        if os.path.exists(CHECKIN_CHANNEL_FILE):
            with open(CHECKIN_CHANNEL_FILE, "r", encoding="utf-8") as f:
                checkin_channel_messages = json.load(f)
    except Exception:
        checkin_channel_messages = {}

def save_checkin_channels():
    try:
        with open(CHECKIN_CHANNEL_FILE, "w", encoding="utf-8") as f:
            json.dump(checkin_channel_messages, f)
    except Exception as e:
        logger.error(f"保存签到频道信息失败: {e}")


def _checkin_today() -> str:
    return _beijing_now().strftime("%Y-%m-%d")


def _checkin_yesterday() -> str:
    return (_beijing_now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _empty_checkin_record() -> dict:
    return {
        "points": 0,
        "last_checkin": "",
        "total_days": 0,
        "streak": 0,
        "max_streak": 0,
    }


def _normalize_checkin_record(user_data: dict) -> dict:
    data = _empty_checkin_record()
    data.update(user_data or {})
    data["points"] = int(data.get("points") or 0)
    data["last_checkin"] = str(data.get("last_checkin") or "")
    data["total_days"] = int(data.get("total_days") or 0)
    data["streak"] = int(data.get("streak") or 0)
    data["max_streak"] = int(data.get("max_streak") or 0)
    if data["total_days"] == 0 and data["last_checkin"]:
        data["total_days"] = 1
    if data["streak"] == 0 and data["last_checkin"]:
        data["streak"] = 1
    if data["max_streak"] < data["streak"]:
        data["max_streak"] = data["streak"]
    return data


def _get_checkin_record(guild_id: str, user_id: str) -> dict:
    guild_data = points_data.setdefault(guild_id, {})
    record = _normalize_checkin_record(guild_data.get(user_id, {}))
    guild_data[user_id] = record
    return record


def _live_streak(user_data: dict) -> int:
    last = user_data.get("last_checkin") or ""
    if not last:
        return 0
    today = _checkin_today()
    yesterday = _checkin_yesterday()
    if last in (today, yesterday):
        return int(user_data.get("streak") or 0)
    return 0


# ─── 举报系统 ───
# 结构: { "report_id": { "guild_id": str, "thread_id": str, "parent_channel_id": str, "reporter_id": str,
#   "reporter_name": str, "target_id": str, "target_name": str, "category": str, "location": str,
#   "reason": str, "anonymous": bool, "status": "draft|pending|reviewing|completed",
#   "review_message_id": str, "public_message_id": str, "created_at": str } }
report_data: dict = {}
report_channel_messages: dict = {}  # 举报入口频道消息: { "channel_id": "message_id" }
report_counter: dict = {}  # 工单计数器: { "guild_id": int }

def load_reports():
    global report_data
    try:
        if os.path.exists(REPORT_FILE):
            with open(REPORT_FILE, "r", encoding="utf-8") as f:
                report_data = json.load(f)
    except Exception:
        report_data = {}

def save_reports():
    try:
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存举报数据失败: {e}")

def load_report_counter():
    global report_counter
    try:
        if os.path.exists(REPORT_COUNTER_FILE):
            with open(REPORT_COUNTER_FILE, "r", encoding="utf-8") as f:
                report_counter = json.load(f)
    except Exception:
        report_counter = {}

def save_report_counter():
    try:
        with open(REPORT_COUNTER_FILE, "w", encoding="utf-8") as f:
            json.dump(report_counter, f)
    except Exception as e:
        logger.error(f"保存工单计数器失败: {e}")

def load_report_channels():
    global report_channel_messages
    try:
        if os.path.exists("report_channels.json"):
            with open("report_channels.json", "r", encoding="utf-8") as f:
                report_channel_messages = json.load(f)
    except Exception:
        report_channel_messages = {}

def save_report_channels():
    try:
        with open("report_channels.json", "w", encoding="utf-8") as f:
            json.dump(report_channel_messages, f)
    except Exception as e:
        logger.error(f"保存举报频道信息失败: {e}")


def _find_open_report(guild_id: str, user_id: str):
    for rec in report_data.values():
        if rec.get("guild_id") == str(guild_id) and rec.get("reporter_id") == str(user_id):
            if rec.get("status") in OPEN_REPORT_STATUSES:
                return rec
    return None


def _find_report_by_thread(thread_id: str):
    tid = str(thread_id)
    for rid, rec in report_data.items():
        if rec.get("thread_id") == tid:
            return rid, rec
    return None, None


def _find_report_by_review_message(message_id: str):
    mid = str(message_id)
    for rid, rec in report_data.items():
        if rec.get("review_message_id") == mid:
            return rid, rec
    return None, None


def _report_status_label(status: str) -> str:
    return {
        "draft": "草稿",
        "pending": "待审理",
        "reviewing": "审理中",
        "completed": "已结案",
    }.get(status, status or "未知")


# ─── 指路系统 ───
guide_channel_messages: dict = {}  # { "channel_id": "message_id" }

def load_guide_channels():
    global guide_channel_messages
    try:
        if os.path.exists(GUIDE_CHANNEL_FILE):
            with open(GUIDE_CHANNEL_FILE, "r", encoding="utf-8") as f:
                guide_channel_messages = json.load(f)
    except Exception:
        guide_channel_messages = {}

def save_guide_channels():
    try:
        with open(GUIDE_CHANNEL_FILE, "w", encoding="utf-8") as f:
            json.dump(guide_channel_messages, f)
    except Exception as e:
        logger.error(f"保存指路频道信息失败: {e}")

# ─── 下载日志 ───
# 结构: [{ "file_id": str, "file_name": str, "downloader_id": int, "downloader_name": str, "channel_id": int, "uploader_id": int, "uploader_name": str, "timestamp": "ISO datetime" }]
download_logs: list = []

def load_download_logs():
    global download_logs
    try:
        if os.path.exists(DOWNLOAD_LOGS_FILE):
            with open(DOWNLOAD_LOGS_FILE, "r", encoding="utf-8") as f:
                download_logs = json.load(f)
    except Exception:
        download_logs = []

def save_download_logs():
    try:
        with open(DOWNLOAD_LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump(download_logs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存下载日志失败: {e}")

# ─── 答题系统 ───
QUIZ_MAX_ERRORS = 0                # 最多允许错几题（0 = 必须全对，且须答完全部题目）
QUIZ_COOLDOWN_MINUTES = 25         # 每次失败后增加的冷却时间（分钟）
QUIZ_QUESTION_TIMEOUT = 300        # 每道题限时（秒），默认 5 分钟
QUIZ_VERIFIED_ROLE = "你过关！小岛居民"        # 答题通过后赋予的身份组
QUIZ_CHANNEL_KEYWORD = "答题"        # 答题频道名称关键词（包含此词即可）
QUIZ_CHANNEL_FILE = "quiz_channels.json"     # 答题频道消息记录
QUIZ_COOLDOWN_FILE = "quiz_cooldowns.json"   # 答题冷却记录
quiz_questions: list = []            # 从 questions.json 加载的题目
quiz_sessions: dict = {}             # 正在答题的用户: {user_id: {questions, current_index, answers, started_at}}
quiz_channel_messages: dict = {}     # 答题频道消息: {channel_id: message_id}
quiz_cooldowns: dict = {}            # 冷却记录: {user_id: {fail_count: int, cooldown_until: str|None}}


def load_questions():
    """从 JSON 加载题目"""
    global quiz_questions
    try:
        if os.path.exists(QUESTIONS_FILE):
            with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
                quiz_questions = json.load(f)
            logger.info(f"已加载 {len(quiz_questions)} 道题目")
    except Exception as e:
        logger.error(f"加载题目失败: {e}")
        quiz_questions = []


def save_records():
    """保存文件记录到 JSON"""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(file_records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存文件记录失败: {e}")


def load_records():
    """从 JSON 加载文件记录"""
    global file_records
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                file_records = json.load(f)
            logger.info(f"已加载 {len(file_records)} 条文件记录")
    except Exception as e:
        logger.error(f"加载文件记录失败: {e}")
        file_records = {}


def load_quiz_channels():
    """加载答题频道消息记录"""
    global quiz_channel_messages
    try:
        if os.path.exists(QUIZ_CHANNEL_FILE):
            with open(QUIZ_CHANNEL_FILE, "r", encoding="utf-8") as f:
                quiz_channel_messages = json.load(f)
    except Exception:
        quiz_channel_messages = {}


def save_quiz_channels():
    """保存答题频道消息记录"""
    try:
        with open(QUIZ_CHANNEL_FILE, "w", encoding="utf-8") as f:
            json.dump(quiz_channel_messages, f)
    except Exception as e:
        logger.error(f"保存答题频道信息失败: {e}")


def load_quiz_cooldowns():
    """加载答题冷却记录"""
    global quiz_cooldowns
    try:
        if os.path.exists(QUIZ_COOLDOWN_FILE):
            with open(QUIZ_COOLDOWN_FILE, "r", encoding="utf-8") as f:
                quiz_cooldowns = json.load(f)
    except Exception:
        quiz_cooldowns = {}


def save_quiz_cooldowns():
    """保存答题冷却记录"""
    try:
        with open(QUIZ_COOLDOWN_FILE, "w", encoding="utf-8") as f:
            json.dump(quiz_cooldowns, f)
    except Exception as e:
        logger.error(f"保存冷却记录失败: {e}")


# ═══════════════════════════════════════════
#  Bot 启动与就绪
# ═══════════════════════════════════════════

@bot.event
async def on_ready():
    load_records()
    load_questions()
    load_quiz_channels()
    load_quiz_cooldowns()
    load_storage_channels()
    load_faq()
    load_points()
    load_checkin_channels()
    load_reports()
    load_report_channels()
    load_report_counter()
    load_guide_channels()
    load_download_logs()
    load_channel_published()
    logger.info(f"✅ Bot 已上线: {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"📡 正在服务 {len(bot.guilds)} 个服务器")
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔧 已同步 {len(synced)} 个斜杠命令")
    except Exception as e:
        logger.error(f"命令同步失败: {e}")

    # 在答题频道中发布/更新答题按钮消息
    await setup_quiz_channels()

    # 在签到频道中发布/更新签到按钮消息
    await setup_checkin_channels()

    # 在举报频道中发布/更新举报按钮消息
    await setup_report_channels()

    # 在指路频道中发布/更新频道导航
    await setup_guide_channels()


# ═══════════════════════════════════════════
#  新成员自动身份组
# ═══════════════════════════════════════════

MEMBER_ROLE_NAME = "入岛新人"


@bot.event
async def on_member_join(member: discord.Member):
    role = discord.utils.get(member.guild.roles, name=MEMBER_ROLE_NAME)
    if role:
        try:
            await member.add_roles(role)
            logger.info(f"已为 {member.name} 分配 {MEMBER_ROLE_NAME} 身份组")
        except discord.Forbidden:
            pass


async def setup_quiz_channels():
    """在名称包含 QUIZ_CHANNEL_KEYWORD 的频道中发布答题按钮消息"""
    quiz_embed = discord.Embed(
        title="📝 入群审核答题",
        description="点击下方按钮开始答题，需要 **答完题库全部题目且全部答对** 才能通过审核。\n\n"
                    "点「查询冷却」可查看自己还要等多久。\n"
                    "如果按钮无法使用，请使用 `/答题` 或 `/冷却` 命令。",
        color=discord.Color.blue(),
    )
    quiz_embed.set_footer(text="答题消息仅自己可见")

    for guild in bot.guilds:
        for channel in guild.text_channels:
            if QUIZ_CHANNEL_KEYWORD not in channel.name:
                continue

            try:
                # 清理频道里所有 bot 之前发的消息，只保留一个
                existing_msg_id = quiz_channel_messages.get(str(channel.id))
                kept = False

                async for old_msg in channel.history(limit=50):
                    if old_msg.author.id != bot.user.id:
                        continue
                    if existing_msg_id and str(old_msg.id) == existing_msg_id:
                        # 这是记录中的那条，更新它
                        try:
                            await old_msg.edit(embed=quiz_embed, view=PersistentQuizView())
                            kept = True
                            logger.info(f"更新答题按钮: #{channel.name}")
                        except Exception:
                            pass
                    else:
                        # 多余的旧消息，删掉
                        try:
                            await old_msg.delete()
                        except Exception:
                            pass

                if not kept:
                    # 没有有效记录，发一条新的
                    msg = await channel.send(embed=quiz_embed, view=PersistentQuizView())
                    quiz_channel_messages[str(channel.id)] = str(msg.id)
                    save_quiz_channels()
                    logger.info(f"发布答题按钮: #{channel.name}")
            except discord.Forbidden:
                logger.warning(f"无权限在 #{channel.name} 发送消息")
            except Exception as e:
                logger.error(f"答题频道 #{channel.name} 设置失败: {e}")


# ═══════════════════════════════════════════
#  /ping - 检查 bot 是否在线
# ═══════════════════════════════════════════

@bot.tree.command(name="ping", description="检查机器人是否在线")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong！在线中", ephemeral=True)


# ═══════════════════════════════════════════
#  /回顶 - 回到主楼（第1楼）
# ═══════════════════════════════════════════

class BackToTopView(discord.ui.View):
    def __init__(self, jump_url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="回到首楼",
            style=discord.ButtonStyle.link,
            url=jump_url,
        ))


@bot.tree.command(name="回顶", description="生成只有自己能看到的回到首楼按钮")
async def back_to_top(interaction: discord.Interaction):
    async for first_msg in interaction.channel.history(oldest_first=True, limit=1):
        await interaction.response.send_message(
            content="\u200b",
            view=BackToTopView(first_msg.jump_url),
            ephemeral=True,
        )
        return
    await interaction.response.send_message("该频道还没有任何消息。", ephemeral=True)


# ═══════════════════════════════════════════
#  /上传文件 - 多附件上传 + 三阶段草稿/发布流程
#  阶段1: 上传文件 → 阶段2: 设置条件 → 阶段3: 编辑内容 → 确认发布
# ═══════════════════════════════════════════

def _build_condition_description(conditions: dict) -> str:
    parts = []
    if conditions.get("password"):
        parts.append("需要密码")
    if conditions.get("require_like_first"):
        parts.append("需要点赞首楼")
    if conditions.get("require_comment_first"):
        cnt = conditions.get("min_comment_length", 1)
        parts.append(f"需要评论首楼（至少{cnt}字）")
    if not parts:
        return "无条件，所有人可获取"
    return " | ".join(parts)


def _format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ─── 阶段1: 上传文件（表单内选文件）───

def _supports_modal_file_upload() -> bool:
    return hasattr(discord.ui, "FileUpload") and hasattr(discord.ui, "Label")


class UploadFileModal(discord.ui.Modal, title="上传文件"):
    def __init__(self):
        super().__init__()
        self.file_upload = discord.ui.FileUpload(
            min_values=1,
            max_values=10,
            required=True,
        )
        self.add_item(discord.ui.Label(
            text="选择文件",
            description="一次最多 10 个",
            component=self.file_upload,
        ))
        self.file_title = discord.ui.TextInput(
            label="标题",
            placeholder="不填则用第一个文件名",
            style=discord.TextStyle.short,
            required=False,
            max_length=100,
        )
        self.add_item(self.file_title)
        self.file_desc = discord.ui.TextInput(
            label="说明",
            placeholder="作者提示、注意事项，可不填",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
        )
        self.add_item(self.file_desc)
        self.file_password = discord.ui.TextInput(
            label="密码",
            placeholder="不填则无需密码",
            style=discord.TextStyle.short,
            required=False,
            max_length=50,
        )
        self.add_item(self.file_password)

    async def on_submit(self, interaction: discord.Interaction):
        attachments = list(self.file_upload.values or [])
        if not attachments:
            await interaction.response.send_message("请至少选择一个文件。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        title = (self.file_title.value or "").strip()
        description = (self.file_desc.value or "").strip()
        password = (self.file_password.value or "").strip() or None
        await _ingest_uploaded_files(interaction, attachments, title, description, password)


@bot.tree.command(name="上传文件", description="打开表单上传文件（标题、说明、密码都在表单里填）")
async def upload_file(interaction: discord.Interaction):
    if not _supports_modal_file_upload():
        await interaction.response.send_message(
            "当前 discord.py 不支持在表单里选文件，请升级到 2.7 或以上。",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(UploadFileModal())


async def _ingest_uploaded_files(
    interaction: discord.Interaction,
    attachments: list,
    title: str = "",
    description: str = "",
    password: Optional[str] = None,
):
    storage_channel = await get_or_create_storage_channel(interaction.guild)
    attachment_records = []
    total_size = 0

    for att in attachments:
        try:
            file_bytes = await att.read()
            discord_file = discord.File(
                fp=io.BytesIO(file_bytes),
                filename=att.filename,
            )
            storage_msg = await storage_channel.send(
                content=f"{att.filename} | 上传者: {interaction.user.display_name} (ID: {interaction.user.id})",
                file=discord_file,
            )
            attachment_records.append({
                "original_name": att.filename,
                "custom_name": att.filename,
                "storage_msg_id": str(storage_msg.id),
                "size": att.size,
            })
            total_size += att.size
        except Exception as e:
            logger.error(f"上传文件 {att.filename} 失败: {e}")
            await interaction.followup.send(f"上传 {att.filename} 失败: {e}", ephemeral=True)
            return

    existing_id, existing = _find_channel_file_bundle(
        interaction.channel.id, interaction.user.id
    )
    if existing_id and existing:
        existing.setdefault("attachments", []).extend(attachment_records)
        existing["size"] = int(existing.get("size") or 0) + total_size
        if title:
            existing["name"] = title
        if description:
            existing["description"] = description
        if password:
            existing.setdefault("conditions", {})["password"] = password
        existing["uploader_name"] = interaction.user.display_name
        existing.setdefault("updates", []).append({
            "time": _beijing_now().isoformat(),
            "files": [a["custom_name"] for a in attachment_records],
        })
        _ensure_resource_code(existing)
        save_records()
        await _upsert_storage_card(interaction.guild, existing_id, existing)
        if existing.get("status") == "published":
            await _refresh_published_card(interaction, existing_id, existing)
            names = "、".join(a["custom_name"] for a in attachment_records)
            await interaction.followup.send(
                f"已追加 {len(attachment_records)} 个文件到 **{existing['name']}**：{names}\n公开卡片已更新。",
                ephemeral=True,
            )
        else:
            await _show_draft_setup(interaction, existing_id, has_previous=True)
        return

    draft_id = attachment_records[0]["storage_msg_id"]
    default_name = title or attachments[0].filename
    conditions = {
        "password": password,
        "require_like_first": False,
        "require_comment_first": False,
        "min_comment_length": 0,
    }
    file_records[draft_id] = {
        "name": default_name,
        "uploader_id": interaction.user.id,
        "uploader_name": interaction.user.display_name,
        "source_channel_id": interaction.channel.id,
        "guild_id": interaction.guild.id,
        "size": total_size,
        "conditions": conditions,
        "description": description,
        "status": "draft",
        "published_msg_id": None,
        "attachments": attachment_records,
        "upload_time": _beijing_now().isoformat(),
        "resource_code": _new_resource_code(),
        "updates": [],
        "storage_card_msg_id": None,
    }
    save_records()
    await _upsert_storage_card(interaction.guild, draft_id, file_records[draft_id])
    await _show_draft_setup(interaction, draft_id, has_previous=False)


# ─── 阶段2: 草稿设置面板（条件 + 文件/附件名称修改）───

class DraftSetupView(discord.ui.View):
    """草稿设置面板：条件设置 + 文件/附件名称修改 + 进入阶段3"""

    def __init__(self, file_id: str, uploader_id: int):
        super().__init__(timeout=600)
        self.file_id = file_id
        self.uploader_id = uploader_id

    def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.uploader_id:
            return False
        return True

    @discord.ui.button(label="🔒 设置密码", style=discord.ButtonStyle.primary, row=0)
    async def set_password(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能设置。", ephemeral=True)
            return
        await interaction.response.send_modal(PasswordModal(self.file_id))

    @discord.ui.button(label="👍 需要点赞", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_like(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能设置。", ephemeral=True)
            return
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        record["conditions"]["require_like_first"] = not record["conditions"]["require_like_first"]
        save_records()
        await interaction.response.send_message(
            f"✅ 点赞要求已{'开启' if record['conditions']['require_like_first'] else '关闭'}",
            ephemeral=True,
        )

    @discord.ui.button(label="💬 需要评论", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_comment(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能设置。", ephemeral=True)
            return
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        record["conditions"]["require_comment_first"] = not record["conditions"]["require_comment_first"]
        if record["conditions"]["require_comment_first"]:
            record["conditions"]["min_comment_length"] = 1
        else:
            record["conditions"]["min_comment_length"] = 0
        save_records()
        await interaction.response.send_message(
            f"✅ 评论要求已{'开启（至少1字）' if record['conditions']['require_comment_first'] else '关闭'}",
            ephemeral=True,
        )

    @discord.ui.button(label="🔢 评论字数", style=discord.ButtonStyle.secondary, row=0)
    async def set_comment_count(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能设置。", ephemeral=True)
            return
        await interaction.response.send_modal(CommentLengthModal(self.file_id))

    @discord.ui.button(label="✏️ 修改文件标题", style=discord.ButtonStyle.success, row=1)
    async def rename_file(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能设置。", ephemeral=True)
            return
        await interaction.response.send_modal(RenameFileModal(self.file_id))

    @discord.ui.button(label="📎 修改附件名", style=discord.ButtonStyle.success, row=1)
    async def rename_attachments(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能设置。", ephemeral=True)
            return
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        await interaction.response.send_modal(RenameAttachmentsModal(self.file_id, record["attachments"]))

    @discord.ui.button(label="📝 编辑内容 →", style=discord.ButtonStyle.danger, row=2)
    async def go_to_content(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能设置。", ephemeral=True)
            return
        await interaction.response.send_modal(ContentEditModal(self.file_id))

    @discord.ui.button(label="📦 整合为ZIP", style=discord.ButtonStyle.secondary, row=2)
    async def pack_zip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能设置。", ephemeral=True)
            return
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        attachments = record.get("attachments", [])
        if len(attachments) < 2:
            await interaction.response.send_message("至少需要 2 个附件才能整合为 ZIP。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await _pack_attachments_to_zip(interaction, self.file_id, record)
        except Exception as e:
            logger.error(f"整合ZIP失败: {e}", exc_info=True)
            await interaction.followup.send(f"❌ 整合失败: {e}", ephemeral=True)

    @discord.ui.button(label="⚡ 快速发布", style=discord.ButtonStyle.success, row=3)
    async def quick_publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_owner(interaction):
            await interaction.response.send_message("只有上传者才能发布。", ephemeral=True)
            return
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await _publish_file(interaction, self.file_id, record)

        # 禁用所有按钮
        self.disable_all_items()
        if hasattr(self, "message") and self.message:
            await self.message.edit(view=self)

    async def on_timeout(self):
        self.disable_all_items()
        if hasattr(self, "message") and self.message:
            await self.message.edit(view=self)


async def _show_draft_setup(interaction: discord.Interaction, file_id: str, has_previous: bool = False):
    """显示草稿设置面板（阶段2）"""
    record = file_records.get(file_id)
    if not record:
        return

    cond_desc = _build_condition_description(record["conditions"])
    attach_list = "\n".join(
        f"📎 {a['custom_name']} ({_format_size(a['size'])})"
        for a in record["attachments"]
    )

    hint = ""
    if has_previous:
        hint = "\n💡 已继承频道上次的条件，可直接点击「⚡ 快速发布」"

    embed = discord.Embed(
        title="⚙️ 阶段2: 设置获取条件",
        description=f"**文件标题:** {record['name']}\n"
                    f"**附件数:** {len(record['attachments'])} 个\n\n"
                    f"**附件列表:**\n{attach_list}\n\n"
                    f"**当前条件:** {cond_desc}{hint}",
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text="快速发布跳过编辑直接发布 | 编辑内容可添加说明")

    view = DraftSetupView(file_id, interaction.user.id)
    view.message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def _pack_attachments_to_zip(interaction: discord.Interaction, file_id: str, record: dict):
    """将所有附件打包为 ZIP 并替换附件列表"""
    attachments = record["attachments"]
    guild = interaction.guild
    guild_id_str = str(record["guild_id"])
    channel_id = storage_channels.get(guild_id_str)
    if not channel_id:
        await interaction.followup.send("❌ 存储频道不存在。", ephemeral=True)
        return

    channel = guild.get_channel(int(channel_id))
    if not channel:
        await interaction.followup.send("❌ 存储频道已删除。", ephemeral=True)
        return

    # 从存储频道下载所有附件
    zip_buffer = io.BytesIO()
    total_zip_size = 0
    file_map = {}  # custom_name -> bytes

    for att in attachments:
        try:
            msg = await channel.fetch_message(int(att["storage_msg_id"]))
        except discord.NotFound:
            await interaction.followup.send(f"❌ 附件 {att['custom_name']} 已被删除。", ephemeral=True)
            return

        if not msg.attachments:
            await interaction.followup.send(f"❌ 附件 {att['custom_name']} 丢失。", ephemeral=True)
            return

        data = await msg.attachments[0].read()
        file_map[att["custom_name"]] = data

    # 创建 ZIP
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in file_map.items():
            zf.writestr(name, data)

    zip_buffer.seek(0)
    total_zip_size = zip_buffer.getbuffer().nbytes

    # 上传 ZIP 到存储频道
    zip_name = f"{record['name']}.zip"
    discord_file = discord.File(fp=zip_buffer, filename=zip_name)
    storage_channel = await get_or_create_storage_channel(guild)
    storage_msg = await storage_channel.send(
        content=f"{zip_name} | 整合ZIP | 上传者: {interaction.user.display_name}",
        file=discord_file,
    )

    # 替换附件列表为单个 ZIP
    record["attachments"] = [{
        "original_name": zip_name,
        "custom_name": zip_name,
        "storage_msg_id": str(storage_msg.id),
        "size": total_zip_size,
    }]
    record["size"] = total_zip_size
    _ensure_resource_code(record)
    save_records()
    await _upsert_storage_card(interaction.guild, file_id, record)

    await interaction.followup.send(
        f"已将 {len(attachments)} 个附件整合为 **{zip_name}** ({_format_size(total_zip_size)})",
        ephemeral=True,
    )


# ─── 阶段2 辅助 Modal：密码、评论字数、文件名、附件名 ───

class PasswordModal(discord.ui.Modal, title="设置密码"):
    def __init__(self, file_id: str):
        super().__init__()
        self.file_id = file_id
        self.pwd = discord.ui.TextInput(
            label="密码（留空则清除密码）",
            placeholder="输入密码，或留空跳过",
            style=discord.TextStyle.short,
            required=False,
            max_length=50,
        )
        self.add_item(self.pwd)

    async def on_submit(self, interaction: discord.Interaction):
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        record["conditions"]["password"] = self.pwd.value.strip() or None
        save_records()
        await interaction.response.send_message(
            f"✅ 密码已{'设置' if record['conditions']['password'] else '清除'}",
            ephemeral=True,
        )


class CommentLengthModal(discord.ui.Modal, title="设置评论字数"):
    def __init__(self, file_id: str):
        super().__init__()
        self.file_id = file_id
        self.count = discord.ui.TextInput(
            label="评论最少需要多少字？",
            placeholder="输入数字，如 15",
            style=discord.TextStyle.short,
            required=True,
            min_length=1,
            max_length=3,
        )
        self.add_item(self.count)

    async def on_submit(self, interaction: discord.Interaction):
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        try:
            count = int(self.count.value)
            if count < 1:
                count = 1
            if count > 500:
                count = 500
        except ValueError:
            await interaction.response.send_message("请输入有效数字。", ephemeral=True)
            return
        record["conditions"]["require_comment_first"] = True
        record["conditions"]["min_comment_length"] = count
        save_records()
        await interaction.response.send_message(
            f"✅ 已设置：评论首楼至少 {count} 字",
            ephemeral=True,
        )


class RenameFileModal(discord.ui.Modal, title="修改文件标题"):
    def __init__(self, file_id: str):
        super().__init__()
        self.file_id = file_id
        record = file_records.get(file_id, {})
        self.new_name = discord.ui.TextInput(
            label="新文件标题",
            placeholder="输入新的文件标题",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
            default=record.get("name", ""),
        )
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction):
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        record["name"] = self.new_name.value.strip()
        save_records()
        await _upsert_storage_card(interaction.guild, self.file_id, record)
        await interaction.response.send_message(
            f"文件标题已修改为：**{record['name']}**",
            ephemeral=True,
        )


class RenameAttachmentsModal(discord.ui.Modal, title="修改附件名称"):
    def __init__(self, file_id: str, attachments: list):
        super().__init__()
        self.file_id = file_id
        self.attach_count = len(attachments)
        # 最多支持 5 个附件改名（Discord Modal 最多 5 个 TextInput）
        for i, att in enumerate(attachments[:5]):
            field = discord.ui.TextInput(
                label=f"附件{i + 1}名称",
                placeholder=att["custom_name"],
                style=discord.TextStyle.short,
                required=False,
                max_length=100,
                default=att["custom_name"],
            )
            self.add_item(field)
            setattr(self, f"attach_{i}", field)

    async def on_submit(self, interaction: discord.Interaction):
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        changed = []
        for i in range(min(self.attach_count, 5)):
            field = getattr(self, f"attach_{i}", None)
            if field and field.value.strip():
                record["attachments"][i]["custom_name"] = field.value.strip()
                changed.append(field.value.strip())
        save_records()
        await _upsert_storage_card(interaction.guild, self.file_id, record)
        await interaction.response.send_message(
            f"已更新 {len(changed)} 个附件名称",
            ephemeral=True,
        )


# ─── 阶段3: 编辑内容 + 确认发布 ───

class ContentEditModal(discord.ui.Modal, title="编辑内容与作者提示"):
    def __init__(self, file_id: str):
        super().__init__()
        self.file_id = file_id
        record = file_records.get(file_id, {})
        self.content = discord.ui.TextInput(
            label="作者提示 / 内容说明",
            placeholder="输入文件说明、作者提示、注意事项等...",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=2000,
            default=record.get("description", ""),
        )
        self.add_item(self.content)

    async def on_submit(self, interaction: discord.Interaction):
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        record["description"] = self.content.value.strip()
        save_records()

        # 显示预览 + 确认发布
        await _show_publish_preview(interaction, self.file_id)


async def _show_publish_preview(interaction: discord.Interaction, file_id: str):
    """显示发布预览 + 确认发布按钮（阶段3）"""
    record = file_records.get(file_id)
    if not record:
        await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
        return

    cond_desc = _build_condition_description(record["conditions"])
    attach_list = "\n".join(
        f"📎 {a['custom_name']} ({_format_size(a['size'])})"
        for a in record["attachments"]
    )
    desc = record.get("description", "") or "（无说明）"

    embed = discord.Embed(
        title="📋 阶段3: 预览确认",
        description=f"**文件标题:** {record['name']}\n"
                    f"**总大小:** {_format_size(record['size'])}\n\n"
                    f"**附件:**\n{attach_list}\n\n"
                    f"**获取条件:** {cond_desc}\n\n"
                    f"**内容说明:**\n{desc[:500]}",
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text="确认无误后点击「确认发布」")

    view = PublishConfirmView(file_id, interaction.user.id)
    view.message = await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class PublishConfirmView(discord.ui.View):
    """确认发布按钮"""

    def __init__(self, file_id: str, uploader_id: int):
        super().__init__(timeout=300)
        self.file_id = file_id
        self.uploader_id = uploader_id

    @discord.ui.button(label="✅ 确认发布", style=discord.ButtonStyle.success)
    async def confirm_publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uploader_id:
            await interaction.response.send_message("只有上传者才能发布。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        record = file_records.get(self.file_id)
        if not record:
            await interaction.followup.send("文件记录已丢失。", ephemeral=True)
            return

        # 发布！
        await _publish_file(interaction, self.file_id, record)

        # 禁用按钮
        self.disable_all_items()
        if hasattr(self, "message") and self.message:
            await self.message.edit(view=self)

    @discord.ui.button(label="↩️ 返回修改", style=discord.ButtonStyle.secondary)
    async def back_to_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uploader_id:
            await interaction.response.send_message("只有上传者才能操作。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _show_draft_setup(interaction, self.file_id)

    async def on_timeout(self):
        self.disable_all_items()
        if hasattr(self, "message") and self.message:
            await self.message.edit(view=self)


def _find_channel_file_bundle(channel_id, uploader_id):
    channel_key = str(channel_id)
    pub = channel_published.get(channel_key)
    if pub:
        rec = file_records.get(pub.get("file_id"))
        if rec and rec.get("uploader_id") == uploader_id:
            return pub.get("file_id"), rec
    drafts = [
        (fid, rec) for fid, rec in file_records.items()
        if str(rec.get("source_channel_id")) == channel_key
        and rec.get("uploader_id") == uploader_id
        and rec.get("status") == "draft"
    ]
    if drafts:
        drafts.sort(key=lambda x: x[1].get("upload_time", ""), reverse=True)
        return drafts[0]
    published = [
        (fid, rec) for fid, rec in file_records.items()
        if str(rec.get("source_channel_id")) == channel_key
        and rec.get("uploader_id") == uploader_id
        and rec.get("status") == "published"
    ]
    if published:
        published.sort(key=lambda x: x[1].get("upload_time", ""), reverse=True)
        return published[0]
    return None, None


async def _refresh_published_card(interaction: discord.Interaction, file_id: str, record: dict):
    channel_id = str(interaction.channel.id)
    embed, view = _build_published_card(record, file_id)
    pub = channel_published.get(channel_id)
    if pub:
        try:
            old_msg = await interaction.channel.fetch_message(int(pub["message_id"]))
            await old_msg.edit(embed=embed, view=view)
            record["published_msg_id"] = str(old_msg.id)
            save_records()
            return
        except Exception:
            try:
                old_msg = await interaction.channel.fetch_message(int(pub["message_id"]))
                await old_msg.delete()
            except Exception:
                pass
    pub_msg = await interaction.channel.send(embed=embed, view=view)
    record["published_msg_id"] = str(pub_msg.id)
    channel_published[channel_id] = {
        "message_id": str(pub_msg.id),
        "file_id": file_id,
    }
    save_records()
    save_channel_published()


async def _publish_file(interaction: discord.Interaction, file_id: str, record: dict):
    """发布文件到频道：删除旧发布卡片，创建新卡片"""
    channel_id = str(interaction.channel.id)

    # 删除该频道之前的发布卡片
    old_pub = channel_published.get(channel_id)
    if old_pub:
        try:
            old_msg = await interaction.channel.fetch_message(int(old_pub["message_id"]))
            await old_msg.delete()
        except Exception:
            pass

    record["status"] = "published"
    save_records()

    # 创建公开卡片
    embed, view = _build_published_card(record, file_id)

    try:
        pub_msg = await interaction.channel.send(embed=embed, view=view)
        record["published_msg_id"] = str(pub_msg.id)
        channel_published[channel_id] = {
            "message_id": str(pub_msg.id),
            "file_id": file_id,
        }
        save_records()
        save_channel_published()
        await _upsert_storage_card(interaction.guild, file_id, record)

        await interaction.followup.send(
            f"文件 **{record['name']}** 已发布到频道！",
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"发布文件失败: {e}")
        await interaction.followup.send(f"❌ 发布失败: {e}", ephemeral=True)


# ─── 公开卡片构建 ───

def _build_published_card(record: dict, file_id: str, persistent: bool = True):
    cond_desc = _build_condition_description(record.get("conditions") or {})
    desc = record.get("description", "") or "（无说明）"
    attach_count = len(record.get("attachments") or [])
    embed = discord.Embed(
        title=f"📁 {record['name']}",
        description=f"{desc[:1000]}\n\n"
                    f"**附件数:** {attach_count} 个\n"
                    f"**总大小:** {_format_size(record['size'])}\n"
                    f"**获取条件:** {cond_desc}",
        color=discord.Color.purple(),
        timestamp=datetime.fromisoformat(record["upload_time"]) if record.get("upload_time") else datetime.now(),
    )
    embed.set_footer(text=f"上传者: {record.get('uploader_name', '未知')} | 先满足条件，再选择文件后下载")
    view = PublishedFileView(file_id, record, persistent=persistent)
    return embed, view


_download_selections: dict = {}


def _selection_key(interaction: discord.Interaction, file_id: str = "") -> tuple:
    return (interaction.user.id, str(interaction.channel.id), file_id or "")


def _resolve_published_record(interaction: discord.Interaction, file_id: str = ""):
    if file_id and file_id in file_records:
        return file_id, file_records[file_id]
    channel_id = str(interaction.channel.id)
    pub_info = channel_published.get(channel_id)
    if pub_info:
        rec = file_records.get(pub_info.get("file_id"))
        if rec:
            return pub_info["file_id"], rec
    return None, None


async def _check_download_prereqs(interaction: discord.Interaction, record: dict) -> list:
    conditions = record.get("conditions") or {}
    failed = []
    if conditions.get("require_like_first"):
        if not await _check_user_liked_first(interaction):
            failed.append("需要给首楼点赞")
    if conditions.get("require_comment_first"):
        min_len = conditions.get("min_comment_length", 1)
        if not await _check_user_comment_length(interaction, min_len):
            failed.append(f"需要评论首楼至少 {min_len} 字")
    return failed


def _selected_indices(interaction: discord.Interaction, record: dict, file_id: str = "") -> list:
    values = _download_selections.get(_selection_key(interaction, file_id), ["file_0"])
    attachments = record.get("attachments") or []
    indices = []
    for value in values:
        if isinstance(value, str) and value.startswith("file_"):
            try:
                idx = int(value.split("_")[1])
            except ValueError:
                continue
            if 0 <= idx < len(attachments) and idx not in indices:
                indices.append(idx)
    if not indices and attachments:
        indices = [0]
    return indices


async def _deliver_selected_files(interaction: discord.Interaction, record: dict, indices: list):
    success = 0
    for idx in indices:
        await _send_attachment_to_user(interaction, record, idx)
        success += 1
    if success > 0:
        await interaction.followup.send(f"已发送 {success} 个文件", ephemeral=True)
    else:
        await interaction.followup.send("未选择有效文件或发送失败", ephemeral=True)


async def _start_download_flow(interaction: discord.Interaction, file_id: str, record: dict, already_deferred: bool = False):
    failed = await _check_download_prereqs(interaction, record)
    if failed:
        text = "前置条件未满足：" + "，".join(failed)
        if already_deferred:
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
        return
    indices = _selected_indices(interaction, record, file_id)
    password = (record.get("conditions") or {}).get("password")
    if password:
        if already_deferred:
            await interaction.followup.send("前置条件已通过。请再点一次下载以填写密码。", ephemeral=True)
            return
        await interaction.response.send_modal(DownloadPasswordModal(file_id, indices, password))
        return
    if not already_deferred:
        await interaction.response.defer(ephemeral=True)
    await _deliver_selected_files(interaction, record, indices)


class PublishedFileView(discord.ui.View):
    def __init__(self, file_id: str = "", record: dict = None, persistent: bool = True):
        super().__init__(timeout=None if persistent else 300)
        self._file_id = file_id
        options = []
        if record:
            for i, att in enumerate(record.get("attachments", [])[:25]):
                options.append(discord.SelectOption(
                    label=att["custom_name"][:80],
                    description=_format_size(att["size"]),
                    value=f"file_{i}",
                ))
        select_kwargs = {
            "placeholder": f"选择要下载的文件（可多选，共 {len(options)} 项）" if options else "选择文件",
            "options": options if options else [discord.SelectOption(label="—", value="none")],
            "row": 0,
            "min_values": 1,
            "max_values": max(len(options), 1) if options else 1,
        }
        if persistent:
            select_kwargs["custom_id"] = "pub_file_select"
        self.select_menu = discord.ui.Select(**select_kwargs)
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)
        button_kwargs = {
            "label": "下载",
            "style": discord.ButtonStyle.primary,
            "row": 1,
        }
        if persistent:
            button_kwargs["custom_id"] = "pub_file_download"
        self.download_btn = discord.ui.Button(**button_kwargs)
        self.download_btn.callback = self.on_download
        self.add_item(self.download_btn)

    async def on_select(self, interaction: discord.Interaction):
        values = []
        if hasattr(interaction, "data") and interaction.data:
            values = list(interaction.data.get("values") or [])
        if not values:
            values = list(self.select_menu.values or [])
        _download_selections[_selection_key(interaction, self._file_id)] = values
        await interaction.response.defer()

    async def on_download(self, interaction: discord.Interaction):
        file_id, record = _resolve_published_record(interaction, self._file_id)
        if not record:
            await interaction.response.send_message("发布记录已过期。", ephemeral=True)
            return
        await _start_download_flow(interaction, file_id, record)


class DownloadPasswordModal(discord.ui.Modal, title="填写下载密码"):
    def __init__(self, file_id: str, indices: list, expected: str):
        super().__init__()
        self.file_id = file_id
        self.indices = indices
        self.expected = expected
        self.pwd = discord.ui.TextInput(
            label="下载密码",
            placeholder="前置已通过，请填写密码",
            style=discord.TextStyle.short,
            required=True,
            max_length=50,
        )
        self.add_item(self.pwd)

    async def on_submit(self, interaction: discord.Interaction):
        if self.pwd.value.strip() != self.expected:
            await interaction.response.send_message("密码不正确。", ephemeral=True)
            return
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _deliver_selected_files(interaction, record, self.indices)


async def _send_attachment_to_user(interaction: discord.Interaction, record: dict, idx: int):
    """发送指定附件给用户"""
    att = record["attachments"][idx]
    try:
        guild = interaction.client.get_guild(record["guild_id"])
        if not guild:
            await interaction.followup.send("❌ 找不到服务器。", ephemeral=True)
            return

        guild_id_str = str(record["guild_id"])
        channel_id = storage_channels.get(guild_id_str)
        if not channel_id:
            await interaction.followup.send("❌ 存储频道不存在。", ephemeral=True)
            return

        channel = guild.get_channel(int(channel_id))
        if not channel:
            await interaction.followup.send("❌ 存储频道已删除。", ephemeral=True)
            return

        try:
            msg = await channel.fetch_message(int(att["storage_msg_id"]))
        except discord.NotFound:
            await interaction.followup.send("❌ 文件已被删除。", ephemeral=True)
            return

        if not msg.attachments:
            await interaction.followup.send("❌ 文件附件丢失。", ephemeral=True)
            return

        attachment = msg.attachments[0]
        file_bytes = await attachment.read()

        discord_file = discord.File(
            fp=io.BytesIO(file_bytes),
            filename=att["custom_name"],
        )
        await interaction.followup.send(
            content=f"📁 **{att['custom_name']}**\n上传者: <@{record['uploader_id']}>",
            file=discord_file,
            ephemeral=True,
        )

        _log_download(interaction, record, att["custom_name"])

    except Exception as e:
        logger.error(f"发送附件失败: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 获取文件时出错: {e}", ephemeral=True)


def _log_download(interaction: discord.Interaction, record: dict, file_label: str):
    record_id = None
    for fid, rec in file_records.items():
        if rec is record:
            record_id = fid
            break
    download_logs.append({
        "file_id": record.get("published_msg_id", "?"),
        "record_id": record_id,
        "resource_code": record.get("resource_code"),
        "file_name": record.get("name", "?"),
        "file_label": file_label,
        "downloader_id": interaction.user.id,
        "downloader_name": interaction.user.display_name,
        "channel_id": interaction.channel.id,
        "uploader_id": record.get("uploader_id", "?"),
        "uploader_name": record.get("uploader_name", "?"),
        "timestamp": _beijing_now().isoformat(),
    })
    save_download_logs()


# ═══════════════════════════════════════════
#  /获取文件 - 与公开卡片同一套获取界面
# ═══════════════════════════════════════════

@bot.tree.command(name="获取文件", description="用与公开卡片相同的界面获取当前帖的文件")
async def get_file(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    channel_id = str(interaction.channel.id)
    file_id = None
    record = None
    pub = channel_published.get(channel_id)
    if pub:
        rec = file_records.get(pub.get("file_id"))
        if rec and rec.get("status") == "published":
            file_id, record = pub["file_id"], rec
    if not record:
        published = [
            (fid, rec) for fid, rec in file_records.items()
            if str(rec.get("source_channel_id")) == channel_id
            and rec.get("status") == "published"
        ]
        if published:
            published.sort(key=lambda x: x[1].get("upload_time", ""), reverse=True)
            file_id, record = published[0]
    if not record:
        await interaction.followup.send("当前频道还没有已发布文件。", ephemeral=True)
        return
    embed, view = _build_published_card(record, file_id, persistent=False)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def _check_user_liked_first(interaction: discord.Interaction) -> bool:
    """检查用户是否给频道第一条消息点了赞（任意表情）"""
    try:
        async for first_msg in interaction.channel.history(oldest_first=True, limit=1):
            for reaction in first_msg.reactions:
                async for user in reaction.users():
                    if user.id == interaction.user.id:
                        return True
            return False
        return False
    except Exception:
        return False


async def _check_user_comment_length(interaction: discord.Interaction, min_length: int) -> bool:
    """检查用户是否在首楼下发过至少 min_length 字的评论"""
    try:
        async for first_msg in interaction.channel.history(oldest_first=True, limit=1):
            first_msg_id = first_msg.id
            async for msg in interaction.channel.history(after=first_msg, limit=200):
                if msg.author.id != interaction.user.id:
                    continue
                ref = msg.reference
                if ref is None:
                    continue
                ref_msg_id = getattr(ref, "message_id", None)
                if ref_msg_id is None:
                    continue
                if ref_msg_id == first_msg_id:
                    if len(msg.content) >= min_length:
                        return True
            return False
        return False
    except Exception as e:
        logger.error(f"检查评论长度失败: {e}")
        return False


# ═══════════════════════════════════════════
#  答题系统 - 逐题按钮 + 冷却机制
# ═══════════════════════════════════════════

class PersistentQuizView(discord.ui.View):
    """答题频道持久化按钮视图（无超时，重启后恢复）"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="开始答题",
        style=discord.ButtonStyle.primary,
        emoji="✍️",
        custom_id="persistent_quiz_start",
    )
    async def quiz_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_quiz(interaction)

    @discord.ui.button(
        label="查询冷却",
        style=discord.ButtonStyle.secondary,
        emoji="⏳",
        custom_id="persistent_quiz_cooldown",
    )
    async def cooldown_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_cooldown_check(interaction)


def _build_question_embed(q: dict, idx: int, total: int) -> discord.Embed:
    """构建单题 embed"""
    embed = discord.Embed(
        title=f"📝 第 {idx + 1}/{total} 题",
        description=q["question"],
        color=discord.Color.blue(),
    )
    for opt in q["options"]:
        embed.add_field(name=opt, value="", inline=False)
    embed.set_footer(text="点击下方按钮选择答案，选择后不可更改")
    return embed


class QuizQuestionView(discord.ui.View):
    """单题选项按钮视图，限时作答"""

    def __init__(self, user_id: int, q_index: int, total: int, interaction: discord.Interaction):
        super().__init__(timeout=QUIZ_QUESTION_TIMEOUT)
        self.user_id = user_id
        self.q_index = q_index
        self.total = total
        self._interaction = interaction

        for label in ["A", "B", "C", "D"]:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, row=0)
            btn.callback = _make_answer_callback(user_id, q_index, total, label)
            self.add_item(btn)

    async def on_timeout(self):
        """超时自动清理答题 session"""
        session = quiz_sessions.get(self.user_id)
        if session and session.get("current_index") == self.q_index:
            quiz_sessions.pop(self.user_id, None)
            try:
                await self._interaction.followup.send(
                    f"⏰ 第 {self.q_index + 1} 题答题超时（限时 {QUIZ_QUESTION_TIMEOUT // 60} 分钟），答题已结束，请重新开始。",
                    ephemeral=True,
                )
            except Exception:
                pass


def _make_answer_callback(user_id: int, q_index: int, total: int, answer: str):
    """创建选项按钮回调"""
    async def callback(interaction: discord.Interaction):
        if interaction.user.id != user_id:
            await interaction.response.send_message("这不是你的答题！", ephemeral=True)
            return

        session = quiz_sessions.get(user_id)
        if not session:
            await interaction.response.send_message("答题已过期，请重新开始。", ephemeral=True)
            return

        # 记录答案
        session["answers"].append(answer)
        next_index = q_index + 1

        if next_index >= total:
            # 答完所有题，显示结果
            await _show_results(interaction, session)
        else:
            # 显示下一题
            session["current_index"] = next_index
            q = session["questions"][next_index]
            embed = _build_question_embed(q, next_index, total)
            view = QuizQuestionView(user_id, next_index, total, interaction)
            await interaction.response.edit_message(embed=embed, view=view)

    return callback


async def _show_results(interaction: discord.Interaction, session: dict):
    """显示答题结果，处理通过/失败和冷却"""
    questions = session["questions"]
    answers = session["answers"]
    total = len(questions)
    user_id = interaction.user.id

    correct = 0
    wrong_nums = []
    for i, q in enumerate(questions):
        expected = q["answer"].upper().strip()
        given = answers[i] if i < len(answers) else "?"
        if given == expected:
            correct += 1
        else:
            wrong_nums.append(i + 1)

    errors = total - correct
    passed = errors <= QUIZ_MAX_ERRORS and len(answers) >= total

    # ── 冷却处理 ──
    cooldown = quiz_cooldowns.get(str(user_id), {"fail_count": 0, "cooldown_until": None})
    cooldown_minutes = 0

    if not passed:
        cooldown["fail_count"] = cooldown.get("fail_count", 0) + 1
        fail_count = cooldown["fail_count"]
        cooldown_minutes = max(0, (fail_count - 1) * QUIZ_COOLDOWN_MINUTES)
        if cooldown_minutes > 0:
            cooldown["cooldown_until"] = (datetime.now() + timedelta(minutes=cooldown_minutes)).isoformat()
        else:
            cooldown["cooldown_until"] = None
        quiz_cooldowns[str(user_id)] = cooldown
        save_quiz_cooldowns()
    else:
        quiz_cooldowns.pop(str(user_id), None)
        save_quiz_cooldowns()

    # ── 构建结果 embed ──
    if passed:
        color = discord.Color.green()
        title = "🎉 答题通过！"
        description = (
            f"正确 {correct}/{total} 题，恭喜你获得了「{QUIZ_VERIFIED_ROLE}」身份组！\n\n"
            f"现在你可以去探索更多小岛内容了，祝你玩得开心～"
        )
    else:
        color = discord.Color.red()
        title = "❌ 答题未通过"
        wrong_list = "、".join(f"第{n}题" for n in wrong_nums)
        description = (
            f"正确 {correct}/{total} 题，错了 {errors} 题（必须全部答对才能通过）\n\n"
            f"答错的题目：{wrong_list}\n\n"
            f"📖 建议去查看社区规则和公告，了解清楚后再来答题哦～"
        )
        if cooldown_minutes > 0:
            description += (
                f"\n\n⏳ 第 {cooldown['fail_count']} 次失败，需要等待 **{cooldown_minutes} 分钟** 后才能重新答题\n"
                f"可点下方「查询冷却」随时查看剩余时间。"
            )
        else:
            description += "\n\n你可以立即重新答题"
        description += "\n\n💪 别灰心，下次一定能过！"

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    result_view = None if passed else PersistentQuizView()
    msg = await interaction.response.edit_message(embed=embed, view=result_view)

    # 通过结果一段时间后自动删除；失败结果保留按钮供查询冷却
    if passed:
        async def _auto_delete():
            await asyncio.sleep(30)
            try:
                await msg.delete()
            except Exception:
                pass

        asyncio.create_task(_auto_delete())

    # 通过后分配身份组
    if passed:
        role = discord.utils.get(interaction.guild.roles, name=QUIZ_VERIFIED_ROLE)
        if role:
            try:
                await interaction.user.add_roles(role)
                logger.info(f"答题通过: {interaction.user.name} 获得 {role.name} 身份组")
            except discord.Forbidden:
                logger.warning(f"无法为 {interaction.user.name} 分配身份组 {QUIZ_VERIFIED_ROLE}")
        else:
            logger.warning(f"服务器中没有找到「{QUIZ_VERIFIED_ROLE}」身份组")

    # 清理 session
    quiz_sessions.pop(user_id, None)


def _get_quiz_cooldown_status(user_id: int) -> tuple[bool, int, int]:
    """返回 (是否在冷却中, 剩余分钟, 失败次数)"""
    cooldown = quiz_cooldowns.get(str(user_id))
    if not cooldown:
        return False, 0, 0
    fail_count = int(cooldown.get("fail_count") or 0)
    until_raw = cooldown.get("cooldown_until")
    if not until_raw:
        return False, 0, fail_count
    try:
        until = datetime.fromisoformat(until_raw)
    except ValueError:
        return False, 0, fail_count
    remaining = until - datetime.now()
    if remaining.total_seconds() <= 0:
        return False, 0, fail_count
    minutes = int(remaining.total_seconds() // 60) + 1
    return True, minutes, fail_count


async def _do_cooldown_check(interaction: discord.Interaction):
    """查询答题冷却，供按钮和 /冷却 命令共用"""
    role = discord.utils.get(interaction.user.roles, name=QUIZ_VERIFIED_ROLE)
    if role:
        await interaction.response.send_message(
            f"✅ 你已经通过了入群审核，拥有「{QUIZ_VERIFIED_ROLE}」身份组，没有冷却。",
            ephemeral=True,
        )
        return

    in_cooldown, minutes, fail_count = _get_quiz_cooldown_status(interaction.user.id)
    if in_cooldown:
        await interaction.response.send_message(
            f"⏳ 你还在冷却中。\n\n"
            f"失败次数：**{fail_count}**\n"
            f"还需等待约 **{minutes} 分钟** 才能重新答题。",
            ephemeral=True,
        )
        return

    if fail_count > 0:
        await interaction.response.send_message(
            f"✅ 当前没有冷却，可以重新答题。\n\n累计失败次数：**{fail_count}**",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "✅ 当前没有冷却，可以直接开始答题。",
        ephemeral=True,
    )


async def _do_quiz(interaction: discord.Interaction):
    """答题核心逻辑，供 /答题 命令和答题频道按钮共用"""
    user_id = interaction.user.id

    # 检查是否已经通过答题
    role = discord.utils.get(interaction.user.roles, name=QUIZ_VERIFIED_ROLE)
    if role:
        await interaction.response.send_message(
            "✅ 你已经通过了入群审核，拥有「{0}」身份组，无需再次答题！".format(QUIZ_VERIFIED_ROLE),
            ephemeral=True,
        )
        return

    # 检查是否有进行中的答题
    if user_id in quiz_sessions:
        await interaction.response.send_message(
            "你有一个答题正在进行中！请先完成它。",
            ephemeral=True,
        )
        return

    # 检查冷却时间
    in_cooldown, minutes, fail_count = _get_quiz_cooldown_status(user_id)
    if in_cooldown:
        await interaction.response.send_message(
            f"⏳ 你还需要等待约 **{minutes} 分钟** 才能重新答题。\n"
            f"失败次数：**{fail_count}**。可点「查询冷却」或使用 `/冷却` 随时查看。",
            ephemeral=True,
        )
        return

    # 检查题库
    if not quiz_questions:
        await interaction.response.send_message(
            "题库中没有题目，请联系管理员添加题目。",
            ephemeral=True,
        )
        return

    # 打乱全部题目后开始答题
    selected = quiz_questions[:]
    random.shuffle(selected)
    total = len(selected)
    quiz_sessions[user_id] = {
        "questions": selected,
        "current_index": 0,
        "answers": [],
        "started_at": datetime.now().isoformat(),
    }

    q = selected[0]
    embed = _build_question_embed(q, 0, total)
    view = QuizQuestionView(user_id, 0, total, interaction)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ═══════════════════════════════════════════
#  /答题 - 备用命令（答题频道按钮失效时使用）
# ═══════════════════════════════════════════

@bot.tree.command(name="答题", description="开始入群审核答题，须答完全部题目且全部答对（备用命令）")
async def start_quiz(interaction: discord.Interaction):
    await _do_quiz(interaction)


@bot.tree.command(name="冷却", description="查询自己的答题冷却剩余时间")
async def cooldown_command(interaction: discord.Interaction):
    await _do_cooldown_check(interaction)


MASK_REMINDER_TEXT = (
    "截图或发日志前，先把这些打码：\n"
    "- API Key / Token / Cookie / 密码\n"
    "- 服务器 IP、端口、面板登录地址\n"
    "- 邮箱、手机号、真实姓名等个人信息\n\n"
    "可以留：系统环境、启动方式、模型名、预设名、报错原文、自己试过的步骤。\n"
    "密钥一旦进公屏，立刻撤回并告诉管理。"
)


@bot.tree.command(name="提醒遮挡", description="提醒截图前遮挡密钥、Token 和服务器地址")
async def mask_reminder(interaction: discord.Interaction):
    embed = discord.Embed(
        title="截图前先遮挡",
        description=MASK_REMINDER_TEXT,
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════
#  FAQ 常见问题系统
# ═══════════════════════════════════════════

@bot.tree.command(name="添加faq", description="添加一条常见问题（仅服务器最高权限者可用）")
@app_commands.describe(关键词="触发关键词", 答案="自动回复的内容")
async def add_faq(interaction: discord.Interaction, 关键词: str, 答案: str):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("只有服务器最高权限者才能使用此命令。", ephemeral=True)
        return

    keyword = 关键词.strip().lower()
    if len(keyword) < 2:
        await interaction.response.send_message("关键词至少需要2个字符。", ephemeral=True)
        return

    faq_data[keyword] = 答案.strip()
    save_faq()
    await interaction.response.send_message(
        f"✅ 已添加 FAQ：**{keyword}** → {答案}",
        ephemeral=True,
    )


@bot.tree.command(name="删除faq", description="删除一条常见问题（仅服务器最高权限者可用）")
@app_commands.describe(关键词="要删除的关键词")
async def remove_faq(interaction: discord.Interaction, 关键词: str):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("只有服务器最高权限者才能使用此命令。", ephemeral=True)
        return

    keyword = 关键词.strip().lower()
    if keyword in faq_data:
        del faq_data[keyword]
        save_faq()
        await interaction.response.send_message(f"✅ 已删除 FAQ：**{keyword}**", ephemeral=True)
    else:
        await interaction.response.send_message(f"未找到关键词 **{keyword}**。", ephemeral=True)


@bot.tree.command(name="常见问题", description="查看所有常见问题列表")
async def list_faq(interaction: discord.Interaction):
    if not faq_data:
        await interaction.response.send_message("📭 还没有添加任何常见问题。", ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 常见问题列表",
        color=discord.Color.blue(),
    )
    for keyword, answer in faq_data.items():
        embed.add_field(
            name=f"💬 {keyword}",
            value=answer[:200] + ("..." if len(answer) > 200 else ""),
            inline=False,
        )

    embed.set_footer(text=f"共 {len(faq_data)} 条 | 发送包含关键词的消息即可自动回复")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# FAQ 自动回复监听 + 举报线程证据同步
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not message.guild:
        return

    if isinstance(message.channel, discord.Thread):
        rid, rec = _find_report_by_thread(str(message.channel.id))
        if rec and rec.get("status") == "reviewing" and str(message.author.id) == str(rec.get("reporter_id")):
            pending = rec.setdefault("pending_evidence", [])
            if message.attachments:
                for att in message.attachments:
                    pending.append({
                        "type": "image",
                        "content": att.url,
                        "author": message.author.display_name,
                        "time": datetime.now().isoformat(),
                    })
            if message.content.strip():
                pending.append({
                    "type": "text",
                    "content": message.content[:500],
                    "author": message.author.display_name,
                    "time": datetime.now().isoformat(),
                })
            if pending:
                save_reports()
                try:
                    await message.add_reaction("📥")
                except Exception:
                    pass

    # 检查消息中是否包含 FAQ 关键词
    content = message.content.strip().lower()
    for keyword, answer in faq_data.items():
        if keyword in content:
            try:
                await message.reply(answer, mention_author=False)
            except Exception:
                pass
            break  # 只匹配第一个关键词

    # 必须调用，否则斜杠命令不会响应
    await bot.process_commands(message)


# ═══════════════════════════════════════════
#  公告推送
# ═══════════════════════════════════════════

@bot.tree.command(name="公告", description="发送公告到指定频道（仅服务器最高权限者可用）")
@app_commands.describe(频道="目标频道", 内容="公告内容")
async def announce(interaction: discord.Interaction, 频道: discord.TextChannel, 内容: str):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("只有服务器最高权限者才能使用此命令。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="📢 公告",
        description=内容,
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"发布者: {interaction.user.display_name}")

    try:
        await 频道.send(embed=embed)
        await interaction.followup.send(f"✅ 公告已发送到 {频道.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"❌ 没有权限在 {频道.mention} 发送消息。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 发送失败: {e}", ephemeral=True)


# ═══════════════════════════════════════════
#  签到系统 - 每日签到 + 随机积分
# ═══════════════════════════════════════════

class PersistentCheckinView(discord.ui.View):
    """签到频道持久化按钮视图（无超时，重启后恢复）"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="每日签到",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="persistent_checkin",
    )
    async def checkin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_checkin(interaction)

    @discord.ui.button(
        label="查看积分",
        style=discord.ButtonStyle.primary,
        emoji="💰",
        custom_id="persistent_checkin_points",
    )
    async def points_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_points_query(interaction)

    @discord.ui.button(
        label="签到天数",
        style=discord.ButtonStyle.secondary,
        emoji="📅",
        custom_id="persistent_checkin_days",
    )
    async def days_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_checkin_days_query(interaction)


async def setup_checkin_channels():
    """在名称包含 CHECKIN_CHANNEL_KEYWORD 的频道中发布签到按钮消息"""
    checkin_embed = discord.Embed(
        title="🏝️ 小岛每日签到",
        description="每天签到可获得 **1~20 随机积分**！\n\n"
                    "点「查看积分」看当前分数，点「签到天数」看连续和累计天数。\n"
                    "也可以使用 `/签到`、`/积分`、`/签到天数`。",
        color=discord.Color.green(),
    )
    checkin_embed.set_footer(text="每天只能签到一次，北京时间 0 点刷新")

    for guild in bot.guilds:
        for channel in guild.text_channels:
            if CHECKIN_CHANNEL_KEYWORD not in channel.name:
                continue

            try:
                existing_msg_id = checkin_channel_messages.get(str(channel.id))
                kept = False

                async for old_msg in channel.history(limit=50):
                    if old_msg.author.id != bot.user.id:
                        continue
                    if existing_msg_id and str(old_msg.id) == existing_msg_id:
                        try:
                            await old_msg.edit(embed=checkin_embed, view=PersistentCheckinView())
                            kept = True
                            logger.info(f"更新签到按钮: #{channel.name}")
                        except Exception:
                            pass
                    else:
                        try:
                            await old_msg.delete()
                        except Exception:
                            pass

                if not kept:
                    msg = await channel.send(embed=checkin_embed, view=PersistentCheckinView())
                    checkin_channel_messages[str(channel.id)] = str(msg.id)
                    save_checkin_channels()
                    logger.info(f"发布签到按钮: #{channel.name}")
            except discord.Forbidden:
                logger.warning(f"无权限在 #{channel.name} 发送消息")
            except Exception as e:
                logger.error(f"签到频道 #{channel.name} 设置失败: {e}")


async def _do_checkin(interaction: discord.Interaction):
    """签到核心逻辑，供按钮和 /签到 命令共用"""
    guild_id = str(interaction.guild.id)
    user_id = str(interaction.user.id)
    today = _checkin_today()
    user_data = _get_checkin_record(guild_id, user_id)

    if user_data.get("last_checkin") == today:
        await interaction.response.send_message(
            f"你今天已经签到过了。当前积分: **{user_data['points']}**\n"
            f"连续签到 **{_live_streak(user_data)}** 天，累计 **{user_data['total_days']}** 天。\n明天再来吧。",
            ephemeral=True,
        )
        return

    yesterday = _checkin_yesterday()
    if user_data.get("last_checkin") == yesterday:
        user_data["streak"] = user_data.get("streak", 0) + 1
    else:
        user_data["streak"] = 1
    user_data["total_days"] = user_data.get("total_days", 0) + 1
    if user_data["streak"] > user_data.get("max_streak", 0):
        user_data["max_streak"] = user_data["streak"]

    earned = random.randint(1, 20)
    user_data["points"] = user_data.get("points", 0) + earned
    user_data["last_checkin"] = today
    points_data[guild_id][user_id] = user_data
    save_points()

    await interaction.response.send_message(
        f"签到成功！获得 **{earned}** 积分\n"
        f"当前总积分: **{user_data['points']}**\n"
        f"连续签到: **{user_data['streak']}** 天\n"
        f"累计签到: **{user_data['total_days']}** 天",
        ephemeral=True,
    )

    async def _auto_delete_checkin():
        await asyncio.sleep(30)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
    asyncio.create_task(_auto_delete_checkin())


async def _do_points_query(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    user_id = str(interaction.user.id)
    user_data = _get_checkin_record(guild_id, user_id)
    last = user_data.get("last_checkin") or "尚未签到"
    embed = discord.Embed(
        title="我的积分",
        description=(
            f"当前积分：**{user_data['points']}**\n"
            f"最近一次签到：{last}"
        ),
        color=discord.Color.gold(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _do_checkin_days_query(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    user_id = str(interaction.user.id)
    user_data = _get_checkin_record(guild_id, user_id)
    last = user_data.get("last_checkin") or "尚未签到"
    embed = discord.Embed(
        title="签到天数",
        description=(
            f"连续签到：**{_live_streak(user_data)}** 天\n"
            f"累计签到：**{user_data['total_days']}** 天（断签也计入）\n"
            f"最长连续：**{user_data['max_streak']}** 天\n"
            f"最近一次签到：{last}"
        ),
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="签到", description="每日签到领取随机积分（1~20）")
async def checkin_command(interaction: discord.Interaction):
    await _do_checkin(interaction)


@bot.tree.command(name="积分", description="查看自己的当前积分")
async def points_command(interaction: discord.Interaction):
    await _do_points_query(interaction)


@bot.tree.command(name="签到天数", description="查看连续签到和累计签到天数")
async def checkin_days_command(interaction: discord.Interaction):
    await _do_checkin_days_query(interaction)


# ═══════════════════════════════════════════
#  举报/工单系统
# ═══════════════════════════════════════════

async def get_or_create_report_review_channel(guild: discord.Guild) -> discord.TextChannel:
    """获取或创建举报审核频道（仅最高权限者和 bot 可见）"""
    # 先查找是否已存在
    for channel in guild.text_channels:
        if channel.name == REPORT_REVIEW_CHANNEL_NAME:
            return channel

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
            create_public_threads=True,
            create_private_threads=True,
            manage_threads=True,
        ),
    }
    if guild.owner:
        overwrites[guild.owner] = discord.PermissionOverwrite(read_messages=True)

    try:
        channel = await guild.create_text_channel(
            name=REPORT_REVIEW_CHANNEL_NAME,
            overwrites=overwrites,
            reason="Chen-Abot 举报审核频道",
        )
        logger.info(f"已创建举报审核频道: #{channel.name} in {guild.name}")
        return channel
    except Exception as e:
        logger.error(f"创建举报审核频道失败: {e}")
        raise


async def get_or_create_blacklist_channel(guild: discord.Guild) -> discord.TextChannel:
    for channel in guild.text_channels:
        if BLACKLIST_CHANNEL_KEYWORD in channel.name:
            return channel
    try:
        channel = await guild.create_text_channel(
            name=BLACKLIST_CHANNEL_NAME,
            reason="Chen-Abot 结案公示频道",
        )
        logger.info(f"已创建黑户地带频道: #{channel.name} in {guild.name}")
        return channel
    except Exception as e:
        logger.error(f"创建黑户地带频道失败: {e}")
        raise


def _flush_pending_evidence(rec: dict) -> int:
    pending = rec.get("pending_evidence") or []
    if not pending:
        return 0
    rec.setdefault("evidence", []).extend(pending)
    rec["pending_evidence"] = []
    return len(pending)


# ─── 举报入口：持久化按钮（直接创建子频道）───

class PersistentReportEntryView(discord.ui.View):
    """举报频道持久化按钮视图"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📢 举报违规行为",
        style=discord.ButtonStyle.danger,
        custom_id="persistent_report_entry",
    )
    async def report_entry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        reporter = interaction.user
        guild = interaction.guild
        guild_id = str(guild.id)

        existing = _find_open_report(guild_id, reporter.id)
        if existing:
            status = existing.get("status")
            if status == "draft":
                created = existing.get("created_at") or ""
                expired = False
                try:
                    created_dt = datetime.fromisoformat(created)
                    expired = (datetime.now() - created_dt).total_seconds() > 2 * 3600
                except Exception:
                    expired = True
                if not expired:
                    thread_id = existing.get("thread_id")
                    await interaction.followup.send(
                        f"你已有未完成的举报工单 **{existing.get('ticket_no', '')}**。\n"
                        f"请先到 <#{thread_id}> 填完资料。同一时间只能有一个工单。",
                        ephemeral=True,
                    )
                    return
                existing["status"] = "abandoned"
                save_reports()
            else:
                thread_id = existing.get("thread_id")
                await interaction.followup.send(
                    f"你已有进行中的工单 **{existing.get('ticket_no', '')}**（{_report_status_label(status)}）。\n"
                    f"私人子区：<#{thread_id}>\n"
                    "岛主结案并关闭该工单后，才能再开新单。",
                    ephemeral=True,
                )
                return

        counter = report_counter.get(guild_id, 0) + 1
        report_counter[guild_id] = counter
        save_report_counter()
        ticket_no = f"#{counter:03d}"

        try:
            report_thread = await interaction.channel.create_thread(
                name=f"举报{ticket_no}-{reporter.display_name[:15]}",
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason="举报工单私人子区",
            )
            await report_thread.add_user(reporter)
            if guild.owner and guild.owner.id != reporter.id:
                try:
                    await report_thread.add_user(guild.owner)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"创建举报子频道失败: {e}")
            await interaction.followup.send(
                f"创建私人子区失败: {e}\n请确认服务器已开启私人线程。",
                ephemeral=True,
            )
            return

        report_id = str(report_thread.id)
        report_data[report_id] = {
            "guild_id": guild_id,
            "thread_id": str(report_thread.id),
            "parent_channel_id": str(interaction.channel.id),
            "reporter_id": str(reporter.id),
            "reporter_name": reporter.display_name,
            "target_id": "",
            "target_name": "",
            "category": "",
            "location": "",
            "reason": "",
            "anonymous": False,
            "ticket_no": ticket_no,
            "status": "draft",
            "review_message_id": "",
            "review_channel_id": "",
            "public_message_id": "",
            "public_channel_id": "",
            "evidence": [],
            "pending_evidence": [],
            "verdict": {},
            "followups": [],
            "created_at": datetime.now().isoformat(),
        }
        save_reports()

        thread_embed = discord.Embed(
            title=f"举报工单 {ticket_no}",
            description=(
                "**这是仅你与岛主可见的私人子区。**\n\n"
                "**举报须知：**\n"
                "1. 如实举报。恶意、伪造证据会被反处理。\n"
                "2. 同一时间只能有一个工单；本单结案前不能再开新单。\n"
                "3. 请写清被举报人 ID、名称、违规类型、发生位置和经过。\n"
                "4. 可匿名。你的身份只有岛主看得到。\n"
                "5. 提交后先排队。岛主点「正在审理」后，本子区会出现长期「补充资料」按钮。\n"
                "6. 补充截图请发在本子区，再点「确认上传」，审核频道才会看到最新资料。\n"
                "7. 结案后岛主会填写最终结果，公示到黑户地带；本子区会立刻关闭。\n"
                "8. 若结果有误，岛主可在审核频道「追加审核结果」，公示会跟在原案下面。\n\n"
                "点下方按钮填写举报信息。"
            ),
            color=discord.Color.orange(),
        )
        thread_embed.set_footer(text=f"工单号: {ticket_no} | 仅举报人与岛主可见")

        await report_thread.send(embed=thread_embed, view=ThreadReportStartView())

        await interaction.followup.send(
            f"私人举报子区已创建：{report_thread.mention}\n请在子区里填写举报信息。其他人看不到这个子区。",
            ephemeral=True,
        )


# ─── 子频道内：开始填写表单按钮 ───

class ThreadReportStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="填写举报信息",
        style=discord.ButtonStyle.danger,
        custom_id="report_start_form",
    )
    async def start_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        rid, rec = _find_report_by_thread(str(interaction.channel.id))
        if rec is None:
            await interaction.response.send_message("找不到对应工单。", ephemeral=True)
            return
        if str(interaction.user.id) != str(rec.get("reporter_id")):
            await interaction.response.send_message("只有举报人才能填写。", ephemeral=True)
            return
        if rec.get("status") not in ("draft",):
            await interaction.response.send_message("这份工单已经提交过了。", ephemeral=True)
            return
        await interaction.response.send_modal(
            ReportFormModal(
                interaction.user,
                rec.get("ticket_no", ""),
                rec.get("guild_id", str(interaction.guild.id)),
                interaction.channel.id,
            )
        )


# ─── 举报表单 Modal ───

class ReportFormModal(discord.ui.Modal, title="提交举报"):
    def __init__(self, reporter: discord.Member, ticket_no: str, guild_id: str, thread_id: int):
        super().__init__()
        self.reporter = reporter
        self.ticket_no = ticket_no
        self.guild_id = guild_id
        self.thread_id = thread_id

        self.target_id = discord.ui.TextInput(
            label="被举报人 ID（数字ID）",
            placeholder="对方 Discord 用户 ID，可右键复制",
            style=discord.TextStyle.short,
            required=True,
            min_length=5,
            max_length=30,
        )
        self.add_item(self.target_id)

        self.target_name = discord.ui.TextInput(
            label="被举报人名称",
            placeholder="对方显示名或用户名",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.add_item(self.target_name)

        self.category = discord.ui.TextInput(
            label="违规类型",
            placeholder="买卖/代充/发密钥/伸手/骚扰/转载侵权/其他",
            style=discord.TextStyle.short,
            required=True,
            max_length=50,
        )
        self.add_item(self.category)

        self.location = discord.ui.TextInput(
            label="发生位置",
            placeholder="频道名或消息链接",
            style=discord.TextStyle.short,
            required=True,
            max_length=200,
        )
        self.add_item(self.location)

        self.reason = discord.ui.TextInput(
            label="经过与证据说明",
            placeholder="时间、做了什么、你已有的证据。截图等岛主开始审理后再补。",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=800,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        target_id = self.target_id.value.strip()
        target_name = self.target_name.value.strip()
        category = self.category.value.strip()
        location = self.location.value.strip()
        reason = self.reason.value.strip()

        view = AnonymousChoiceView(
            self.reporter,
            target_id,
            target_name,
            category,
            location,
            reason,
            self.ticket_no,
            self.guild_id,
            self.thread_id,
        )
        await interaction.followup.send(
            "请选择举报方式：",
            view=view,
            ephemeral=True,
        )


# ─── 匿名选择视图 ───

class AnonymousChoiceView(discord.ui.View):
    def __init__(self, reporter: discord.Member, target_id: str, target_name: str, category: str, location: str, reason: str, ticket_no: str, guild_id: str, thread_id: int):
        super().__init__(timeout=120)
        self.reporter = reporter
        self.target_id = target_id
        self.target_name = target_name
        self.category = category
        self.location = location
        self.reason = reason
        self.ticket_no = ticket_no
        self.guild_id = guild_id
        self.thread_id = thread_id

    @discord.ui.button(label="匿名举报", style=discord.ButtonStyle.secondary)
    async def anonymous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.reporter.id:
            await interaction.response.send_message("这不是你的操作。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _create_report(
            interaction, self.reporter, self.target_id, self.target_name, self.category, self.location, self.reason,
            anonymous=True, ticket_no=self.ticket_no, guild_id=self.guild_id, thread_id=self.thread_id,
        )

    @discord.ui.button(label="实名举报", style=discord.ButtonStyle.primary)
    async def named(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.reporter.id:
            await interaction.response.send_message("这不是你的操作。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _create_report(
            interaction, self.reporter, self.target_id, self.target_name, self.category, self.location, self.reason,
            anonymous=False, ticket_no=self.ticket_no, guild_id=self.guild_id, thread_id=self.thread_id,
        )


async def _create_report(
    interaction: discord.Interaction,
    reporter: discord.Member,
    target_id: str,
    target_name: str,
    category: str,
    location: str,
    reason: str,
    anonymous: bool,
    ticket_no: str = "",
    guild_id: str = "",
    thread_id: int = 0,
):
    guild = interaction.guild
    guild_id = guild_id or str(guild.id)
    report_id = str(thread_id)

    report_thread = guild.get_thread(thread_id) if thread_id else None
    if not report_thread:
        await interaction.followup.send("子区已丢失，请重新举报。", ephemeral=True)
        return

    rec = report_data.get(report_id)
    if rec is None:
        rec = {"thread_id": str(thread_id), "created_at": datetime.now().isoformat()}
        report_data[report_id] = rec

    rec.update({
        "guild_id": guild_id,
        "thread_id": str(report_thread.id),
        "parent_channel_id": rec.get("parent_channel_id") or str(getattr(interaction.channel, "parent_id", interaction.channel.id)),
        "reporter_id": str(reporter.id),
        "reporter_name": reporter.display_name,
        "target_id": target_id,
        "target_name": target_name,
        "category": category,
        "location": location,
        "reason": reason,
        "anonymous": anonymous,
        "ticket_no": ticket_no,
        "status": "pending",
        "evidence": rec.get("evidence") or [],
        "pending_evidence": rec.get("pending_evidence") or [],
        "verdict": rec.get("verdict") or {},
        "followups": rec.get("followups") or [],
        "public_message_id": rec.get("public_message_id") or "",
        "public_channel_id": rec.get("public_channel_id") or "",
    })

    reporter_label = "匿名用户" if anonymous else f"{reporter.mention} ({reporter.display_name})"
    thread_embed = discord.Embed(
        title=f"举报工单 {ticket_no}",
        description=(
            f"**举报人:** {reporter_label}\n"
            f"**被举报人ID:** `{target_id}`\n"
            f"**被举报人名称:** {target_name}\n"
            f"**违规类型:** {category}\n"
            f"**发生位置:** {location}\n"
            f"**经过:** {reason}\n\n"
            "已提交，正在排队。岛主点「正在审理」后，本子区会出现「补充资料」按钮。\n"
            "那时再发截图，并点确认上传，审核频道才会看到。"
        ),
        color=discord.Color.orange(),
        timestamp=datetime.now(),
    )
    thread_embed.set_footer(text=f"工单号: {ticket_no} | 状态: 待审理")
    await report_thread.send(embed=thread_embed)

    try:
        review_channel = await get_or_create_report_review_channel(guild)
        view = PersistentReportReviewView()
        review_msg = await review_channel.send(embed=_build_review_embed(rec), view=view)
        rec["review_message_id"] = str(review_msg.id)
        rec["review_channel_id"] = str(review_channel.id)
        save_reports()
        await interaction.followup.send(
            f"举报已提交。工单号: **{ticket_no}**\n"
            f"请等岛主开始审理。开始后才能在 {report_thread.mention} 补充截图。",
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"创建审核工单失败: {e}")
        save_reports()
        await interaction.followup.send(
            f"私人子区 {report_thread.mention} 已创建，但审核工单创建失败: {e}",
            ephemeral=True,
        )


def _format_evidence_block(rec: dict) -> str:
    evidence_list = rec.get("evidence") or []
    pending = rec.get("pending_evidence") or []
    lines = []
    if evidence_list:
        lines.append("**已确认补充资料**")
        for i, ev in enumerate(evidence_list, 1):
            if ev.get("type") == "image":
                lines.append(f"{i}. [图片] {ev.get('content', '')}")
            else:
                lines.append(f"{i}. {str(ev.get('content', ''))[:300]}")
    else:
        lines.append("**已确认补充资料**")
        lines.append("暂无")
    if rec.get("status") == "reviewing" and pending:
        lines.append("")
        lines.append(f"**待确认上传:** {len(pending)} 条（举报人尚未点确认）")
    return "\n".join(lines)


def _build_review_embed(rec: dict) -> discord.Embed:
    ticket_no = rec.get("ticket_no", "???")
    status = rec.get("status", "pending")
    anonymous = rec.get("anonymous", False)
    reporter_name = rec.get("reporter_name", "未知")
    reporter_id = rec.get("reporter_id", "?")
    target_id = rec.get("target_id", "?")
    target_name = rec.get("target_name", "?")
    category = rec.get("category") or "未填"
    location = rec.get("location") or "未填"
    reason = rec.get("reason") or "?"
    thread_id = rec.get("thread_id", "0")
    color_map = {
        "draft": discord.Color.light_grey(),
        "pending": discord.Color.orange(),
        "reviewing": discord.Color.blue(),
        "completed": discord.Color.dark_grey(),
    }
    body = (
        f"**被举报人ID:** `{target_id}`\n"
        f"**被举报人名称:** {target_name}\n"
        f"**违规类型:** {category}\n"
        f"**发生位置:** {location}\n"
        f"**经过:** {reason}\n\n"
        f"{_format_evidence_block(rec)}"
    )
    verdict = rec.get("verdict") or {}
    if verdict:
        body += (
            f"\n\n**结案结果:** {verdict.get('conclusion', '')}\n"
            f"**处理对象:** {verdict.get('punished', '')}\n"
            f"**处罚:** {verdict.get('penalty', '')}\n"
            f"**说明:** {verdict.get('note', '')}"
        )
    followups = rec.get("followups") or []
    if followups:
        body += f"\n\n已追加审核 **{len(followups)}** 次，详见黑户地带原案下方。"
    embed = discord.Embed(
        title=f"工单 {ticket_no}",
        description=body[:4096],
        color=color_map.get(status, discord.Color.orange()),
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="举报人",
        value=f"{'匿名用户' if anonymous else reporter_name} (ID: {reporter_id})",
        inline=True,
    )
    embed.add_field(name="状态", value=_report_status_label(status), inline=True)
    if status == "completed":
        embed.add_field(name="子区", value="已关闭", inline=True)
    else:
        embed.add_field(name="子区", value=f"<#{thread_id}>", inline=True)
    embed.set_footer(text=f"工单号: {ticket_no}")
    return embed


async def _update_review_card(report_id: str, guild: discord.Guild, view: discord.ui.View = None):
    rec = report_data.get(report_id)
    if not rec:
        return
    try:
        review_channel_id = rec.get("review_channel_id")
        review_msg_id = rec.get("review_message_id")
        if not review_channel_id or not review_msg_id:
            return
        review_channel = guild.get_channel(int(review_channel_id))
        if not review_channel:
            return
        review_msg = await review_channel.fetch_message(int(review_msg_id))
        kwargs = {"embed": _build_review_embed(rec)}
        if view is not None:
            kwargs["view"] = view
        await review_msg.edit(**kwargs)
    except Exception as e:
        logger.error(f"更新审核卡片失败: {e}")


def _build_public_verdict_embed(rec: dict) -> discord.Embed:
    ticket_no = rec.get("ticket_no", "???")
    verdict = rec.get("verdict") or {}
    conclusion = verdict.get("conclusion") or "未填写"
    embed = discord.Embed(
        title=f"工单 {ticket_no} 审理结果",
        description=(
            f"**结论:** {conclusion}\n"
            f"**被处理人:** {verdict.get('punished') or rec.get('target_name') or '未填写'}\n"
            f"**被处理人ID:** `{verdict.get('punished_id') or rec.get('target_id') or '未填写'}`\n"
            f"**处罚:** {verdict.get('penalty') or '未填写'}\n"
            f"**说明:** {verdict.get('note') or '无'}\n"
            f"**违规类型:** {rec.get('category') or '未填'}"
        ),
        color=discord.Color.dark_red() if "成立" in conclusion and "不成立" not in conclusion else discord.Color.dark_grey(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"工单号: {ticket_no} | 结案公示")
    return embed


def _build_followup_embed(rec: dict, followup: dict) -> discord.Embed:
    ticket_no = rec.get("ticket_no", "???")
    embed = discord.Embed(
        title=f"工单 {ticket_no} 追加审核",
        description=(
            f"**追加结论:** {followup.get('conclusion') or '未填写'}\n"
            f"**被处理人:** {followup.get('punished') or '未填写'}\n"
            f"**被处理人ID:** `{followup.get('punished_id') or '未填写'}`\n"
            f"**处罚/更正:** {followup.get('penalty') or '未填写'}\n"
            f"**说明:** {followup.get('note') or '无'}"
        ),
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"工单号: {ticket_no} | 追加在原案下方")
    return embed


async def _close_report_thread(guild: discord.Guild, rec: dict):
    thread_id = rec.get("thread_id")
    if not thread_id:
        return
    thread = guild.get_thread(int(thread_id))
    if thread is None:
        try:
            thread = await guild.fetch_channel(int(thread_id))
        except Exception:
            return
    try:
        await thread.delete()
        logger.info(f"举报子区已关闭: {getattr(thread, 'name', thread_id)}")
    except Exception as e:
        logger.error(f"关闭举报子区失败: {e}")


async def _publish_verdict(guild: discord.Guild, rec: dict):
    channel = await get_or_create_blacklist_channel(guild)
    msg = await channel.send(embed=_build_public_verdict_embed(rec))
    rec["public_message_id"] = str(msg.id)
    rec["public_channel_id"] = str(channel.id)
    save_reports()
    return msg


async def _publish_followup(guild: discord.Guild, rec: dict, followup: dict):
    channel_id = rec.get("public_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if channel is None:
        channel = await get_or_create_blacklist_channel(guild)
    ref = None
    if rec.get("public_message_id"):
        try:
            ref = await channel.fetch_message(int(rec["public_message_id"]))
        except Exception:
            ref = None
    kwargs = {"embed": _build_followup_embed(rec, followup)}
    if ref:
        kwargs["reference"] = ref
    await channel.send(**kwargs)


# ─── 审核工单按钮视图 ───

class PersistentReportReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _resolve(self, interaction: discord.Interaction):
        return _find_report_by_review_message(str(interaction.message.id))

    @discord.ui.button(
        label="正在审理",
        style=discord.ButtonStyle.primary,
        custom_id="report_reviewing",
    )
    async def reviewing(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("只有岛主才能审理举报。", ephemeral=True)
            return
        report_id, rec = self._resolve(interaction)
        if rec is None:
            await interaction.response.send_message("工单数据丢失。", ephemeral=True)
            return
        if rec.get("status") == "completed":
            await interaction.response.send_message("此工单已结案。如需更正，请用「追加审核结果」。", ephemeral=True)
            return
        if rec.get("status") == "reviewing":
            await interaction.response.send_message("已经在审理中。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        rec["status"] = "reviewing"
        save_reports()

        guild = interaction.guild
        thread = guild.get_thread(int(rec.get("thread_id") or 0))
        if thread:
            try:
                notify_embed = discord.Embed(
                    title="岛主正在审理你的举报",
                    description=(
                        "本工单已进入审理中。现在可以补充截图和说明。\n"
                        "请把资料发在这个子区，再点下方「确认上传」。\n"
                        "确认后，审核频道会立刻看到最新资料。\n"
                        "结案后这个子区会关闭，按钮也会消失。"
                    ),
                    color=discord.Color.blue(),
                )
                await thread.send(embed=notify_embed, view=PersistentEvidenceView())
            except Exception as e:
                logger.error(f"通知举报人失败: {e}")

        await _update_review_card(report_id, guild)
        await interaction.followup.send("已标为审理中。举报人子区已出现长期「补充资料」按钮。", ephemeral=True)

    @discord.ui.button(
        label="审理完毕",
        style=discord.ButtonStyle.success,
        custom_id="report_completed",
    )
    async def completed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("只有岛主才能审理举报。", ephemeral=True)
            return
        report_id, rec = self._resolve(interaction)
        if rec is None:
            await interaction.response.send_message("工单数据丢失。", ephemeral=True)
            return
        if rec.get("status") == "completed":
            await interaction.response.send_message("此工单已结案。如需更正，请用「追加审核结果」。", ephemeral=True)
            return
        await interaction.response.send_modal(VerdictModal(report_id, is_followup=False))

    @discord.ui.button(
        label="追加审核结果",
        style=discord.ButtonStyle.secondary,
        custom_id="report_followup",
    )
    async def followup_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("只有岛主才能追加审核。", ephemeral=True)
            return
        report_id, rec = self._resolve(interaction)
        if rec is None:
            await interaction.response.send_message("工单数据丢失。", ephemeral=True)
            return
        if rec.get("status") != "completed":
            await interaction.response.send_message("先结案，才能追加审核结果。", ephemeral=True)
            return
        await interaction.response.send_modal(VerdictModal(report_id, is_followup=True))


class PersistentEvidenceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="确认上传补充资料",
        style=discord.ButtonStyle.primary,
        custom_id="report_confirm_evidence",
    )
    async def confirm_evidence(self, interaction: discord.Interaction, button: discord.ui.Button):
        report_id, rec = _find_report_by_thread(str(interaction.channel.id))
        if rec is None:
            await interaction.response.send_message("找不到对应工单。", ephemeral=True)
            return
        if str(interaction.user.id) != str(rec.get("reporter_id")):
            await interaction.response.send_message("只有本单举报人可以确认上传。", ephemeral=True)
            return
        if rec.get("status") != "reviewing":
            await interaction.response.send_message("只有审理中才能补充资料。", ephemeral=True)
            return
        count = _flush_pending_evidence(rec)
        if count == 0:
            await interaction.response.send_message(
                "没有待确认资料。请先在本子区发截图或文字，再点这个按钮。",
                ephemeral=True,
            )
            return
        save_reports()
        await _update_review_card(report_id, interaction.guild)
        await interaction.response.send_message(
            f"已确认上传 {count} 条资料，审核频道已更新。",
            ephemeral=True,
        )
        try:
            await interaction.channel.send(f"举报人已确认上传 {count} 条补充资料。")
        except Exception:
            pass


class VerdictModal(discord.ui.Modal):
    def __init__(self, report_id: str, is_followup: bool = False):
        super().__init__(title="追加审核结果" if is_followup else "填写结案结果")
        self.report_id = report_id
        self.is_followup = is_followup
        rec = report_data.get(report_id) or {}
        default_name = rec.get("target_name") or ""
        default_id = rec.get("target_id") or ""

        self.conclusion = discord.ui.TextInput(
            label="结论",
            placeholder="成立 / 不成立 / 部分成立",
            style=discord.TextStyle.short,
            required=True,
            max_length=50,
        )
        self.add_item(self.conclusion)

        self.punished = discord.ui.TextInput(
            label="被处理人名称",
            placeholder="公示里显示的处理对象",
            default=default_name,
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.add_item(self.punished)

        self.punished_id = discord.ui.TextInput(
            label="被处理人 ID",
            placeholder="Discord 用户 ID",
            default=default_id,
            style=discord.TextStyle.short,
            required=True,
            max_length=30,
        )
        self.add_item(self.punished_id)

        self.penalty = discord.ui.TextInput(
            label="处罚或更正",
            placeholder="警告 / 禁言 / 封禁 / 无处罚 / 撤销原处罚",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.add_item(self.penalty)

        self.note = discord.ui.TextInput(
            label="说明（可附图片链接）",
            placeholder="审理依据。有图可先发到审核频道，再把链接贴这里。",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=800,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rec = report_data.get(self.report_id)
        if rec is None:
            await interaction.followup.send("工单数据丢失。", ephemeral=True)
            return
        payload = {
            "conclusion": self.conclusion.value.strip(),
            "punished": self.punished.value.strip(),
            "punished_id": self.punished_id.value.strip(),
            "penalty": self.penalty.value.strip(),
            "note": self.note.value.strip(),
            "by": str(interaction.user.id),
            "at": datetime.now().isoformat(),
        }
        guild = interaction.guild
        if self.is_followup:
            rec.setdefault("followups", []).append(payload)
            save_reports()
            try:
                await _publish_followup(guild, rec, payload)
            except Exception as e:
                logger.error(f"追加公示失败: {e}")
                await interaction.followup.send(f"追加结果已记下，但公示失败: {e}", ephemeral=True)
                return
            await _update_review_card(self.report_id, guild, view=PersistentReportReviewView())
            await interaction.followup.send("追加审核结果已发到黑户地带，跟在原案下面。", ephemeral=True)
            return

        rec["verdict"] = payload
        rec["status"] = "completed"
        save_reports()
        try:
            await _publish_verdict(guild, rec)
        except Exception as e:
            logger.error(f"结案公示失败: {e}")
            await interaction.followup.send(f"结果已记下，但黑户地带公示失败: {e}", ephemeral=True)
            return

        thread = guild.get_thread(int(rec.get("thread_id") or 0))
        if thread:
            try:
                done_embed = discord.Embed(
                    title="你的举报已结案",
                    description="岛主已填写最终结果，并公示到黑户地带。本子区即将关闭。",
                    color=discord.Color.green(),
                )
                await thread.send(embed=done_embed)
            except Exception:
                pass
        await _close_report_thread(guild, rec)
        await _update_review_card(self.report_id, guild, view=PersistentReportReviewView())
        await interaction.followup.send("已结案，结果已发到黑户地带，私人子区已关闭。", ephemeral=True)


async def setup_report_channels():
    """在名称包含 REPORT_CHANNEL_KEYWORD 的频道中发布举报按钮消息"""
    report_embed = discord.Embed(
        title="🚨 报告！有间谍！",
        description=(
            "发现违规行为？点下方按钮开私人工单。\n\n"
            "**规则：**\n"
            "同一时间只能有一个未结案工单。\n"
            "子区仅你和岛主可见。恶意举报会被反处理。\n\n"
            "**流程：**\n"
            "1. 开私人子区，填被举报人、类型、位置、经过\n"
            "2. 岛主点「正在审理」后，子区出现「补充资料」\n"
            "3. 发截图后点确认上传，审核频道才会看到\n"
            "4. 结案必填结果，公示到黑户地带，子区立刻关闭\n"
            "5. 结果有误可追加审核，公示跟在原案下面"
        ),
        color=discord.Color.red(),
    )
    report_embed.set_footer(text="恶意举报将受到处罚，请如实举报")

    for guild in bot.guilds:
        for channel in guild.text_channels:
            if REPORT_CHANNEL_KEYWORD not in channel.name:
                continue

            try:
                existing_msg_id = report_channel_messages.get(str(channel.id))
                kept = False

                async for old_msg in channel.history(limit=50):
                    if old_msg.author.id != bot.user.id:
                        continue
                    if existing_msg_id and str(old_msg.id) == existing_msg_id:
                        try:
                            await old_msg.edit(embed=report_embed, view=PersistentReportEntryView())
                            kept = True
                            logger.info(f"更新举报按钮: #{channel.name}")
                        except Exception:
                            pass
                    else:
                        try:
                            await old_msg.delete()
                        except Exception:
                            pass

                if not kept:
                    msg = await channel.send(embed=report_embed, view=PersistentReportEntryView())
                    report_channel_messages[str(channel.id)] = str(msg.id)
                    save_report_channels()
                    logger.info(f"发布举报按钮: #{channel.name}")
            except discord.Forbidden:
                logger.warning(f"无权限在 #{channel.name} 发送消息")
            except Exception as e:
                logger.error(f"举报频道 #{channel.name} 设置失败: {e}")


async def setup_guide_channels():
    """在名称包含 GUIDE_CHANNEL_KEYWORD 的频道中发布频道导航"""
    # 排除的频道名称
    EXCLUDED_NAMES = {"📁-文件存储", "举报审核", "测试", "黑户地带"}

    for guild in bot.guilds:
        # 收集所有频道（文字、语音、论坛、舞台），按分类分组
        categories = {}
        no_category = []

        all_channels = list(guild.text_channels) + list(guild.voice_channels)
        # 添加论坛频道
        try:
            all_channels += list(guild.forum_channels)
        except AttributeError:
            pass
        # 添加舞台频道
        try:
            all_channels += list(guild.stage_channels)
        except AttributeError:
            pass
        # 添加所有类型的频道（公告/新闻等可能不在 text_channels 中）
        for ch in guild.channels:
            if ch not in all_channels and not isinstance(ch, discord.CategoryChannel):
                all_channels.append(ch)

        for channel in all_channels:
            if channel.category:
                cat_name = channel.category.name
                if cat_name not in categories:
                    categories[cat_name] = []
                categories[cat_name].append(channel)
            else:
                no_category.append(channel)

        for channel in guild.text_channels:
            if GUIDE_CHANNEL_KEYWORD not in channel.name:
                continue

            # 构建导航（排除指路自身、文件存储、举报审核）
            lines = []
            for cat_name, chs in categories.items():
                filtered = [ch for ch in chs
                            if ch.id != channel.id
                            and ch.name not in EXCLUDED_NAMES
                            and GUIDE_CHANNEL_KEYWORD not in ch.name]
                if not filtered:
                    continue
                lines.append(f"**📁 {cat_name}**")
                for ch in filtered:
                    lines.append(f"　└ {ch.mention}")
                lines.append("")

            filtered_no_cat = [ch for ch in no_category
                               if ch.id != channel.id
                               and ch.name not in EXCLUDED_NAMES
                               and GUIDE_CHANNEL_KEYWORD not in ch.name]
            if filtered_no_cat:
                lines.append("**📁 未分类**")
                for ch in filtered_no_cat:
                    lines.append(f"　└ {ch.mention}")

            if not lines:
                continue

            guide_embed = discord.Embed(
                title="🗺️ 小岛指路牌",
                description="\n".join(lines)[:4096],
                color=discord.Color.teal(),
            )
            guide_embed.set_footer(text="点击频道名即可跳转 | 自动更新")

            try:
                existing_msg_id = guide_channel_messages.get(str(channel.id))
                kept = False

                async for old_msg in channel.history(limit=50):
                    if old_msg.author.id != bot.user.id:
                        continue
                    if existing_msg_id and str(old_msg.id) == existing_msg_id:
                        try:
                            await old_msg.edit(embed=guide_embed)
                            kept = True
                            logger.info(f"更新指路导航: #{channel.name}")
                        except Exception:
                            pass
                    else:
                        try:
                            await old_msg.delete()
                        except Exception:
                            pass

                if not kept:
                    msg = await channel.send(embed=guide_embed)
                    guide_channel_messages[str(channel.id)] = str(msg.id)
                    save_guide_channels()
                    logger.info(f"发布指路导航: #{channel.name}")
            except discord.Forbidden:
                logger.warning(f"无权限在 #{channel.name} 发送消息")
            except Exception as e:
                logger.error(f"指路频道 #{channel.name} 设置失败: {e}")


# ═══════════════════════════════════════════
#  /清理测试数据 - 仅岛主可用，清理所有举报工单
# ═══════════════════════════════════════════

@bot.tree.command(name="清理测试数据", description="删除所有举报工单子频道、清空审核频道、重置计数器（仅岛主可用）")
async def cleanup_reports(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("只有岛主才能使用此命令。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    deleted_threads = 0
    cleared_messages = 0

    # 1. 删除所有举报子频道
    for rid, rec in list(report_data.items()):
        if rec.get("guild_id") != str(guild.id):
            continue
        thread_id = rec.get("thread_id")
        if thread_id:
            thread = guild.get_thread(int(thread_id))
            if thread:
                try:
                    await thread.delete()
                    deleted_threads += 1
                except Exception as e:
                    logger.warning(f"删除子频道失败: {e}")

    # 2. 清空审核频道消息
    for channel in guild.text_channels:
        if channel.name == REPORT_REVIEW_CHANNEL_NAME:
            try:
                async for msg in channel.history(limit=100):
                    if msg.author.id == bot.user.id:
                        try:
                            await msg.delete()
                            cleared_messages += 1
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"清空审核频道失败: {e}")

    # 3. 重置数据
    report_data.clear()
    save_reports()
    report_counter.pop(str(guild.id), None)
    save_report_counter()

    await interaction.followup.send(
        f"✅ 清理完成！\n"
        f"已删除 {deleted_threads} 个举报子频道\n"
        f"已清空审核频道 {cleared_messages} 条消息\n"
        f"工单计数器已重置，下次从 #001 开始",
        ephemeral=True,
    )


# ═══════════════════════════════════════════
#  /查询记录 - 仅岛主可用，查看频道文件下载记录
# ═══════════════════════════════════════════

@bot.tree.command(name="查询记录", description="查看当前频道所有文件的下载记录（仅岛主可用）")
async def query_logs(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("只有岛主才能使用此命令。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    channel_id = interaction.channel.id
    # 筛选当前频道的下载记录
    channel_logs = [log for log in download_logs if log.get("channel_id") == channel_id]

    if not channel_logs:
        await interaction.followup.send("📭 当前频道还没有文件下载记录。", ephemeral=True)
        return

    # 按文件分组
    file_groups = {}
    for log in channel_logs:
        fid = log.get("file_id", "?")
        if fid not in file_groups:
            file_groups[fid] = {
                "file_name": log.get("file_name", "?"),
                "uploader_id": log.get("uploader_id", "?"),
                "uploader_name": log.get("uploader_name", "?"),
                "downloads": [],
            }
        file_groups[fid]["downloads"].append(log)

    # 生成报告文本
    report_lines = [f"📋 下载记录报告 - <#{channel_id}>", "=" * 40, ""]
    total_downloads = 0

    for fid, group in file_groups.items():
        total = len(group["downloads"])
        total_downloads += total
        report_lines.append(f"📁 {group['file_name']}")
        report_lines.append(f"   上传者: {group['uploader_name']} (ID: {group['uploader_id']})")
        report_lines.append(f"   总下载次数: {total}")
        report_lines.append("")
        for dl in group["downloads"]:
            ts = dl.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                time_str = ts[:16]
            report_lines.append(f"   └ {time_str} | {dl['downloader_name']} (ID: {dl['downloader_id']})")
        report_lines.append("")

    report_lines.append("=" * 40)
    report_lines.append(f"📊 频道总下载: {total_downloads} 次")
    report_lines.append(f"📁 文件数: {len(file_groups)} 个")
    report_text = "\n".join(report_lines)

    # 生成报告图片
    try:
        from PIL import Image, ImageDraw, ImageFont
        # 计算图片尺寸
        line_height = 22
        max_width = 0
        img_lines = report_text.split("\n")
        # 粗略估算宽度
        for line in img_lines:
            w = len(line) * 10  # 每个字符约10px宽
            if w > max_width:
                max_width = w
        img_width = max(800, min(max_width + 60, 1200))
        img_height = len(img_lines) * line_height + 40

        img = Image.new("RGB", (img_width, img_height), color=(30, 30, 30))
        draw = ImageDraw.Draw(img)

        # 尝试加载中文字体
        font = None
        font_paths = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for fp in font_paths:
            try:
                font = ImageFont.truetype(fp, 16)
                break
            except Exception:
                continue

        if font is None:
            font = ImageFont.load_default()

        y = 10
        for line in img_lines:
            draw.text((10, y), line, fill=(220, 220, 220), font=font)
            y += line_height

        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        img_file = discord.File(fp=img_bytes, filename="download_report.png")

        view = ReportCopyView(report_text)
        await interaction.followup.send(
            content=f"📋 **下载记录报告** — <#{channel_id}>\n\n"
                    f"📊 总下载 {total_downloads} 次 | 📁 {len(file_groups)} 个文件\n\n"
                    f"下方为报告图片，点击按钮可复制纯文本报告",
            file=img_file,
            view=view,
            ephemeral=True,
        )
    except ImportError:
        # 没有 Pillow，只发送文本报告
        view = ReportCopyView(report_text)
        await interaction.followup.send(
            content=f"📋 **下载记录报告** — <#{channel_id}>\n\n```{report_text[:1900]}```",
            view=view,
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"生成报告图片失败: {e}")
        view = ReportCopyView(report_text)
        await interaction.followup.send(
            content=f"📋 **下载记录报告** — <#{channel_id}>\n\n```{report_text[:1900]}```",
            view=view,
            ephemeral=True,
        )


class ReportCopyView(discord.ui.View):
    """复制报告按钮视图"""

    def __init__(self, report_text: str):
        super().__init__(timeout=300)
        self.report_text = report_text

    @discord.ui.button(label="📋 复制报告", style=discord.ButtonStyle.primary)
    async def copy_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 发送一个可复制的文本消息
        await interaction.response.send_message(
            f"```\n{self.report_text[:1900]}\n```",
            ephemeral=True,
        )


# ═══════════════════════════════════════════
#  错误处理
# ═══════════════════════════════════════════

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("你没有权限执行此操作！", ephemeral=True)
        return
    logger.error(f"斜杠命令错误: {error}")
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("执行命令时出错，请稍后再试。", ephemeral=True)
    except:
        pass


# ═══════════════════════════════════════════
#  HTTP 服务器（平台健康检查用，可选）
# ═══════════════════════════════════════════

_runner = None


async def start_http_server():
    """启动轻量 HTTP 服务器用于平台健康检查"""
    global _runner
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    
    async def health_check(request):
        return web.Response(text="OK")
    
    app.router.add_get("/", health_check)
    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 HTTP 健康检查已启动，端口: {port}")


# ═══════════════════════════════════════════
#  启动 Bot（含自动重启）
# ═══════════════════════════════════════════

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("❌ 未设置 DISCORD_BOT_TOKEN 环境变量！")
        exit(1)

    token = token.strip()
    logger.info(f"🔑 Token 长度: {len(token)} 字符，开头: {token[:10]}...")

    _http_started = False

    async def shutdown():
        """优雅关闭：清理 HTTP runner 和 bot 连接"""
        global _http_started
        logger.info("🛑 正在关闭...")
        if _runner:
            await _runner.cleanup()
            _http_started = False
        if not bot.is_closed():
            await bot.close()
        logger.info("✅ 已关闭")

    async def heartbeat():
        """定期心跳日志，用于追踪 bot 在线状态"""
        while True:
            await asyncio.sleep(3600)  # 每小时一次
            if bot.is_ready():
                guild_count = len(bot.guilds)
                logger.info(f"💓 心跳 — 在线，服务 {guild_count} 个服务器")

    async def main():
        global _http_started
        if not _http_started:
            await start_http_server()
            _http_started = True

        # 注册持久化视图（必须在 bot.start() 之前）
        bot.add_view(PersistentQuizView())
        bot.add_view(PersistentCheckinView())
        bot.add_view(PersistentReportEntryView())
        bot.add_view(ThreadReportStartView())
        bot.add_view(PersistentReportReviewView())
        bot.add_view(PersistentEvidenceView())
        bot.add_view(PublishedFileView())
        bot.add_view(PersistentStorageCardView())

        # 启动心跳任务
        bot.heartbeat_task = asyncio.create_task(heartbeat())

        logger.info("🚀 正在启动 Chen-Abot...")
        try:
            await bot.start(token)
        except discord.LoginFailure as e:
            logger.error(f"❌ Token 无效: {e}")
            return False  # Token 无效，不再重试
        except Exception as e:
            logger.error(f"❌ Bot 异常断开: {e}")
            return True  # 可重试的错误
        return True  # 正常退出也允许重试

    async def run_with_retry():
        """带自动重启的启动循环，Token 无效时退出"""
        retry_count = 0
        max_backoff = 300  # 最大退避 5 分钟

        while True:
            try:
                should_retry = await main()
            except Exception as e:
                logger.error(f"❌ 未捕获的异常: {e}")
                should_retry = True

            if not should_retry:
                # Token 无效，不重试
                break

            await shutdown()

            # 指数退避
            retry_count += 1
            delay = min(2 ** retry_count, max_backoff)
            logger.info(f"🔄 {delay} 秒后自动重启（第 {retry_count} 次）...")
            await asyncio.sleep(delay)

    try:
        asyncio.run(run_with_retry())
    except KeyboardInterrupt:
        logger.info("👋 收到中断信号，退出")
    finally:
        # 确保清理
        if not bot.is_closed():
            asyncio.run(bot.close())