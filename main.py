import os
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import logging
from datetime import datetime, timedelta

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

# ─── 审核日志频道名称（可自定义） ───
AUDIT_LOG_CHANNEL = "审核日志"
WELCOME_CHANNEL = "欢迎频道"
MEMBER_ROLE_NAME = "社区成员"  # 新成员自动获得的身份组


# ═══════════════════════════════════════════
#  Bot 启动与就绪
# ═══════════════════════════════════════════

@bot.event
async def on_ready():
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

@bot.event
async def on_member_join(member: discord.Member):
    """新成员加入时发送欢迎消息并自动分配身份组"""
    # 发送欢迎消息
    channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL)
    if channel is None:
        channel = member.guild.system_channel

    if channel:
        embed = discord.Embed(
            title="👋 欢迎加入社区！",
            description=f"欢迎 {member.mention} 来到 **{member.guild.name}**！\n"
                        f"请先阅读 <#规则频道> 了解社区规范。",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"你是第 {len(member.guild.members)} 位成员")
        await channel.send(embed=embed)

    # 自动分配身份组
    role = discord.utils.get(member.guild.roles, name=MEMBER_ROLE_NAME)
    if role:
        try:
            await member.add_roles(role)
            logger.info(f"已为 {member.name} 分配身份组: {role.name}")
        except discord.Forbidden:
            logger.warning(f"权限不足，无法为 {member.name} 分配身份组")


@bot.event
async def on_member_remove(member: discord.Member):
    """成员离开时记录"""
    channel = discord.utils.get(member.guild.text_channels, name=AUDIT_LOG_CHANNEL)
    if channel:
        embed = discord.Embed(
            title="👋 成员离开",
            description=f"{member.mention} ({member.name}) 离开了服务器",
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        await channel.send(embed=embed)


# ═══════════════════════════════════════════
#  消息审核系统（自动过滤）
# ═══════════════════════════════════════════

# 违禁词列表（可自行添加）
BLOCKED_WORDS = [
    # 在这里添加需要过滤的词汇
    # "违禁词1",
    # "违禁词2",
]

# 屏蔽链接的域名
BLOCKED_DOMAINS = [
    # "discord.gg",
    # "malicious-site.com",
]


@bot.event
async def on_message(message: discord.Message):
    """消息审核：检测违禁词和不安全链接"""
    # 忽略 bot 自己的消息
    if message.author.bot:
        return

    # 检测违禁词
    content_lower = message.content.lower()
    for word in BLOCKED_WORDS:
        if word.lower() in content_lower:
            await message.delete()
            await _send_warning(message, f"违禁词检测: `{word}`")
            await _log_to_audit(
                message.guild, "🚫 违禁词触发",
                f"用户: {message.author.mention}\n内容: {message.content[:500]}\n触发词: `{word}`",
                discord.Color.red()
            )
            return

    # 检测不安全链接
    for domain in BLOCKED_DOMAINS:
        if domain in content_lower:
            await message.delete()
            await _send_warning(message, f"不安全链接检测: `{domain}`")
            await _log_to_audit(
                message.guild, "🔗 不安全链接",
                f"用户: {message.author.mention}\n内容: {message.content[:500]}\n域名: `{domain}`",
                discord.Color.red()
            )
            return

    # 处理命令
    await bot.process_commands(message)


async def _send_warning(message: discord.Message, reason: str):
    """向用户发送警告"""
    try:
        embed = discord.Embed(
            title="⚠️ 警告",
            description=f"你的消息已被删除，原因: {reason}\n请遵守社区规范。",
            color=discord.Color.yellow(),
        )
        await message.author.send(embed=embed)
    except discord.Forbidden:
        pass  # 无法发送私信


async def _log_to_audit(guild: discord.Guild, title: str, description: str, color: discord.Color):
    """记录到审核日志频道"""
    channel = discord.utils.get(guild.text_channels, name=AUDIT_LOG_CHANNEL)
    if channel:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now(),
        )
        await channel.send(embed=embed)


# ═══════════════════════════════════════════
#  Slash 命令：审核管理
# ═══════════════════════════════════════════

