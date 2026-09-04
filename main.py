import os
import io
import json
import random
import asyncio
import zipfile
import discord
from discord.ext import commands
from discord import app_commands
from aiohttp import web
import logging
from datetime import datetime, timedelta
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
GUIDE_CHANNEL_KEYWORD = "指路"           # 指路频道关键词
GUIDE_CHANNEL_FILE = "guide_channels.json"
DOWNLOAD_LOGS_FILE = "download_logs.json"
PUBLISHED_FILE = "channel_published.json"

# ─── 文件记录存储 ───
# 结构: { "file_id": { "name": str, "uploader_id": int, "status": "draft|published", "description": str,
#                      "attachments": [{ "original_name": str, "custom_name": str, "storage_msg_id": str, "size": int }],
#                      "conditions": { ... }, "published_msg_id": str, "source_channel_id": int, "guild_id": int, "upload_time": str } }
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
# 积分: { "guild_id": { "user_id": {"points": int, "last_checkin": "YYYY-MM-DD"} } }
points_data: dict = {}
# 签到频道消息: { "channel_id": "message_id" }
checkin_channel_messages: dict = {}
CHECKIN_CHANNEL_KEYWORD = "签到"  # 签到频道名称关键词

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

# ─── 举报系统 ───
# 结构: { "report_id": { "guild_id": str, "thread_id": str, "parent_channel_id": str, "reporter_id": str, "reporter_name": str, "target_id": str, "target_name": str, "reason": str, "anonymous": bool, "status": "pending|reviewing|completed", "review_message_id": str, "created_at": str } }
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
                    "如果按钮无法使用，请使用 `/答题` 命令。",
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
#  /回顶 - 回到楼主（第1楼）
# ═══════════════════════════════════════════

@bot.tree.command(name="回顶", description="回到楼主（第1楼）")
async def back_to_top(interaction: discord.Interaction):
    """发送该频道第一条消息的跳转链接"""
    async for first_msg in interaction.channel.history(oldest_first=True, limit=1):
        jump_url = first_msg.jump_url
        embed = discord.Embed(
            title="🔝 回顶",
            description=f"[点击跳转到楼主（第1楼）]({jump_url})",
            color=discord.Color.blue(),
        )
        embed.add_field(name="楼主", value=first_msg.author.mention, inline=True)
        embed.add_field(
            name="发布时间",
            value=discord.utils.format_dt(first_msg.created_at, "R"),
            inline=True,
        )
        await interaction.response.send_message(embed=embed)
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


# ─── 阶段1: 上传文件命令（支持多附件）───

