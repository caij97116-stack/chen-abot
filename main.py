import os
import io
import json
import random
import asyncio
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

# ─── 文件记录存储 ───
# 结构: { "file_id": { "name": str, "uploader_id": int, ..., "storage_msg_id": str, "conditions": { ... } } }
file_records: dict = {}

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

# ─── 答题系统 ───
QUIZ_QUESTIONS_PER_ROUND = 5       # 每次答题出几道题
QUIZ_MAX_ERRORS = 2                # 最多允许错几题（超过则失败）
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
    logger.info(f"✅ Bot 已上线: {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"📡 正在服务 {len(bot.guilds)} 个服务器")
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔧 已同步 {len(synced)} 个斜杠命令")
    except Exception as e:
        logger.error(f"命令同步失败: {e}")

    # 在答题频道中发布/更新答题按钮消息
    await setup_quiz_channels()


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
        description="点击下方按钮开始答题，需要 **全部答对** 才能通过审核。\n\n"
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
#  /上传文件 - 上传文件，上传后弹出条件设置
# ═══════════════════════════════════════════

class ConditionsModal(discord.ui.Modal, title="设置获取条件"):
    """上传后弹出的条件设置窗口"""

    def __init__(self, file_id: str):
        super().__init__()
        self.file_id = file_id
        self.password_input = discord.ui.TextInput(
            label="密码（留空则不设密码）",
            placeholder="输入密码，或留空跳过",
            style=discord.TextStyle.short,
            required=False,
            max_length=50,
        )
        self.add_item(self.password_input)

    async def on_submit(self, interaction: discord.Interaction):
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return

        if record["uploader_id"] != interaction.user.id:
            await interaction.response.send_message("只有上传者才能设置条件。", ephemeral=True)
            return

        password = self.password_input.value.strip() or None
        new_conditions = {
            "password": password,
            "require_like_first": False,
            "require_comment_first": False,
            "min_comment_length": 0,
        }
        record["conditions"] = new_conditions
        save_records()

        cond_desc = _build_condition_description(new_conditions)
        await interaction.response.send_message(
            f"✅ 条件已设置：{cond_desc}",
            ephemeral=True,
        )


class ConditionView(discord.ui.View):
    """上传确认消息上的按钮"""

    def __init__(self, file_id: str, uploader_id: int):
        super().__init__(timeout=300)
        self.file_id = file_id
        self.uploader_id = uploader_id

    @discord.ui.button(label="🔒 设置获取条件", style=discord.ButtonStyle.primary)
    async def set_conditions_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uploader_id:
            await interaction.response.send_message("只有上传者才能设置条件。", ephemeral=True)
            return
        await interaction.response.send_modal(ConditionsModal(self.file_id))

    @discord.ui.button(label="👍 需要点赞", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_like(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uploader_id:
            await interaction.response.send_message("只有上传者才能设置条件。", ephemeral=True)
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

    @discord.ui.button(label="💬 需要评论", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_comment(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uploader_id:
            await interaction.response.send_message("只有上传者才能设置条件。", ephemeral=True)
            return
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        record["conditions"]["require_comment_first"] = not record["conditions"]["require_comment_first"]
        record["conditions"]["min_comment_length"] = 1 if record["conditions"]["require_comment_first"] else 0
        save_records()
        await interaction.response.send_message(
            f"✅ 评论要求已{'开启（至少1字）' if record['conditions']['require_comment_first'] else '关闭'}",
            ephemeral=True,
        )

    @discord.ui.button(label="🔢 评论字数", style=discord.ButtonStyle.secondary, row=1)
    async def set_comment_count(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uploader_id:
            await interaction.response.send_message("只有上传者才能设置条件。", ephemeral=True)
            return
        await interaction.response.send_modal(CommentLengthModal(self.file_id))

    async def on_timeout(self):
        self.disable_all_items()
        if self.message:
            await self.message.edit(view=self)


class CommentLengthModal(discord.ui.Modal, title="设置评论字数"):
    def __init__(self, file_id: str):
        super().__init__()
        self.file_id = file_id
        self.count_input = discord.ui.TextInput(
            label="评论最少需要多少字？",
            placeholder="输入数字，如 15",
            style=discord.TextStyle.short,
            required=True,
            min_length=1,
            max_length=3,
        )
        self.add_item(self.count_input)

    async def on_submit(self, interaction: discord.Interaction):
        record = file_records.get(self.file_id)
        if not record:
            await interaction.response.send_message("文件记录已丢失。", ephemeral=True)
            return
        try:
            count = int(self.count_input.value)
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


@bot.tree.command(name="上传文件", description="上传文件到当前频道")
@app_commands.describe(
    文件="要上传的文件（png/json/zip/txt/mp4等）",
)
async def upload_file(
    interaction: discord.Interaction,
    文件: discord.Attachment,
):
    await interaction.response.defer(ephemeral=True)

    try:
        # 读取文件内容
        file_bytes = await 文件.read()

        # 获取存储频道
        storage_channel = await get_or_create_storage_channel(interaction.guild)

        # 将文件发送到存储频道
        discord_file = discord.File(
            fp=io.BytesIO(file_bytes),
            filename=文件.filename,
        )
        storage_msg = await storage_channel.send(
            content=f"📁 {文件.filename} | 上传者: {interaction.user.display_name} (ID: {interaction.user.id})",
            file=discord_file,
        )

        file_id = str(storage_msg.id)
    except Exception as e:
        logger.error(f"上传文件失败: {e}")
        await interaction.followup.send(f"❌ 上传失败: {e}", ephemeral=True)
        return

    # 查找当前频道已有的文件，继承条件
    channel_files = {
        fid: rec for fid, rec in file_records.items()
        if str(rec.get("source_channel_id")) == str(interaction.channel.id)
    }

    old_conditions = None
    if channel_files:
        old_conditions = next(iter(channel_files.values()))["conditions"]

    conditions = old_conditions if old_conditions else {
        "password": None,
        "require_like_first": False,
        "require_comment_first": False,
        "min_comment_length": 0,
    }

    file_records[file_id] = {
        "name": 文件.filename,
        "uploader_id": interaction.user.id,
        "uploader_name": interaction.user.display_name,
        "source_channel_id": interaction.channel.id,
        "guild_id": interaction.guild.id,
        "storage_msg_id": str(storage_msg.id),
        "size": 文件.size,
        "conditions": conditions,
        "upload_time": datetime.now().isoformat(),
    }
    save_records()

    cond_desc = _build_condition_description(conditions)
    embed = discord.Embed(
        title="✅ 文件上传成功",
        description=f"**文件名:** {文件.filename}\n"
                    f"**大小:** {_format_size(文件.size)}\n\n"
                    f"获取条件: {cond_desc}\n\n"
                    f"点击下方按钮修改条件 ⬇️",
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"上传者: {interaction.user.display_name}")

    view = ConditionView(file_id, interaction.user.id)
    view.message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def _build_condition_description(conditions: dict) -> str:
    parts = []
    if conditions["password"]:
        parts.append("需要密码")
    if conditions["require_like_first"]:
        parts.append("需要点赞首楼")
    if conditions["require_comment_first"]:
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


# ═══════════════════════════════════════════
#  /获取文件 - 查看文件列表并获取
# ═══════════════════════════════════════════

class GetFileView(discord.ui.View):
    """文件列表视图，每个文件一个获取按钮"""

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

        for i, (file_id, rec) in enumerate(sorted_files[:25]):  # Discord 最多 25 个按钮
            btn = discord.ui.Button(
                label=f"获取 {rec['name'][:60]}",
                style=discord.ButtonStyle.primary,
                custom_id=file_id,
                row=i // 5,  # 每行最多 5 个按钮
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

            conditions = record["conditions"]
            is_uploader = interaction.user.id == record["uploader_id"]
            failed_reasons = []

            if not is_uploader:
                if conditions["password"]:
                    if not self.password:
                        failed_reasons.append("需要输入密码")
                    elif self.password != conditions["password"]:
                        failed_reasons.append("密码错误")

                if conditions["require_like_first"]:
                    if not await _check_user_liked_first(interaction):
                        failed_reasons.append("需要给首楼点赞")

                if conditions["require_comment_first"]:
                    min_len = conditions.get("min_comment_length", 1)
                    if not await _check_user_comment_length(interaction, min_len):
                        failed_reasons.append(f"需要评论首楼至少 {min_len} 字")

            if failed_reasons:
                await interaction.followup.send(
                    "❌ " + "，".join(failed_reasons),
                    ephemeral=True,
                )
                return

            await _send_file_to_user(interaction, record, file_id)

        return callback


@bot.tree.command(name="获取文件", description="查看当前频道文件列表并获取")
@app_commands.describe(
    密码="如果上传者设置了密码，请在此输入",
)
async def get_file(
    interaction: discord.Interaction,
    密码: Optional[str] = None,
):
    """显示频道内所有文件，可点击按钮获取"""
    await interaction.response.defer(ephemeral=True)

    channel_files = {
        fid: rec for fid, rec in file_records.items()
        if str(rec.get("source_channel_id")) == str(interaction.channel.id)
    }

    if not channel_files:
        await interaction.followup.send("📭 当前频道还没有上传过文件。", ephemeral=True)
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
        cond_desc = _build_condition_description(rec["conditions"])
        upload_time = rec.get("upload_time", "未知")[:10]
        is_uploader = interaction.user.id == rec["uploader_id"]

        # 检查用户已满足的条件
        met_parts = []
        unmet_parts = []
        conditions = rec["conditions"]

        if is_uploader:
            met_parts.append("✅ 上传者（无条件）")
        else:
            if conditions["password"]:
                if 密码 and 密码 == conditions["password"]:
                    met_parts.append("✅ 密码正确")
                else:
                    unmet_parts.append("❌ 需要密码")
            if conditions["require_like_first"]:
                liked = await _check_user_liked_first(interaction)
                if liked:
                    met_parts.append("✅ 已点赞")
                else:
                    unmet_parts.append("❌ 未点赞")
            if conditions["require_comment_first"]:
                min_len = conditions.get("min_comment_length", 1)
                met = await _check_user_comment_length(interaction, min_len)
                if met:
                    met_parts.append(f"✅ 已评论（≥{min_len}字）")
                else:
                    unmet_parts.append(f"❌ 评论不足{min_len}字")

            if not conditions["password"] and not conditions["require_like_first"] and not conditions["require_comment_first"]:
                met_parts.append("✅ 无条件")

        status = "\n".join(met_parts + unmet_parts) if (met_parts or unmet_parts) else "✅ 无条件"

        embed.add_field(
            name=f"📁 {rec['name']}",
            value=f"上传者: <@{rec['uploader_id']}>\n"
                  f"大小: {_format_size(rec['size'])}\n"
                  f"时间: {upload_time}\n"
                  f"{status}",
            inline=False,
        )

    embed.set_footer(text="点击下方按钮获取文件")
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
                # 检查是否是回复首楼的消息
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


async def _send_file_to_user(interaction: discord.Interaction, record: dict, 文件id: str):
    """从存储频道拉取文件并发送给用户"""
    try:
        storage_msg_id = record.get("storage_msg_id")
        guild_id = record.get("guild_id")

        if not storage_msg_id or not guild_id:
            await interaction.followup.send("❌ 文件记录无效。", ephemeral=True)
            return

        guild = interaction.client.get_guild(guild_id)
        if not guild:
            await interaction.followup.send("❌ 找不到服务器。", ephemeral=True)
            return

        guild_id_str = str(guild_id)
        channel_id = storage_channels.get(guild_id_str)
        if not channel_id:
            await interaction.followup.send("❌ 存储频道不存在，请重新上传文件。", ephemeral=True)
            return

        channel = guild.get_channel(int(channel_id))
        if not channel:
            await interaction.followup.send("❌ 存储频道已删除，请重新上传文件。", ephemeral=True)
            return

        try:
            msg = await channel.fetch_message(int(storage_msg_id))
        except discord.NotFound:
            await interaction.followup.send("❌ 文件已被删除，请重新上传。", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"获取存储消息失败: {e}")
            await interaction.followup.send(f"❌ 获取文件时出错: {e}", ephemeral=True)
            return

        if not msg.attachments:
            await interaction.followup.send("❌ 文件附件丢失，请重新上传。", ephemeral=True)
            return

        attachment = msg.attachments[0]
        file_bytes = await attachment.read()

        discord_file = discord.File(
            fp=io.BytesIO(file_bytes),
            filename=record["name"],
        )
        await interaction.followup.send(
            content=f"📁 **{record['name']}**\n"
                    f"上传者: <@{record['uploader_id']}>",
            file=discord_file,
            ephemeral=True,
        )
        logger.info(f"文件发送成功: {record['name']}")
    except Exception as e:
        logger.error(f"获取文件失败: {e}", exc_info=True)
        await interaction.followup.send(
            f"❌ 获取文件时出错: {e}",
            ephemeral=True,
        )


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
    passed = errors <= QUIZ_MAX_ERRORS

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
            f"正确 {correct}/{total} 题，错了 {errors} 题（超过 {QUIZ_MAX_ERRORS} 题需重考）\n\n"
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
    if len(quiz_questions) < QUIZ_QUESTIONS_PER_ROUND:
        await interaction.response.send_message(
            f"题库中题目不足 {QUIZ_QUESTIONS_PER_ROUND} 道，请联系管理员添加题目。",
            ephemeral=True,
        )
        return

    # 随机抽题，开始答题
    selected = random.sample(quiz_questions, QUIZ_QUESTIONS_PER_ROUND)
    quiz_sessions[user_id] = {
        "questions": selected,
        "current_index": 0,
        "answers": [],
        "started_at": datetime.now().isoformat(),
    }

    q = selected[0]
    embed = _build_question_embed(q, 0, QUIZ_QUESTIONS_PER_ROUND)
    view = QuizQuestionView(user_id, 0, QUIZ_QUESTIONS_PER_ROUND, interaction)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ═══════════════════════════════════════════
#  /答题 - 备用命令（答题频道按钮失效时使用）
# ═══════════════════════════════════════════

@bot.tree.command(name="答题", description="开始入群审核答题（备用命令）")
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


# FAQ 自动回复监听
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not message.guild:
        return

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
#  启动 Bot
# ═══════════════════════════════════════════

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("❌ 未设置 DISCORD_BOT_TOKEN 环境变量！")
        exit(1)

    token = token.strip()
    logger.info(f"🔑 Token 长度: {len(token)} 字符，开头: {token[:10]}...")

    async def shutdown():
        """优雅关闭：清理 HTTP runner 和 bot 连接"""
        logger.info("🛑 正在关闭...")
        if _runner:
            await _runner.cleanup()
        if not bot.is_closed():
            await bot.close()
        logger.info("✅ 已关闭")

    async def main():
        await start_http_server()
        # 注册持久化答题按钮（必须在 bot.start() 之前）
        bot.add_view(PersistentQuizView())
        logger.info("🚀 正在启动 Chen-Abot...")
        try:
            await bot.start(token)
        except discord.LoginFailure as e:
            logger.error(f"❌ Token 无效: {e}")
        except Exception as e:
            logger.error(f"❌ 启动失败: {e}")
        finally:
            await shutdown()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass