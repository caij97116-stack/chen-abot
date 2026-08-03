import os
import sys
import aiohttp
import asyncio

token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
print(f"Token 长度: {len(token)}")
print(f"Token 前20字符: {token[:20]}")
print(f"Token 后10字符: {token[-10:]}")
print(f"Token 纯字节: {token.encode()[:30]}")

async def test():
    headers = {"Authorization": f"Bot {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get("https://discord.com/api/v10/users/@me", headers=headers) as resp:
            body = await resp.text()
            print(f"状态码: {resp.status}")
            print(f"响应: {body[:500]}")

asyncio.run(test())