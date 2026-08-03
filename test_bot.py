import os
import discord

token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
print(f"Token 长度: {len(token)}")

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    print(f"✅ 成功登录: {bot.user}")

try:
    bot.run(token)
except Exception as e:
    print(f"❌ 登录失败: {type(e).__name__}: {e}")