import os
import json
import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime
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

bot = commands.Bot(command_prefix="!", intents=intents)

# ─── 数据文件路径 ───
DATA_FILE = "file_records.json"

# ─── 文件记录存储 ───
# 结构: { "file_id": { "name": str, "uploader_id": int, ..., "conditions": { "password": str|None, "require_like_first": bool, "require_comment_first": bool, "comment_count": int } } }
file_records: dict = {}


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


# ═══════════════════════════════════════════
#  Bot 启动与就绪
# ═══════════════════════════════════════════

@bot.event
async def on_ready():
    load_records()
    logger.info(f"✅ Bot 已上线: {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"📡 正在服务 {len(bot.guilds)} 个服务器")
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔧 已同步 {len(synced)} 个斜杠命令")
    except Exception as e:
        logger.error(f"命令同步失败: {e}")


# ═══════════════════════════════════════════
#  新成员欢迎系统
# ═══════════════════════════════════════════

WELCOME_CHANNEL = "欢迎频道"
MEMBER_ROLE_NAME = "社区成员"


@bot.event
async def on_member_join(member: discord.Member):
    channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL)
    if channel is None:
        channel = member.guild.system_channel

    if channel:
        embed = discord.Embed(
            title="👋 欢迎加入社区！",
            description=f"欢迎 {member.mention} 来到 **{member.guild.name}**！",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"你是第 {len(member.guild.members)} 位成员")
        await channel.send(embed=embed)

    role = discord.utils.get(member.guild.roles, name=MEMBER_ROLE_NAME)
    if role:
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            pass


# ═══════════════════════════════════════════
#  /回顶 - 回到楼主（第1楼）
# ═══════════════════════════════════════════

@bot.tree.command(name="回顶", description="回到楼主（第1楼）")
async def back_to_top(interaction: discord.Interaction):
    """直接显示首楼内容，并回复首楼实现跳转"""
    async for first_msg in interaction.channel.history(oldest_first=True, limit=1):
        # 回复首楼 —— Discord 会自动生成跳转横条，点击即可回到首楼
        content = first_msg.content or "*（无文字内容）*"
        if len(content) > 1900:
            content = content[:1900] + "\n\n*（内容过长已截断）*"

        embed = discord.Embed(
            title="🔝 首楼（楼主）",
            description=content,
            color=discord.Color.blue(),
            timestamp=first_msg.created_at,
        )
        embed.set_author(
            name=first_msg.author.display_name,
            icon_url=first_msg.author.display_avatar.url,
        )

        # 如果有附件也一并显示
        if first_msg.attachments:
            attachment_urls = "\n".join(
                f"[{a.filename}]({a.url})" for a in first_msg.attachments
            )
            embed.add_field(name="附件", value=attachment_urls[:1024], inline=False)

        # 用 reply 发送，回复横条直接可跳转回首楼
        await interaction.response.send_message(
            embed=embed,
            reference=first_msg,
            mention_author=False,
        )
        return

    await interaction.response.send_message("该频道还没有任何消息。", ephemeral=True)


# ═══════════════════════════════════════════
#  /上传文件 - 上传文件并设置获取条件
# ═══════════════════════════════════════════

_YES_NO = [
    app_commands.Choice(name="是", value="yes"),
    app_commands.Choice(name="否", value="no"),
]


