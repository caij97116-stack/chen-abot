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
# 结构: { "file_id": { "name": str, "uploader_id": int, "channel_id": int, "message_id": int, "conditions": { "password": str|None, "role_id": int|None, "user_id": int|None, "description": str } } }
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
    """发送该频道第一条消息的跳转链接"""
    # 获取该频道的第一条消息
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
#  /上传文件 - 上传文件并设置获取条件
# ═══════════════════════════════════════════

# 上传文件时 Discord 限制: 25MB（免费）/ 100MB（Nitro）
# 支持的文件类型: png, jpg, gif, json, txt, zip, pdf, mp4, mp3 等


@bot.tree.command(name="上传文件", description="上传文件到社区，可设置获取条件")
@app_commands.describe(
    文件="要上传的文件",
    获取条件="设置获取条件，例如: /上传文件 文件:xxx.zip 获取条件:密码:abc123 或 身份组:@VIP 或 用户:@某人",
)
async def upload_file(
    interaction: discord.Interaction,
    文件: discord.Attachment,
    获取条件: Optional[str] = None,
):
    """
    上传文件并设置获取条件。
    条件格式（可组合，用 | 分隔）:
    - 密码:xxxxx   → 需要输入密码才能获取
    - 身份组:@角色名 → 需要拥有指定身份组
    - 用户:@用户名  → 仅指定用户可获取
    示例: 密码:abc123 | 身份组:@VIP
    """
    await interaction.response.defer(ephemeral=False)

    # 解析条件
    conditions = {
        "password": None,
        "role_id": None,
        "user_id": None,
        "description": 获取条件 or "无条件",
    }

    if 获取条件:
        parts = [p.strip() for p in 获取条件.split("|")]
        for part in parts:
            if part.startswith("密码:"):
                conditions["password"] = part[3:].strip()
            elif part.startswith("身份组:"):
                role_mention = part[4:].strip()
                # 从 mention 中提取 role id: <@&123456789>
                if role_mention.startswith("<@&") and role_mention.endswith(">"):
                    try:
                        conditions["role_id"] = int(role_mention[3:-1])
                    except ValueError:
                        pass
                else:
                    # 尝试按角色名匹配
                    role = discord.utils.get(interaction.guild.roles, name=role_mention)
                    if role:
                        conditions["role_id"] = role.id
            elif part.startswith("用户:"):
                user_mention = part[5:].strip()
                # 从 mention 中提取 user id: <@123456789>
                if user_mention.startswith("<@") and user_mention.endswith(">"):
                    uid = user_mention[2:-1]
                    if uid.startswith("!"):
                        uid = uid[1:]
                    try:
                        conditions["user_id"] = int(uid)
                    except ValueError:
                        pass

    # 将文件消息发送到频道
    file_msg = await interaction.channel.send(
        content=f"📁 **{文件.filename}**",
        file=await 文件.to_file(),
    )

    # 获取文件消息中的附件 URL
    if file_msg.attachments:
        attachment = file_msg.attachments[0]
    else:
        attachment = 文件

    # 生成文件 ID
    file_id = str(file_msg.id)

    # 保存记录
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

    # 构建条件说明
    cond_desc = _build_condition_description(conditions, interaction.guild)

    embed = discord.Embed(
        title="✅ 文件上传成功",
        description=f"**文件名:** {文件.filename}\n"
                    f"**大小:** {_format_size(文件.size)}\n"
                    f"**文件ID:** `{file_id}`\n\n"
                    f"**获取条件:** {cond_desc}\n\n"
                    f"其他人可使用 `/获取文件 文件id:{file_id}` 来获取此文件。",
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"上传者: {interaction.user.display_name}")

    await interaction.followup.send(embed=embed)


def _build_condition_description(conditions: dict, guild: discord.Guild) -> str:
    """构建条件的可读描述"""
    parts = []
    if conditions["user_id"]:
        user = guild.get_member(conditions["user_id"])
        parts.append(f"仅限用户: {user.mention if user else '未知用户'}")
    if conditions["role_id"]:
        role = guild.get_role(conditions["role_id"])
        parts.append(f"需要身份组: {role.mention if role else '未知身份组'}")
    if conditions["password"]:
        parts.append(f"需要密码: `{conditions['password']}`")
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
    """获取文件，需满足上传者设置的条件"""
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

    # 逐项检查条件
    failed_reasons = []

    if not is_uploader:
        # 检查用户限制
        if conditions["user_id"] and interaction.user.id != conditions["user_id"]:
            user = interaction.guild.get_member(conditions["user_id"])
            failed_reasons.append(
                f"仅限 {user.mention if user else '指定用户'} 获取"
            )

        # 检查身份组限制
        if conditions["role_id"]:
            role = interaction.guild.get_role(conditions["role_id"])
            if role and role not in interaction.user.roles:
                failed_reasons.append(f"需要拥有 {role.mention} 身份组")

        # 检查密码
        if conditions["password"]:
            if not 密码:
                failed_reasons.append("需要输入密码")
            elif 密码 != conditions["password"]:
                failed_reasons.append("密码错误")

    # 如果有未满足的条件
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
    try:
        # 获取原始消息中的附件
        channel = bot.get_channel(record["channel_id"])
        if channel:
            try:
                original_msg = await channel.fetch_message(record["message_id"])
                if original_msg.attachments:
                    file_bytes = await original_msg.attachments[0].read()
                    discord_file = discord.File(
                        file_bytes, filename=record["name"]
                    )
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