@bot.tree.command(name="ping", description="查看 bot 延迟和状态")
async def ping(interaction: discord.Interaction):
    """检查 bot 是否在线"""
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"延迟: {latency}ms\n在线服务器: {len(bot.guilds)} 个",
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="清除", description="批量删除消息（管理员）")
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(数量="要删除的消息数量（1-100）")
async def clear(interaction: discord.Interaction, 数量: int):
    """清除指定数量的消息"""
    if 数量 < 1 or 数量 > 100:
        await interaction.response.send_message("数量必须在 1-100 之间！", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=数量)

    await _log_to_audit(
        interaction.guild,
        "🧹 消息清除",
        f"管理员: {interaction.user.mention}\n频道: {interaction.channel.mention}\n清除数量: {len(deleted)}",
        discord.Color.blue(),
    )
    await interaction.followup.send(f"已清除 {len(deleted)} 条消息", ephemeral=True)


@bot.tree.command(name="禁言", description="禁言某个成员（管理员）")
@app_commands.default_permissions(moderate_members=True)
@app_commands.describe(成员="要禁言的成员", 分钟="禁言时长（分钟，默认10分钟）", 原因="禁言原因")
async def mute(interaction: discord.Interaction, 成员: discord.Member, 分钟: int = 10, 原因: str = "未指定"):
    """禁言指定成员"""
    if 分钟 < 1 or 分钟 > 40320:  # 最长28天
        await interaction.response.send_message("禁言时间必须在 1-40320 分钟之间！", ephemeral=True)
        return

    duration = timedelta(minutes=分钟)
    try:
        await 成员.timeout(duration, reason=f"由 {interaction.user} 禁言: {原因}")
        embed = discord.Embed(
            title="🔇 成员已被禁言",
            description=f"**成员:** {成员.mention}\n**时长:** {分钟} 分钟\n**原因:** {原因}\n**操作者:** {interaction.user.mention}",
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        await interaction.response.send_message(embed=embed)

        await _log_to_audit(
            interaction.guild,
            "🔇 禁言",
            f"成员: {成员.mention} ({成员.id})\n时长: {分钟} 分钟\n原因: {原因}\n操作者: {interaction.user.mention}",
            discord.Color.orange(),
        )
    except discord.Forbidden:
        await interaction.response.send_message("权限不足，无法禁言该成员！", ephemeral=True)


@bot.tree.command(name="解除禁言", description="解除成员的禁言（管理员）")
@app_commands.default_permissions(moderate_members=True)
@app_commands.describe(成员="要解除禁言的成员")
async def unmute(interaction: discord.Interaction, 成员: discord.Member):
    """解除禁言"""
    try:
        await 成员.timeout(None, reason=f"由 {interaction.user} 解除禁言")
        embed = discord.Embed(
            title="🔊 已解除禁言",
            description=f"**成员:** {成员.mention}\n**操作者:** {interaction.user.mention}",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        await interaction.response.send_message(embed=embed)

        await _log_to_audit(
            interaction.guild,
            "🔊 解除禁言",
            f"成员: {成员.mention}\n操作者: {interaction.user.mention}",
            discord.Color.green(),
        )
    except discord.Forbidden:
        await interaction.response.send_message("权限不足，无法解除禁言！", ephemeral=True)


@bot.tree.command(name="踢出", description="将成员踢出服务器（管理员）")
@app_commands.default_permissions(kick_members=True)
@app_commands.describe(成员="要踢出的成员", 原因="踢出原因")
async def kick(interaction: discord.Interaction, 成员: discord.Member, 原因: str = "未指定"):
    """踢出成员"""
    try:
        await 成员.kick(reason=f"由 {interaction.user} 踢出: {原因}")
        embed = discord.Embed(
            title="👢 成员已被踢出",
            description=f"**成员:** {成员.mention}\n**原因:** {原因}\n**操作者:** {interaction.user.mention}",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        await interaction.response.send_message(embed=embed)

        await _log_to_audit(
            interaction.guild,
            "👢 踢出成员",
            f"成员: {成员.mention} ({成员.id})\n原因: {原因}\n操作者: {interaction.user.mention}",
            discord.Color.red(),
        )
    except discord.Forbidden:
        await interaction.response.send_message("权限不足，无法踢出该成员！", ephemeral=True)


@bot.tree.command(name="封禁", description="封禁某个成员（管理员）")
@app_commands.default_permissions(ban_members=True)
@app_commands.describe(成员="要封禁的成员", 原因="封禁原因", 删除消息天数="删除该成员过去几天的消息（0-7）")
async def ban(interaction: discord.Interaction, 成员: discord.Member, 原因: str = "未指定", 删除消息天数: int = 0):
    """封禁成员"""
    if 删除消息天数 < 0 or 删除消息天数 > 7:
        await interaction.response.send_message("删除消息天数必须在 0-7 之间！", ephemeral=True)
        return

    try:
        await 成员.ban(reason=f"由 {interaction.user} 封禁: {原因}", delete_message_days=删除消息天数)
        embed = discord.Embed(
            title="🔨 成员已被封禁",
            description=f"**成员:** {成员.mention}\n**原因:** {原因}\n**操作者:** {interaction.user.mention}",
            color=discord.Color.dark_red(),
            timestamp=datetime.now(),
        )
        await interaction.response.send_message(embed=embed)

        await _log_to_audit(
            interaction.guild,
            "🔨 封禁成员",
            f"成员: {成员.mention} ({成员.id})\n原因: {原因}\n操作者: {interaction.user.mention}",
            discord.Color.dark_red(),
        )
    except discord.Forbidden:
        await interaction.response.send_message("权限不足，无法封禁该成员！", ephemeral=True)


@bot.tree.command(name="解除封禁", description="解除成员的封禁（管理员）")
@app_commands.default_permissions(ban_members=True)
@app_commands.describe(用户id="要解除封禁的用户ID")
async def unban(interaction: discord.Interaction, 用户id: str):
    """解除封禁（通过用户ID）"""
    try:
        user = await bot.fetch_user(int(用户id))
        await interaction.guild.unban(user, reason=f"由 {interaction.user} 解除封禁")
        embed = discord.Embed(
            title="✅ 已解除封禁",
            description=f"**用户:** {user.mention}\n**操作者:** {interaction.user.mention}",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        await interaction.response.send_message(embed=embed)

        await _log_to_audit(
            interaction.guild,
            "✅ 解除封禁",
            f"用户: {user.mention} ({用户id})\n操作者: {interaction.user.mention}",
            discord.Color.green(),
        )
    except ValueError:
        await interaction.response.send_message("无效的用户ID！", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("该用户不在封禁列表中！", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("权限不足！", ephemeral=True)


@bot.tree.command(name="警告", description="向成员发送警告（管理员）")
@app_commands.default_permissions(moderate_members=True)
@app_commands.describe(成员="要警告的成员", 原因="警告原因")
async def warn(interaction: discord.Interaction, 成员: discord.Member, 原因: str):
    """向成员发送警告"""
    embed = discord.Embed(
        title="⚠️ 警告",
        description=f"**成员:** {成员.mention}\n**原因:** {原因}\n**操作者:** {interaction.user.mention}",
        color=discord.Color.yellow(),
        timestamp=datetime.now(),
    )
    await interaction.response.send_message(embed=embed)

    # 私信通知被警告的成员
    try:
        dm_embed = discord.Embed(
            title="⚠️ 你收到了一条警告",
            description=f"在 **{interaction.guild.name}** 中，你收到了一条警告。\n\n**原因:** {原因}",
            color=discord.Color.yellow(),
        )
        await 成员.send(embed=dm_embed)
    except discord.Forbidden:
        pass

    await _log_to_audit(
        interaction.guild,
        "⚠️ 警告",
        f"成员: {成员.mention}\n原因: {原因}\n操作者: {interaction.user.mention}",
        discord.Color.yellow(),
    )


# ═══════════════════════════════════════════
#  Slash 命令：信息查询
# ═══════════════════════════════════════════

@bot.tree.command(name="用户信息", description="查看用户信息")
@app_commands.describe(成员="要查看的成员（不填则查看自己）")
async def userinfo(interaction: discord.Interaction, 成员: discord.Member = None):
    """查看用户详细信息"""
    成员 = 成员 or interaction.user

    # 计算加入时间
    joined_at = 成员.joined_at.strftime("%Y-%m-%d %H:%M") if 成员.joined_at else "未知"
    created_at = 成员.created_at.strftime("%Y-%m-%d %H:%M")

    # 身份组
    roles = [role.mention for role in 成员.roles[1:]]  # 排除 @everyone
    roles_str = " ".join(roles) if roles else "无"

    embed = discord.Embed(
        title=f"👤 {成员.display_name} 的用户信息",
        color=成员.color if 成员.color != discord.Color.default() else discord.Color.blue(),
        timestamp=datetime.now(),
    )
    embed.set_thumbnail(url=成员.display_avatar.url)
    embed.add_field(name="用户名", value=f"{成员.name}#{成员.discriminator}", inline=True)
    embed.add_field(name="用户ID", value=成员.id, inline=True)
    embed.add_field(name="加入时间", value=joined_at, inline=True)
    embed.add_field(name="账号创建", value=created_at, inline=True)
    embed.add_field(name="身份组", value=roles_str[:1024], inline=False)
    embed.add_field(name="是否为Bot", value="是" if 成员.bot else "否", inline=True)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="服务器信息", description="查看服务器信息")
async def serverinfo(interaction: discord.Interaction):
    """查看服务器信息"""
    guild = interaction.guild

    # 统计
    total_members = guild.member_count
    humans = len([m for m in guild.members if not m.bot])
    bots = len([m for m in guild.members if m.bot])
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    roles = len(guild.roles)

    embed = discord.Embed(
        title=f"📊 {guild.name} 服务器信息",
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="服务器ID", value=guild.id, inline=True)
    embed.add_field(name="创建者", value=guild.owner.mention if guild.owner else "未知", inline=True)
    embed.add_field(name="创建时间", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="成员总数", value=total_members, inline=True)
    embed.add_field(name="真人/机器人", value=f"{humans} / {bots}", inline=True)
    embed.add_field(name="文字/语音频道", value=f"{text_channels} / {voice_channels}", inline=True)
    embed.add_field(name="身份组数量", value=roles, inline=True)
    embed.add_field(name="Boost等级", value=f"等级 {guild.premium_tier} ({guild.premium_subscription_count} 次)", inline=True)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="头像", description="查看用户头像")
@app_commands.describe(成员="要查看的成员（不填则查看自己）")
async def avatar(interaction: discord.Interaction, 成员: discord.Member = None):
    """查看头像"""
    成员 = 成员 or interaction.user
    embed = discord.Embed(
        title=f"🖼️ {成员.display_name} 的头像",
        color=discord.Color.blue(),
    )
    embed.set_image(url=成员.display_avatar.url)
    await interaction.response.send_message(embed=embed)


# ═══════════════════════════════════════════
#  Slash 命令：帮助
# ═══════════════════════════════════════════

@bot.tree.command(name="帮助", description="查看所有可用命令")
async def help_command(interaction: discord.Interaction):
    """显示帮助信息"""
    embed = discord.Embed(
        title="🤖 Chen-Abot 帮助菜单",
        description="以下是所有可用命令：",
        color=discord.Color.purple(),
        timestamp=datetime.now(),
    )

    embed.add_field(
        name="📋 通用命令",
        value="`/ping` - 查看 bot 延迟\n"
              "`/用户信息` - 查看用户信息\n"
              "`/服务器信息` - 查看服务器信息\n"
              "`/头像` - 查看用户头像\n"
              "`/帮助` - 显示此菜单",
        inline=False,
    )

    embed.add_field(
        name="🛡️ 管理命令",
        value="`/清除 <数量>` - 批量删除消息\n"
              "`/禁言 <成员> <分钟> <原因>` - 禁言成员\n"
              "`/解除禁言 <成员>` - 解除禁言\n"
              "`/警告 <成员> <原因>` - 警告成员\n"
              "`/踢出 <成员> <原因>` - 踢出成员\n"
              "`/封禁 <成员> <原因>` - 封禁成员\n"
              "`/解除封禁 <用户ID>` - 解除封禁",
        inline=False,
    )

    embed.add_field(
        name="🤖 自动功能",
        value="• 新成员欢迎 + 自动分配身份组\n"
              "• 违禁词过滤\n"
              "• 不安全链接检测\n"
              "• 审核日志记录",
        inline=False,
    )

    embed.set_footer(text="祝你在社区玩得开心！")
    await interaction.response.send_message(embed=embed)


# ═══════════════════════════════════════════
#  错误处理
# ═══════════════════════════════════════════

@bot.event
async def on_command_error(ctx: commands.Context, error):
    """命令错误处理"""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("你没有权限执行此操作！", delete_after=5)
        return
    logger.error(f"命令错误: {error}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """斜杠命令错误处理"""
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