@bot.tree.command(name="上传文件", description="上传文件到社区，由上传者自定义获取条件")
@app_commands.describe(
    文件="要上传的文件（png/json/zip/txt/mp4等）",
    密码="自定义密码，获取文件时需要输入。不填则不需要密码",
    需要点赞="是否需要给首楼点赞才能获取？",
    需要评论="是否需要在首楼下评论才能获取？",
    评论条数="需要评论多少条？（默认1条，仅当「需要评论=是」时生效）",
)
@app_commands.choices(需要点赞=_YES_NO, 需要评论=_YES_NO)
async def upload_file(
    interaction: discord.Interaction,
    文件: discord.Attachment,
    密码: Optional[str] = None,
    需要点赞: Optional[app_commands.Choice[str]] = None,
    需要评论: Optional[app_commands.Choice[str]] = None,
    评论条数: int = 1,
):
    await interaction.response.defer(ephemeral=False)

    if 评论条数 < 1:
        评论条数 = 1
    if 评论条数 > 50:
        评论条数 = 50

    conditions = {
        "password": 密码.strip() if 密码 else None,
        "require_like_first": 需要点赞 is not None and 需要点赞.value == "yes",
        "require_comment_first": 需要评论 is not None and 需要评论.value == "yes",
        "comment_count": 评论条数 if (需要评论 is not None and 需要评论.value == "yes") else 0,
    }

    # 发送文件到频道
    file_msg = await interaction.channel.send(
        content=f"📁 **{文件.filename}**",
        file=await 文件.to_file(),
    )

    attachment = file_msg.attachments[0] if file_msg.attachments else 文件
    file_id = str(file_msg.id)

    file_records[file_id] = {
        "name": 文件.filename,
        "uploader_id": interaction.user.id,
        "uploader_name": interaction.user.display_name,
        "channel_id": interaction.channel.id,
        "guild_id": interaction.guild.id,
        "message_id": file_msg.id,
        "attachment_url": attachment.url,
        "size": 文件.size,
        "conditions": conditions,
        "upload_time": datetime.now().isoformat(),
    }
    save_records()

    cond_desc = _build_condition_description(conditions)
    embed = discord.Embed(
        title="✅ 文件上传成功",
        description=f"**文件名:** {文件.filename}\n"
                    f"**大小:** {_format_size(文件.size)}\n"
                    f"**文件ID:** `{file_id}`\n\n"
                    f"**获取条件:** {cond_desc}\n\n"
                    f"使用 `/获取文件 文件id:{file_id}` 来获取此文件。",
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"上传者: {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


def _build_condition_description(conditions: dict) -> str:
    parts = []
    if conditions["password"]:
        parts.append("需要密码")
    if conditions["require_like_first"]:
        parts.append("需要点赞首楼")
    if conditions["require_comment_first"]:
        cnt = conditions.get("comment_count", 1)
        parts.append(f"需要评论首楼 {cnt} 条")
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
#  /获取文件 - 获取已上传的文件（需满足条件）
# ═══════════════════════════════════════════

@bot.tree.command(name="获取文件", description="获取已上传的文件（需满足获取条件）")
@app_commands.describe(
    文件id="要获取的文件ID（上传时会显示）",
    密码="如果上传者设置了密码，请在此输入",
)
async def get_file(
    interaction: discord.Interaction,
    文件id: str,
    密码: Optional[str] = None,
):
    """获取文件，需满足上传者设置的条件（点赞首楼/评论首楼/密码）"""
    await interaction.response.defer(ephemeral=True)

    # 查找文件记录
    record = file_records.get(文件id)
    if not record:
        await interaction.followup.send(
            f"❌ 未找到文件ID `{文件id}` 对应的文件。",
            ephemeral=True,
        )
        return

    conditions = record["conditions"]

    # 检查是否为上传者本人（上传者永远可以获取）
    is_uploader = interaction.user.id == record["uploader_id"]

    failed_reasons = []

    if not is_uploader:
        # 检查密码
        if conditions["password"]:
            if not 密码:
                failed_reasons.append("需要输入密码")
            elif 密码 != conditions["password"]:
                failed_reasons.append("密码错误")

        # 检查点赞首楼
        if conditions["require_like_first"]:
            liked = await _check_user_liked_first(interaction)
            if not liked:
                failed_reasons.append("需要给首楼（第一条消息）点赞")

        # 检查评论首楼
        if conditions["require_comment_first"]:
            needed = conditions.get("comment_count", 1)
            count = await _count_user_comments_on_first(interaction)
            if count < needed:
                failed_reasons.append(f"需要在首楼下评论 {needed} 条（当前已评论 {count} 条）")

    if failed_reasons:
        embed = discord.Embed(
            title="🔒 无法获取文件",
            description=f"**文件:** {record['name']}\n\n"
                        f"以下条件未满足:\n"
                        + "\n".join(f"• {r}" for r in failed_reasons),
            color=discord.Color.red(),
        )
        if conditions["password"] and not 密码:
            embed.set_footer(text="提示: 使用 /获取文件 文件id:xxx 密码:xxx 来输入密码")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # 条件满足，发送文件
    await _send_file_to_user(interaction, record, 文件id)


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


async def _count_user_comments_on_first(interaction: discord.Interaction) -> int:
    """统计用户在频道第一条消息下方评论了多少条"""
    try:
        async for first_msg in interaction.channel.history(oldest_first=True, limit=1):
            first_msg_id = first_msg.id
            count = 0
            async for msg in interaction.channel.history(after=first_msg, limit=200):
                if msg.author.id == interaction.user.id and msg.reference and msg.reference.message_id == first_msg_id:
                    count += 1
            return count
        return 0
    except Exception:
        return 0


async def _send_file_to_user(interaction: discord.Interaction, record: dict, 文件id: str):
    """发送文件给用户"""
    try:
        # 获取原始消息中的附件
        channel = bot.get_channel(record["channel_id"])
        if channel:
            try:
                original_msg = await channel.fetch_message(record["message_id"])
                if original_msg.attachments:
                    file_bytes = await original_msg.attachments[0].read()
                    discord_file = discord.File(file_bytes, filename=record["name"])
                    await interaction.followup.send(
                        content=f"📁 **{record['name']}**\n"
                                f"上传者: <@{record['uploader_id']}>\n"
                                f"文件ID: `{文件id}`",
                        file=discord_file,
                        ephemeral=True,
                    )
                    return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        # 如果无法从原始消息获取，尝试从 URL 下载
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(record["attachment_url"]) as resp:
                if resp.status == 200:
                    file_bytes = await resp.read()
                    discord_file = discord.File(file_bytes, filename=record["name"])
                    await interaction.followup.send(
                        content=f"📁 **{record['name']}**\n"
                                f"上传者: <@{record['uploader_id']}>\n"
                                f"文件ID: `{文件id}`",
                        file=discord_file,
                        ephemeral=True,
                    )
                    return

        await interaction.followup.send(
            "❌ 无法读取文件，原始文件可能已被删除。",
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"获取文件失败: {e}")
        await interaction.followup.send(
            f"❌ 获取文件时出错: {e}",
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
#  启动 Bot
# ═══════════════════════════════════════════

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("❌ 未设置 DISCORD_BOT_TOKEN 环境变量！")
        logger.error("请在环境变量中设置 DISCORD_BOT_TOKEN，或创建 .env 文件。")
        exit(1)

    logger.info("🚀 正在启动 Chen-Abot...")
    bot.run(token)