@bot.tree.command(name="上传文件", description="上传文件到当前频道（支持多附件）")
@app_commands.describe(
    文件1="要上传的文件",
    文件2="（可选）第二个文件",
    文件3="（可选）第三个文件",
    文件4="（可选）第四个文件",
    文件5="（可选）第五个文件",
)
async def upload_file(
    interaction: discord.Interaction,
    文件1: discord.Attachment,
    文件2: discord.Attachment = None,
    文件3: discord.Attachment = None,
    文件4: discord.Attachment = None,
    文件5: discord.Attachment = None,
):
    await interaction.response.defer(ephemeral=True)

    # 收集所有附件
    attachments = [文件1]
    for a in [文件2, 文件3, 文件4, 文件5]:
        if a is not None:
            attachments.append(a)

    # 上传所有文件到存储频道
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
                content=f"📁 {att.filename} | 上传者: {interaction.user.display_name} (ID: {interaction.user.id})",
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
            await interaction.followup.send(f"❌ 上传 {att.filename} 失败: {e}", ephemeral=True)
            return

    # 生成草稿 ID（使用第一个附件存储消息的 ID）
    draft_id = attachment_records[0]["storage_msg_id"]

    # 默认文件名：取第一个附件名
    default_name = attachments[0].filename

    # 继承频道已有条件
    channel_files = {
        fid: rec for fid, rec in file_records.items()
        if str(rec.get("source_channel_id")) == str(interaction.channel.id)
        and rec.get("status") == "published"
    }
    old_conditions = None
    has_previous = bool(channel_files)
    if has_previous:
        old_conditions = next(iter(channel_files.values())).get("conditions")

    conditions = old_conditions if old_conditions else {
        "password": None,
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
        "description": "",
        "status": "draft",
        "published_msg_id": None,
        "attachments": attachment_records,
        "upload_time": datetime.now().isoformat(),
    }
    save_records()

    # 显示阶段1完成 → 进入阶段2（设置条件）
    await _show_draft_setup(interaction, draft_id, has_previous)


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
        content=f"📦 {zip_name} | 整合ZIP | 上传者: {interaction.user.display_name}",
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
    save_records()

    await interaction.followup.send(
        f"✅ 已将 {len(attachments)} 个附件整合为 **{zip_name}** ({_format_size(total_zip_size)})",
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
        await interaction.response.send_message(
            f"✅ 文件标题已修改为：**{record['name']}**",
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
        await interaction.response.send_message(
            f"✅ 已更新 {len(changed)} 个附件名称",
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

        await interaction.followup.send(
            f"✅ 文件 **{record['name']}** 已发布到频道！",
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"发布文件失败: {e}")
        await interaction.followup.send(f"❌ 发布失败: {e}", ephemeral=True)


# ─── 公开卡片构建 ───

def _build_published_card(record: dict, file_id: str):
    """构建公开的发布卡片（embed + 带下拉选择器的 view）"""
    cond_desc = _build_condition_description(record["conditions"])
    desc = record.get("description", "") or "（无说明）"
    attach_count = len(record["attachments"])

    embed = discord.Embed(
        title=f"📁 {record['name']}",
        description=f"{desc[:1000]}\n\n"
                    f"**附件数:** {attach_count} 个\n"
                    f"**总大小:** {_format_size(record['size'])}\n"
                    f"**获取条件:** {cond_desc}",
        color=discord.Color.purple(),
        timestamp=datetime.fromisoformat(record["upload_time"]) if record.get("upload_time") else datetime.now(),
    )
    embed.set_footer(text=f"上传者: {record.get('uploader_name', '未知')} | 选择文件后点击下载")

    view = PublishedFileView(file_id, record)
    return embed, view


class PublishedFileView(discord.ui.View):
    """公开卡片：文件下拉选择 + 密码输入 + 下载按钮（持久化）"""

    def __init__(self, file_id: str = "", record: dict = None):
        super().__init__(timeout=None)  # 持久化
        self._file_id = file_id
        self._record = record
        self._password = None  # 用户输入的密码（仅当前会话有效）

        # 构建选项（在 build 时设置）
        options = []
        if record:
            for i, att in enumerate(record.get("attachments", [])):
                options.append(discord.SelectOption(
                    label=f"📎 {att['custom_name'][:80]}",
                    description=f"{_format_size(att['size'])}",
                    value=f"file_{i}",
                ))

        self.select_menu = discord.ui.Select(
            placeholder=f"选择要下载的文件（可多选，共 {len(options)} 项）" if options else "选择文件",
            options=options if options else [discord.SelectOption(label="—", value="none")],
            custom_id="pub_file_select",
            row=0,
            min_values=1,
            max_values=max(len(options), 1) if options else 1,
        )
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

        self.password_btn = discord.ui.Button(
            label="🔒 输入密码",
            style=discord.ButtonStyle.secondary,
            custom_id="pub_file_password",
            row=1,
        )
        self.password_btn.callback = self.on_password_btn
        self.add_item(self.password_btn)

        self.download_btn = discord.ui.Button(
            label="⬇️ 下载",
            style=discord.ButtonStyle.primary,
            custom_id="pub_file_download",
            row=1,
        )
        self.download_btn.callback = self.on_download
        self.add_item(self.download_btn)

        self.selected_values = ["file_0"]

    async def on_select(self, interaction: discord.Interaction):
        self.selected_values = self.select_menu.values
        await interaction.response.defer()

    async def on_password_btn(self, interaction: discord.Interaction):
        """打开密码输入框"""
        await interaction.response.send_modal(PublicPasswordModal(self))

    async def on_download(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # 从频道发布记录中查找 file_id
        channel_id = str(interaction.channel.id)
        pub_info = channel_published.get(channel_id)
        if not pub_info:
            await interaction.followup.send("❌ 发布记录已过期。", ephemeral=True)
            return

        record = file_records.get(pub_info["file_id"])
        if not record:
            await interaction.followup.send("❌ 文件记录已丢失。", ephemeral=True)
            return

        # 检查条件 — 对所有人（含上传者）强制执行
        conditions = record.get("conditions", {})
        failed_reasons = []

        if conditions.get("password"):
            if not self._password:
                failed_reasons.append("需要输入密码（点击 🔒输入密码 按钮）")
            elif self._password != conditions["password"]:
                failed_reasons.append("密码错误")
        if conditions.get("require_like_first"):
            if not await _check_user_liked_first(interaction):
                failed_reasons.append("需要给首楼点赞")
        if conditions.get("require_comment_first"):
            min_len = conditions.get("min_comment_length", 1)
            if not await _check_user_comment_length(interaction, min_len):
                failed_reasons.append(f"需要评论首楼至少 {min_len} 字")

        if failed_reasons:
            await interaction.followup.send(
                "❌ " + "，".join(failed_reasons),
                ephemeral=True,
            )
            return

        # 根据选择发送多个文件
        attachments = record.get("attachments", [])
        success_count = 0
        for value in self.selected_values:
            if value.startswith("file_"):
                idx = int(value.split("_")[1])
                if idx < len(attachments):
                    await _send_attachment_to_user(interaction, record, idx)
                    success_count += 1

        if success_count > 0:
            await interaction.followup.send(f"✅ 已发送 {success_count} 个文件", ephemeral=True)
        else:
            await interaction.followup.send("❌ 未选择有效文件或发送失败", ephemeral=True)


class PublicPasswordModal(discord.ui.Modal, title="输入下载密码"):
    """公开卡片上的密码输入框"""

    def __init__(self, view: PublishedFileView):
        super().__init__()
        self._view = view
        self.pwd = discord.ui.TextInput(
            label="请输入下载密码",
            placeholder="输入上传者设置的密码",
            style=discord.TextStyle.short,
            required=True,
            max_length=50,
        )
        self.add_item(self.pwd)

    async def on_submit(self, interaction: discord.Interaction):
        self._view._password = self.pwd.value.strip()
        await interaction.response.send_message("✅ 密码已记录，现在可以点击下载了", ephemeral=True)


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
    """记录下载日志"""
    download_logs.append({
        "file_id": record.get("published_msg_id", "?"),
        "file_name": record.get("name", "?"),
        "file_label": file_label,
        "downloader_id": interaction.user.id,
        "downloader_name": interaction.user.display_name,
        "channel_id": interaction.channel.id,
        "uploader_id": record.get("uploader_id", "?"),
        "uploader_name": record.get("uploader_name", "?"),
        "timestamp": datetime.now().isoformat(),
    })
    save_download_logs()


# ═══════════════════════════════════════════
#  /获取文件 - 查看当前频道已发布文件列表
# ═══════════════════════════════════════════

class GetFileView(discord.ui.View):
    """文件列表视图，每个已发布文件一个获取按钮"""

    def __init__(self, channel_files: dict, interaction_user_id: int, password: Optional[str]):
        super().__init__(timeout=300)
        self.channel_files = channel_files
        self.interaction_user_id = interaction_user_id
        self.password = password

        sorted_files = sorted(
            channel_files.items(),
            key=lambda x: x[1].get("upload_time", ""),
            reverse=True,
        )

        for i, (file_id, rec) in enumerate(sorted_files[:25]):
            btn = discord.ui.Button(
                label=f"获取 {rec['name'][:60]}",
                style=discord.ButtonStyle.primary,
                custom_id=file_id,
                row=i // 5,
            )
            btn.callback = self.make_callback(file_id)
            self.add_item(btn)

    def make_callback(self, file_id: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            if interaction.user.id != self.interaction_user_id:
                await interaction.followup.send("这不是你的操作。", ephemeral=True)
                return

            record = self.channel_files.get(file_id)
            if not record:
                await interaction.followup.send("文件记录已丢失。", ephemeral=True)
                return

            conditions = record.get("conditions", {})
            failed_reasons = []

            if conditions.get("password"):
                if not self.password:
                    failed_reasons.append("需要输入密码（使用 /获取文件 密码:xxx）")
                elif self.password != conditions["password"]:
                    failed_reasons.append("密码错误")

            if conditions.get("require_like_first"):
                if not await _check_user_liked_first(interaction):
                    failed_reasons.append("需要给首楼点赞")

            if conditions.get("require_comment_first"):
                min_len = conditions.get("min_comment_length", 1)
                if not await _check_user_comment_length(interaction, min_len):
                    failed_reasons.append(f"需要评论首楼至少 {min_len} 字")

            if failed_reasons:
                await interaction.followup.send(
                    "❌ " + "，".join(failed_reasons),
                    ephemeral=True,
                )
                return

            # 发送第一个附件（兼容旧行为：点击获取按钮默认发送第一个文件）
            attachments = record.get("attachments", [])
            if attachments:
                await _send_attachment_to_user(interaction, record, 0)
            else:
                await interaction.followup.send("❌ 没有可用的附件。", ephemeral=True)

        return callback


@bot.tree.command(name="获取文件", description="查看当前频道已发布文件列表并获取")
@app_commands.describe(
    密码="如果上传者设置了密码，请在此输入",
)
async def get_file(
    interaction: discord.Interaction,
    密码: Optional[str] = None,
):
    """显示频道内已发布文件，可点击按钮获取"""
    await interaction.response.defer(ephemeral=True)

    # 非上传者只能看到已发布文件；上传者可以看到自己所有文件（含草稿）
    channel_files = {
        fid: rec for fid, rec in file_records.items()
        if str(rec.get("source_channel_id")) == str(interaction.channel.id)
        and (rec.get("status") == "published" or rec.get("uploader_id") == interaction.user.id)
    }

    if not channel_files:
        await interaction.followup.send("📭 当前频道还没有已发布文件。", ephemeral=True)
        return

    sorted_files = sorted(
        channel_files.items(),
        key=lambda x: x[1].get("upload_time", ""),
        reverse=True,
    )

    embed = discord.Embed(
        title=f"📋 文件列表（共 {len(sorted_files)} 个）",
        color=discord.Color.blue(),
    )

    for file_id, rec in sorted_files:
        cond_desc = _build_condition_description(rec.get("conditions", {}))
        raw_time = rec.get("upload_time", "")
        try:
            dt = datetime.fromisoformat(raw_time)
            upload_time = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            upload_time = raw_time[:16] if len(raw_time) >= 16 else "未知"
        is_uploader = interaction.user.id == rec.get("uploader_id")
        status_label = rec.get("status", "published")
        draft_tag = " [草稿]" if status_label == "draft" else ""

        attach_count = len(rec.get("attachments", []))
        attach_info = f"附件: {attach_count} 个" if attach_count > 0 else ""

        # 条件检查
        met_parts = []
        unmet_parts = []
        conditions = rec.get("conditions", {})

        if is_uploader:
            met_parts.append("✅ 上传者（无条件）")
        else:
            if conditions.get("password"):
                if 密码 and 密码 == conditions["password"]:
                    met_parts.append("✅ 密码正确")
                else:
                    unmet_parts.append("❌ 需要密码")
            if conditions.get("require_like_first"):
                liked = await _check_user_liked_first(interaction)
                if liked:
                    met_parts.append("✅ 已点赞")
                else:
                    unmet_parts.append("❌ 未点赞")
            if conditions.get("require_comment_first"):
                min_len = conditions.get("min_comment_length", 1)
                met = await _check_user_comment_length(interaction, min_len)
                if met:
                    met_parts.append(f"✅ 已评论（≥{min_len}字）")
                else:
                    unmet_parts.append(f"❌ 评论不足{min_len}字")

            if not conditions.get("password") and not conditions.get("require_like_first") and not conditions.get("require_comment_first"):
                met_parts.append("✅ 无条件")

        status = "\n".join(met_parts + unmet_parts) if (met_parts or unmet_parts) else "✅ 无条件"

        embed.add_field(
            name=f"📁 {rec.get('name', '?')}{draft_tag}",
            value=f"上传者: <@{rec.get('uploader_id', '?')}>\n"
                  f"大小: {_format_size(rec.get('size', 0))}\n"
                  f"{attach_info}\n"
                  f"时间: {upload_time}\n"
                  f"{status}",
            inline=False,
        )

    embed.set_footer(text="点击下方按钮获取文件（默认获取第一个附件）")
    view = GetFileView(channel_files, interaction.user.id, 密码)
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
                f"\n\n⏳ 第 {cooldown['fail_count']} 次失败，需要等待 **{cooldown_minutes} 分钟** 后才能重新答题"
            )
        else:
            description += "\n\n你可以立即重新答题"
        description += "\n\n💪 别灰心，下次一定能过！"

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    msg = await interaction.response.edit_message(embed=embed, view=None)

    # 结果消息一定时间后自动删除（仅答题者可见，超时消失）
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
    cooldown = quiz_cooldowns.get(str(user_id))
    if cooldown and cooldown.get("cooldown_until"):
        try:
            until = datetime.fromisoformat(cooldown["cooldown_until"])
            if until > datetime.now():
                remaining = until - datetime.now()
                minutes = int(remaining.total_seconds() // 60) + 1
                await interaction.response.send_message(
                    f"⏳ 你还需要等待约 **{minutes} 分钟** 才能重新答题。",
                    ephemeral=True,
                )
                return
        except ValueError:
            pass

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

    # 检查是否是举报线程中的消息（报告人补充证据）
    if isinstance(message.channel, discord.Thread):
        thread_id = str(message.channel.id)
        # 查找对应的举报记录
        for rid, rec in report_data.items():
            if rec.get("thread_id") == thread_id and rec.get("status") != "completed":
                # 补充证据
                evidence_entry = {
                    "type": "text",
                    "content": message.content[:500] if message.content else "",
                    "author": message.author.display_name,
                    "time": datetime.now().isoformat(),
                }
                # 如果有图片附件
                if message.attachments:
                    for att in message.attachments:
                        img_entry = {
                            "type": "image",
                            "content": att.url,
                            "author": message.author.display_name,
                            "time": datetime.now().isoformat(),
                        }
                        rec.setdefault("evidence", []).append(img_entry)
                if message.content.strip():
                    rec.setdefault("evidence", []).append(evidence_entry)
                save_reports()
                # 更新审核卡片
                await _update_review_card(rid, message.guild)
                break

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


async def setup_checkin_channels():
    """在名称包含 CHECKIN_CHANNEL_KEYWORD 的频道中发布签到按钮消息"""
    checkin_embed = discord.Embed(
        title="🏝️ 小岛每日签到",
        description="每天签到可获得 **1~20 随机积分**！\n\n"
                    "点击下方按钮签到，或使用 `/签到` 命令。",
        color=discord.Color.green(),
    )
    checkin_embed.set_footer(text="每天只能签到一次，UTC+8 零点刷新")

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
    today = datetime.now().strftime("%Y-%m-%d")

    # 初始化 guild 数据
    if guild_id not in points_data:
        points_data[guild_id] = {}

    user_data = points_data[guild_id].get(user_id, {"points": 0, "last_checkin": ""})

    # 检查今天是否已签到
    if user_data.get("last_checkin") == today:
        await interaction.response.send_message(
            f"⏳ 你今天已经签到过了！当前积分: **{user_data['points']}**\n明天再来吧～",
            ephemeral=True,
        )
        return

    # 随机积分
    earned = random.randint(1, 20)
    user_data["points"] = user_data.get("points", 0) + earned
    user_data["last_checkin"] = today
    points_data[guild_id][user_id] = user_data
    save_points()

    await interaction.response.send_message(
        f"✅ 签到成功！获得 **{earned}** 积分 🎉\n"
        f"当前总积分: **{user_data['points']}**",
        ephemeral=True,
    )

    # 30秒后自动删除签到消息
    async def _auto_delete_checkin():
        await asyncio.sleep(30)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
    asyncio.create_task(_auto_delete_checkin())


@bot.tree.command(name="签到", description="每日签到领取随机积分（1~20）")
async def checkin_command(interaction: discord.Interaction):
    await _do_checkin(interaction)


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

        # 生成工单号
        guild_id = str(guild.id)
        counter = report_counter.get(guild_id, 0) + 1
        report_counter[guild_id] = counter
        save_report_counter()
        ticket_no = f"#{counter:03d}"

        try:
            # 直接创建子频道（线程）
            report_thread = await interaction.channel.create_thread(
                name=f"举报{ticket_no}-{reporter.display_name[:15]}",
                type=discord.ChannelType.private_thread if isinstance(interaction.channel, discord.TextChannel) else discord.ChannelType.public_thread,
                reason="举报工单子频道",
            )
            await report_thread.add_user(reporter)
        except Exception as e:
            logger.error(f"创建举报子频道失败: {e}")
            await interaction.followup.send(
                f"❌ 创建举报子频道失败: {e}\n请确认服务器已开启线程功能。",
                ephemeral=True,
            )
            return

        # 在子频道中发送欢迎消息 + 填写表单按钮
        thread_embed = discord.Embed(
            title=f"📋 举报工单 {ticket_no}",
            description=(
                "**举报须知：**\n"
                "1. 请如实举报，恶意举报将被处罚\n"
                "2. 举报内容需包含被举报人ID、违规简述\n"
                "3. 可匿名举报，举报人信息仅岛主可见\n"
                "4. 填写完表单后可在本频道补充图片和详细说明\n"
                "5. 岛主审理完毕后会通知你\n\n"
                "点击下方按钮开始填写举报信息 ⬇️"
            ),
            color=discord.Color.orange(),
        )
        thread_embed.set_footer(text=f"工单号: {ticket_no}")

        view = ThreadReportStartView(reporter, ticket_no, guild_id, report_thread.id)
        await report_thread.send(embed=thread_embed, view=view)

        await interaction.followup.send(
            f"✅ 举报子频道已创建：{report_thread.mention}\n请在子频道中填写举报信息。",
            ephemeral=True,
        )


# ─── 子频道内：开始填写表单按钮 ───

class ThreadReportStartView(discord.ui.View):
    """子频道中开始填写举报信息的按钮"""

    def __init__(self, reporter: discord.Member, ticket_no: str, guild_id: str, thread_id: int):
        super().__init__(timeout=600)
        self.reporter = reporter
        self.ticket_no = ticket_no
        self.guild_id = guild_id
        self.thread_id = thread_id

    @discord.ui.button(label="📝 填写举报信息", style=discord.ButtonStyle.danger)
    async def start_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.reporter.id:
            await interaction.response.send_message("只有举报人才能填写。", ephemeral=True)
            return
        await interaction.response.send_modal(
            ReportFormModal(self.reporter, self.ticket_no, self.guild_id, self.thread_id)
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
            placeholder="请输入被举报人的 Discord 用户 ID",
            style=discord.TextStyle.short,
            required=True,
            min_length=5,
            max_length=30,
        )
        self.add_item(self.target_id)

        self.target_name = discord.ui.TextInput(
            label="被举报人名称",
            placeholder="请输入被举报人的显示名称",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.add_item(self.target_name)

        self.reason = discord.ui.TextInput(
            label="违规简述",
            placeholder="简要描述违规行为",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        target_id = self.target_id.value.strip()
        target_name = self.target_name.value.strip()
        reason = self.reason.value.strip()

        # 询问是否匿名
        view = AnonymousChoiceView(
            self.reporter,
            target_id,
            target_name,
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
    def __init__(self, reporter: discord.Member, target_id: str, target_name: str, reason: str, ticket_no: str, guild_id: str, thread_id: int):
        super().__init__(timeout=120)
        self.reporter = reporter
        self.target_id = target_id
        self.target_name = target_name
        self.reason = reason
        self.ticket_no = ticket_no
        self.guild_id = guild_id
        self.thread_id = thread_id

    @discord.ui.button(label="🔒 匿名举报", style=discord.ButtonStyle.secondary)
    async def anonymous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.reporter.id:
            await interaction.response.send_message("这不是你的操作。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _create_report(
            interaction, self.reporter, self.target_id, self.target_name, self.reason,
            anonymous=True, ticket_no=self.ticket_no, guild_id=self.guild_id, thread_id=self.thread_id,
        )

    @discord.ui.button(label="👤 实名举报", style=discord.ButtonStyle.primary)
    async def named(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.reporter.id:
            await interaction.response.send_message("这不是你的操作。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _create_report(
            interaction, self.reporter, self.target_id, self.target_name, self.reason,
            anonymous=False, ticket_no=self.ticket_no, guild_id=self.guild_id, thread_id=self.thread_id,
        )


async def _create_report(
    interaction: discord.Interaction,
    reporter: discord.Member,
    target_id: str,
    target_name: str,
    reason: str,
    anonymous: bool,
    ticket_no: str = "",
    guild_id: str = "",
    thread_id: int = 0,
):
    """创建举报工单：使用已有子频道，创建审核工单卡片"""
    guild = interaction.guild
    guild_id = guild_id or str(guild.id)
    report_id = str(interaction.id)

    # 获取已有的子频道
    report_thread = guild.get_thread(thread_id) if thread_id else None
    if not report_thread:
        await interaction.followup.send("❌ 子频道已丢失，请重新举报。", ephemeral=True)
        return

    # 在子频道中发送汇总信息
    reporter_label = "匿名用户" if anonymous else f"{reporter.mention} ({reporter.display_name})"
    thread_embed = discord.Embed(
        title=f"📋 举报工单 {ticket_no}",
        description=(
            f"**举报人:** {reporter_label}\n"
            f"**被举报人ID:** {target_id}\n"
            f"**被举报人名称:** {target_name}\n"
            f"**违规简述:** {reason}\n\n"
            "📎 **请在下方补充图片、链接等更多证据**\n"
            "岛主审理完毕后会通知你。"
        ),
        color=discord.Color.orange(),
        timestamp=datetime.now(),
    )
    thread_embed.set_footer(text=f"工单号: {ticket_no}")
    await report_thread.send(embed=thread_embed)

    # 构建审核工单卡片
    evidence_text = "📝 **举报表单**\n"
    evidence_text += f"> 被举报人ID: {target_id}\n"
    evidence_text += f"> 被举报人名称: {target_name}\n"
    evidence_text += f"> 违规简述: {reason}\n"
    evidence_text += "\n📎 **补充证据**\n_（举报人添加的内容将自动显示在此处）_\n"

    review_embed = discord.Embed(
        title=f"📋 工单 {ticket_no}",
        description=evidence_text[:4096],
        color=discord.Color.orange(),
        timestamp=datetime.now(),
    )
    review_embed.add_field(name="举报人", value=f"{'匿名用户' if anonymous else reporter.display_name} (ID: {reporter.id})", inline=True)
    review_embed.add_field(name="状态", value="⏳ 待审理", inline=True)
    review_embed.add_field(name="子频道", value=report_thread.mention, inline=True)
    review_embed.set_footer(text=f"工单号: {ticket_no} | 点击按钮审理")

    try:
        review_channel = await get_or_create_report_review_channel(guild)
        view = PersistentReportReviewView(report_id, report_thread.id, ticket_no)
        review_msg = await review_channel.send(embed=review_embed, view=view)

        report_data[report_id] = {
            "guild_id": guild_id,
            "thread_id": str(report_thread.id),
            "parent_channel_id": str(interaction.channel.id),
            "reporter_id": str(reporter.id),
            "reporter_name": reporter.display_name,
            "target_id": target_id,
            "target_name": target_name,
            "reason": reason,
            "anonymous": anonymous,
            "ticket_no": ticket_no,
            "status": "pending",
            "review_message_id": str(review_msg.id),
            "review_channel_id": str(review_channel.id),
            "evidence": [],
            "created_at": datetime.now().isoformat(),
        }
        save_reports()

        await interaction.followup.send(
            f"✅ 举报已提交！工单号: **{ticket_no}**\n"
            f"你可以在子频道 {report_thread.mention} 中补充更多证据。",
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"创建审核工单失败: {e}")
        await interaction.followup.send(
            f"举报子频道 {report_thread.mention} 已创建，但审核工单创建失败: {e}",
            ephemeral=True,
        )


async def _update_review_card(report_id: str, guild: discord.Guild):
    """根据最新证据更新审核工单卡片"""
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
    except Exception:
        return

    ticket_no = rec.get("ticket_no", "???")
    anonymous = rec.get("anonymous", False)
    reporter_name = rec.get("reporter_name", "未知")
    reporter_id = rec.get("reporter_id", "?")
    target_id = rec.get("target_id", "?")
    target_name = rec.get("target_name", "?")
    reason = rec.get("reason", "?")
    thread_id = rec.get("thread_id", "0")
    status = rec.get("status", "pending")

    status_label = {"pending": "⏳ 待审理", "reviewing": "🔍 审理中", "completed": "✅ 已处理"}.get(status, status)

    # 构建证据文本
    evidence_text = "📝 **举报表单**\n"
    evidence_text += f"> 被举报人ID: {target_id}\n"
    evidence_text += f"> 被举报人名称: {target_name}\n"
    evidence_text += f"> 违规简述: {reason}\n"

    evidence_list = rec.get("evidence", [])
    if evidence_list:
        evidence_text += "\n📎 **补充证据**\n"
        for i, ev in enumerate(evidence_list, 1):
            if ev["type"] == "text":
                evidence_text += f"> {i}. {ev['content'][:300]}\n"
            elif ev["type"] == "image":
                evidence_text += f"> {i}. [图片证据] {ev['content']}\n"
    else:
        evidence_text += "\n📎 **补充证据**\n_（暂无补充证据）_\n"

    review_embed = review_msg.embeds[0] if review_msg.embeds else discord.Embed()
    review_embed.title = f"📋 工单 {ticket_no}"
    review_embed.description = evidence_text[:4096]
    review_embed.clear_fields()
    review_embed.add_field(name="举报人", value=f"{'匿名用户' if anonymous else reporter_name} (ID: {reporter_id})", inline=True)
    review_embed.add_field(name="状态", value=status_label, inline=True)
    review_embed.add_field(name="子频道", value=f"<#{thread_id}>", inline=True)
    review_embed.set_footer(text=f"工单号: {ticket_no} | 点击按钮审理")

    color_map = {"pending": discord.Color.orange(), "reviewing": discord.Color.blue(), "completed": discord.Color.green()}
    review_embed.color = color_map.get(status, discord.Color.orange())

    try:
        await review_msg.edit(embed=review_embed)
    except Exception as e:
        logger.error(f"更新审核卡片失败: {e}")


# ─── 审核工单按钮视图 ───

class PersistentReportReviewView(discord.ui.View):
    """审核工单持久化按钮视图"""

    def __init__(self, report_id: str, thread_id: int, ticket_no: str = ""):
        super().__init__(timeout=None)
        self.report_id = report_id
        self.thread_id = thread_id
        self.ticket_no = ticket_no

    @discord.ui.button(
        label="🔍 正在审理",
        style=discord.ButtonStyle.primary,
        custom_id="report_reviewing",
    )
    async def reviewing(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 只有最高权限者能操作
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("只有岛主才能审理举报。", ephemeral=True)
            return

        rec = report_data.get(self.report_id)
        if not rec:
            await interaction.response.send_message("工单数据丢失。", ephemeral=True)
            return

        rec["status"] = "reviewing"
        save_reports()

        # 通知举报人子频道
        guild = interaction.guild
        thread = guild.get_thread(self.thread_id)
        if thread:
            try:
                notify_embed = discord.Embed(
                    title="🔍 岛主正在审理你的举报",
                    description="感谢你的举报，岛主已开始审理此工单，请耐心等待。",
                    color=discord.Color.blue(),
                )
                await thread.send(embed=notify_embed)
            except Exception:
                pass

        await _update_review_card(self.report_id, guild)
        await interaction.response.send_message("✅ 已标记为审理中，举报人已收到通知。", ephemeral=True)

    @discord.ui.button(
        label="✅ 审理完毕",
        style=discord.ButtonStyle.success,
        custom_id="report_completed",
    )
    async def completed(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 只有最高权限者能操作
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("只有岛主才能审理举报。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        rec = report_data.get(self.report_id)
        if not rec:
            await interaction.followup.send("工单数据丢失。", ephemeral=True)
            return

        rec["status"] = "completed"
        save_reports()

        # 通知举报人子频道
        guild = interaction.guild
        thread = guild.get_thread(self.thread_id)
        if thread:
            try:
                done_embed = discord.Embed(
                    title="✅ 你的举报已处理完毕",
                    description="岛主已审理完毕此工单，感谢你的举报。\n\n此子频道将在 10 秒后自动删除。",
                    color=discord.Color.green(),
                )
                await thread.send(embed=done_embed)

                # 10秒后删除子频道
                async def _delete_thread():
                    await asyncio.sleep(10)
                    try:
                        await thread.delete()
                        logger.info(f"举报子频道已删除: {thread.name}")
                    except Exception as e:
                        logger.error(f"删除举报子频道失败: {e}")

                asyncio.create_task(_delete_thread())
            except Exception:
                pass

        # 更新审核卡片
        await _update_review_card(self.report_id, guild)

        # 禁用按钮
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        await interaction.followup.send("✅ 工单已处理完毕，举报子频道将被删除。", ephemeral=True)


async def setup_report_channels():
    """在名称包含 REPORT_CHANNEL_KEYWORD 的频道中发布举报按钮消息"""
    report_embed = discord.Embed(
        title="🚨 报告！有间谍！",
        description=(
            "发现违规行为？点击下方按钮提交举报工单。\n\n"
            "举报流程：\n"
            "1. 点击按钮阅读举报规则\n"
            "2. 等待 10 秒后开始填写举报信息\n"
            "3. 在专属子频道中补充证据\n"
            "4. 岛主审理完毕后通知你"
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
    EXCLUDED_NAMES = {"📁-文件存储", "举报审核", "测试"}

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
        bot.add_view(PersistentReportReviewView("", 0))
        bot.add_view(PublishedFileView())

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