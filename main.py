import os
import io
import json
import random
import re
import secrets
import struct
import asyncio
import zipfile
import zlib
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from aiohttp import web
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from collections import defaultdict, deque
from urllib.parse import quote, unquote, urlparse

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
GAMBLE_FILE = "gamble_data.json"
GAMBLE_CHANNEL_FILE = "gamble_channels.json"
GIVEAWAY_FILE = "giveaway_data.json"
MUSIC_FILE = "music_data.json"
MUSIC_CHANNEL_KEYWORD = "贝多芬"
MUSIC_STORE_DIR = os.path.join("file_store", "music")
MUSIC_MAX_BYTES = 25 * 1024 * 1024
MUSIC_MAX_TRACKS = 100
MUSIC_PAGE_SIZE = 20
MUSIC_AUDIO_EXTS = (".mp3", ".ogg", ".wav", ".m4a", ".flac", ".opus")
MUSIC_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
MUSIC_PLATFORM_RE = re.compile(
    r"(music\.163\.com|163cn\.tv|y\.qq\.com|i\.y\.qq\.com|open\.spotify\.com|"
    r"youtu\.be|youtube\.com|bilibili\.com|b23\.tv|kugou\.com|kuwo\.cn)",
    re.IGNORECASE,
)
GAMBLE_CHANNEL_KEYWORD = "赌王来一下"
GAMBLE_DRAW_COST_POINTS = 10
GAMBLE_DRAW_TEN = 10
GAMBLE_FRAGMENT_PER_TICKET = 5
STORY_FILE = "story_data.json"
STORY_CHANNEL_KEYWORD = "故事分享会"
STORY_STORE_DIR = os.path.join("file_store", "story")
STORY_MAX_IMAGES = 4
STORY_TEXT_MAX = 1000
STORY_COMMENT_MAX = 300
STORY_COMMENT_SHOW = 8
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
FILE_STORE_DIR = "file_store"
ALERT_CHANNEL_KEYWORD = "异常提醒"
ALERT_CHANNEL_NAME = "异常提醒"
ALERT_DOWNLOAD_WINDOW_SEC = 60
ALERT_DOWNLOAD_COUNT = 5
ALERT_SPAM_WINDOW_SEC = 12
ALERT_SPAM_COUNT = 6
ALERT_COOLDOWN_SEC = 180
DOWNLOAD_COOLDOWN_SEC = 30
CLAIM_TTL_SEC = 600
AD_KEYWORDS = (
    "免费代充", "代充", "出号", "收号", "卖号", "倒卖", "代肝",
    "加v", "加微", "加vx", "微信", "qq群", "QQ群", "淘宝", "咸鱼",
    "闲鱼", "转卖", "代打", "优惠券",
)
_alert_download_times: dict = defaultdict(deque)
_alert_message_times: dict = defaultdict(deque)
_alert_last_sent: dict = {}
_download_cooldowns: dict = {}
_claim_tokens: dict = {}

# ─── 文件记录存储 ───
# 结构: { "file_id": { "name": str, "uploader_id": int, "status": "draft|published", "description": str,
#                      "attachments": [{ "original_name": str, "custom_name": str, "storage_path": str, "size": int }],
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


_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_TEXT_KEY = "Comment"
_PNG_LSB_MAGIC = b"CABOTD"
_ZIP_EXTRA_ID = 0x4342  # 'CB'


def _ensure_file_store():
    os.makedirs(FILE_STORE_DIR, exist_ok=True)
    os.makedirs(MUSIC_STORE_DIR, exist_ok=True)
    os.makedirs(STORY_STORE_DIR, exist_ok=True)


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "file")
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in base).strip()
    return cleaned[:80] or "file"


def _new_file_id() -> str:
    for _ in range(32):
        fid = secrets.token_hex(8)
        if fid not in file_records:
            return fid
    return secrets.token_hex(16)


def _new_storage_path(filename: str) -> str:
    _ensure_file_store()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return os.path.join(FILE_STORE_DIR, f"{stamp}_{secrets.token_hex(6)}_{_safe_filename(filename)}")


def _save_bytes_to_store(file_bytes: bytes, filename: str) -> str:
    path = _new_storage_path(filename)
    with open(path, "wb") as f:
        f.write(file_bytes)
    return path


def _read_stored_bytes(att: dict):
    path = att.get("storage_path")
    if path and os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    return None


async def _read_attachment_bytes(record: dict, att: dict):
    data = _read_stored_bytes(att)
    if data is not None:
        return data
    msg_id = att.get("storage_msg_id")
    if not msg_id:
        return None
    guild = bot.get_guild(record.get("guild_id")) if record.get("guild_id") else None
    if not guild:
        return None
    channel_id = storage_channels.get(str(record.get("guild_id")))
    if not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return None
    try:
        msg = await channel.fetch_message(int(msg_id))
    except Exception:
        return None
    if not msg.attachments:
        return None
    try:
        data = await msg.attachments[0].read()
    except Exception:
        return None
    try:
        att["storage_path"] = _save_bytes_to_store(data, att.get("custom_name") or msg.attachments[0].filename)
        save_records()
    except Exception:
        pass
    return data


def _delete_stored_file(att: dict):
    path = att.get("storage_path")
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except Exception:
            pass


def _delete_record_files(record: dict):
    for att in record.get("attachments") or []:
        _delete_stored_file(att)


def _new_code(prefix: str, existing: set) -> str:
    for _ in range(32):
        code = prefix + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        if code not in existing:
            return code
    return prefix + secrets.token_hex(4).upper()


def _new_resource_code() -> str:
    existing = {rec.get("resource_code") for rec in file_records.values()}
    return _new_code("R-", existing)


def _ensure_resource_code(record: dict) -> str:
    code = record.get("resource_code")
    if code:
        return code
    code = _new_resource_code()
    record["resource_code"] = code
    return code


def _all_downloader_codes() -> set:
    codes = set()
    for rec in file_records.values():
        for code in (rec.get("downloader_codes") or {}).values():
            if code:
                codes.add(code)
    for log in download_logs:
        code = log.get("downloader_code")
        if code:
            codes.add(code)
    return codes


def _new_downloader_code() -> str:
    return _new_code("D-", _all_downloader_codes())


def _record_id_of(record: dict):
    for fid, rec in file_records.items():
        if rec is record:
            return fid
    return None


def _get_or_create_downloader_code(record: dict, user_id) -> str:
    mapping = record.setdefault("downloader_codes", {})
    key = str(user_id)
    if mapping.get(key):
        return mapping[key]
    code = _new_downloader_code()
    mapping[key] = code
    save_records()
    return code


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)


def _iter_png_chunks(data: bytes):
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        start = pos
        pos += 12 + length
        if pos > len(data):
            break
        yield start, chunk_type, data[start + 8:start + 8 + length]


def _embed_png_text(data: bytes, keyword: str, value: str) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        return data
    payload = keyword.encode("latin-1") + b"\x00" + value.encode("latin-1", "replace")
    text_chunk = _png_chunk(b"tEXt", payload)
    pieces = [data[:8]]
    inserted = False
    for start, chunk_type, chunk_data in _iter_png_chunks(data):
        if chunk_type == b"tEXt" and chunk_data.startswith(keyword.encode("latin-1") + b"\x00"):
            continue
        if chunk_type == b"IEND" and not inserted:
            pieces.append(text_chunk)
            inserted = True
        length = struct.unpack(">I", data[start:start + 4])[0]
        pieces.append(data[start:start + 12 + length])
    if not inserted:
        pieces.append(text_chunk)
    return b"".join(pieces)


def _read_png_text(data: bytes, keyword: str = _PNG_TEXT_KEY):
    if not data.startswith(_PNG_SIGNATURE):
        return None
    prefix = keyword.encode("latin-1") + b"\x00"
    for _, chunk_type, chunk_data in _iter_png_chunks(data):
        if chunk_type == b"tEXt" and chunk_data.startswith(prefix):
            try:
                return chunk_data[len(prefix):].decode("latin-1")
            except Exception:
                return None
    return None


def _png_parse_ihdr(data: bytes):
    for _, chunk_type, chunk_data in _iter_png_chunks(data):
        if chunk_type == b"IHDR" and len(chunk_data) >= 13:
            width, height = struct.unpack(">II", chunk_data[:8])
            return width, height, chunk_data[8], chunk_data[9], chunk_data[12]
    return None


def _png_row_bytes(width: int, bit_depth: int, color_type: int):
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if not channels or width <= 0:
        return None
    if bit_depth == 8:
        return width * channels
    if bit_depth == 16:
        return width * channels * 2
    bits = width * channels * bit_depth
    return (bits + 7) // 8


def _png_collect_idat(data: bytes) -> bytes:
    return b"".join(chunk_data for _, chunk_type, chunk_data in _iter_png_chunks(data) if chunk_type == b"IDAT")


def _png_rebuild_with_idat(data: bytes, new_idat: bytes) -> bytes:
    pieces = [data[:8]]
    wrote = False
    for start, chunk_type, chunk_data in _iter_png_chunks(data):
        if chunk_type == b"IDAT":
            if not wrote:
                for i in range(0, len(new_idat), 32768):
                    pieces.append(_png_chunk(b"IDAT", new_idat[i:i + 32768]))
                wrote = True
            continue
        length = struct.unpack(">I", data[start:start + 4])[0]
        pieces.append(data[start:start + 12 + length])
    return b"".join(pieces)


def _bits_to_bytes(bits: list) -> bytes:
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        value = 0
        for j in range(8):
            value = (value << 1) | bits[i + j]
        out.append(value)
    return bytes(out)


def _payload_to_bits(payload: bytes) -> list:
    bits = []
    for byte in payload:
        for i in range(8):
            bits.append((byte >> (7 - i)) & 1)
    return bits


def _embed_png_lsb(data: bytes, code: str) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        return data
    info = _png_parse_ihdr(data)
    if not info:
        return data
    width, height, bit_depth, color_type, interlace = info
    if interlace or bit_depth not in (8, 16):
        return data
    row = _png_row_bytes(width, bit_depth, color_type)
    if not row:
        return data
    try:
        raw = zlib.decompress(_png_collect_idat(data))
    except Exception:
        return data
    expected = (row + 1) * height
    if len(raw) < expected:
        return data
    payload = _PNG_LSB_MAGIC + bytes([min(len(code), 255)]) + code.encode("latin-1", "replace")[:255]
    bits = _payload_to_bits(payload)
    if row * height < len(bits):
        return data
    buf = bytearray(raw)
    bit_i = 0
    for y in range(height):
        start = y * (row + 1) + 1
        for x in range(row):
            if bit_i >= len(bits):
                break
            buf[start + x] = (buf[start + x] & 0xFE) | bits[bit_i]
            bit_i += 1
        if bit_i >= len(bits):
            break
    try:
        new_idat = zlib.compress(bytes(buf), 9)
    except Exception:
        return data
    return _png_rebuild_with_idat(data, new_idat)


def _read_png_lsb(data: bytes):
    if not data.startswith(_PNG_SIGNATURE):
        return None
    info = _png_parse_ihdr(data)
    if not info:
        return None
    width, height, bit_depth, color_type, interlace = info
    if interlace or bit_depth not in (8, 16):
        return None
    row = _png_row_bytes(width, bit_depth, color_type)
    if not row:
        return None
    try:
        raw = zlib.decompress(_png_collect_idat(data))
    except Exception:
        return None
    need_bits = (len(_PNG_LSB_MAGIC) + 1 + 32) * 8
    bits = []
    for y in range(height):
        start = y * (row + 1) + 1
        if start + row > len(raw):
            break
        for x in range(row):
            bits.append(raw[start + x] & 1)
            if len(bits) >= need_bits:
                break
        if len(bits) >= need_bits:
            break
    payload = _bits_to_bytes(bits)
    if not payload.startswith(_PNG_LSB_MAGIC):
        return None
    n = payload[len(_PNG_LSB_MAGIC)]
    start = len(_PNG_LSB_MAGIC) + 1
    if start + n > len(payload):
        return None
    try:
        return payload[start:start + n].decode("latin-1")
    except Exception:
        return None


def _embed_zip_comment(data: bytes, comment: str) -> bytes:
    if len(data) < 22 or data[:2] != b"PK":
        return data
    comment_bytes = comment.encode("utf-8")
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(data):
        return data
    body = data[:eocd + 20]
    return body + struct.pack("<H", len(comment_bytes)) + comment_bytes


def _read_zip_comment(data: bytes):
    if len(data) < 22 or data[:2] != b"PK":
        return None
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(data):
        return None
    comment_len = struct.unpack("<H", data[eocd + 20:eocd + 22])[0]
    raw = data[eocd + 22:eocd + 22 + comment_len]
    if not raw:
        return None
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw.decode("latin-1", "ignore")


def _zip_upsert_extra(extra: bytes, new_field: bytes) -> bytes:
    pos = 0
    parts = []
    replaced = False
    extra = extra or b""
    while pos + 4 <= len(extra):
        hid, size = struct.unpack("<HH", extra[pos:pos + 4])
        end = pos + 4 + size
        if end > len(extra):
            break
        if hid == _ZIP_EXTRA_ID:
            if not replaced:
                parts.append(new_field)
                replaced = True
        else:
            parts.append(extra[pos:end])
        pos = end
    if pos < len(extra):
        parts.append(extra[pos:])
    if not replaced:
        parts.append(new_field)
    return b"".join(parts)


def _embed_zip_extra_and_comment(data: bytes, code: str) -> bytes:
    if len(data) < 22 or data[:2] != b"PK":
        return data
    payload = code.encode("utf-8")
    extra_field = struct.pack("<HH", _ZIP_EXTRA_ID, len(payload)) + payload
    try:
        in_buf = io.BytesIO(data)
        out_buf = io.BytesIO()
        with zipfile.ZipFile(in_buf, "r") as zin:
            with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    raw = zin.read(item.filename)
                    info = zipfile.ZipInfo(filename=item.filename, date_time=item.date_time)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.comment = item.comment
                    info.extra = _zip_upsert_extra(item.extra or b"", extra_field)
                    info.create_system = item.create_system
                    zout.writestr(info, raw)
                zout.comment = payload
        return out_buf.getvalue()
    except Exception:
        return _embed_zip_comment(data, code)


def _read_zip_extra(data: bytes):
    if len(data) < 22 or data[:2] != b"PK":
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            for info in zf.infolist():
                extra = info.extra or b""
                pos = 0
                while pos + 4 <= len(extra):
                    hid, size = struct.unpack("<HH", extra[pos:pos + 4])
                    end = pos + 4 + size
                    if end > len(extra):
                        break
                    if hid == _ZIP_EXTRA_ID:
                        raw = extra[pos + 4:end]
                        try:
                            return raw.decode("utf-8")
                        except Exception:
                            return raw.decode("latin-1", "ignore")
                    pos = end
    except Exception:
        return None
    return None


def _normalize_downloader_code(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    upper = text.upper()
    if "D-" in upper:
        start = upper.find("D-")
        token = "".join(ch for ch in upper[start:] if ch.isalnum() or ch == "-")
        return token or upper
    return text


def _first_downloader_code(*values):
    normalized = []
    for value in values:
        code = _normalize_downloader_code(value)
        if code:
            normalized.append(code)
    for code in normalized:
        if code.startswith("D-"):
            return code
    return normalized[0] if normalized else None


def _embed_downloader_code(file_bytes: bytes, filename: str, code: str) -> bytes:
    name = (filename or "").lower()
    if name.endswith(".png") or file_bytes.startswith(_PNG_SIGNATURE):
        marked = _embed_png_text(file_bytes, _PNG_TEXT_KEY, code)
        return _embed_png_lsb(marked, code)
    if name.endswith(".zip") or file_bytes[:2] == b"PK":
        return _embed_zip_extra_and_comment(file_bytes, code)
    return file_bytes


def _extract_downloader_code_from_bytes(file_bytes: bytes):
    if file_bytes.startswith(_PNG_SIGNATURE):
        return _first_downloader_code(_read_png_text(file_bytes), _read_png_lsb(file_bytes))
    if file_bytes[:2] == b"PK":
        return _first_downloader_code(_read_zip_comment(file_bytes), _read_zip_extra(file_bytes))
    return None


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


def _find_alert_channel(guild: discord.Guild):
    if not guild:
        return None
    for channel in guild.text_channels:
        if ALERT_CHANNEL_KEYWORD in channel.name:
            return channel
    return None


async def _place_alert_channel(channel: discord.TextChannel):
    try:
        if channel.position != 1:
            await channel.edit(position=1)
    except Exception as e:
        logger.warning(f"调整异常提醒频道位置失败: {e}")


async def _ensure_alert_channel_access(channel: discord.TextChannel):
    guild = channel.guild
    try:
        overwrites = channel.overwrites
        overwrites[guild.default_role] = discord.PermissionOverwrite(read_messages=False)
        overwrites[guild.me] = discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )
        if guild.owner:
            overwrites[guild.owner] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        await channel.edit(overwrites=overwrites)
    except Exception as e:
        logger.warning(f"调整异常提醒频道权限失败: {e}")


async def setup_alert_channel():
    for guild in bot.guilds:
        channel = _find_alert_channel(guild)
        if not channel:
            logger.warning(f"{guild.name} 没有「异常提醒」频道，跳过")
            continue
        await _ensure_alert_channel_access(channel)
        await _place_alert_channel(channel)
        try:
            pins = await channel.pins()
            if not any(m.author.id == bot.user.id for m in pins):
                msg = await channel.send("异常提醒频道已就绪。连下、刷屏、广告会分开提醒。仅岛主可见。")
                await msg.pin()
        except Exception as e:
            logger.warning(f"置顶异常提醒说明失败: {e}")


def _alert_cooldown_ok(kind: str, guild_id, user_id) -> bool:
    key = (kind, str(guild_id), str(user_id))
    now = datetime.now(timezone.utc).timestamp()
    last = _alert_last_sent.get(key, 0)
    if now - last < ALERT_COOLDOWN_SEC:
        return False
    _alert_last_sent[key] = now
    return True


async def _send_alert(guild: discord.Guild, kind: str, title: str, description: str, color: discord.Color):
    channel = _find_alert_channel(guild)
    if not channel:
        return
    embed = discord.Embed(
        title=title,
        description=description[:4000],
        color=color,
        timestamp=_beijing_now(),
    )
    embed.set_footer(text=kind)
    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.warning(f"发送异常提醒失败: {e}")


async def _maybe_alert_download(interaction: discord.Interaction, record: dict, file_label: str):
    if not interaction.guild:
        return
    if interaction.user.id == interaction.guild.owner_id:
        return
    now = datetime.now(timezone.utc).timestamp()
    key = (str(interaction.guild.id), str(interaction.user.id))
    bucket = _alert_download_times[key]
    bucket.append(now)
    while bucket and now - bucket[0] > ALERT_DOWNLOAD_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) < ALERT_DOWNLOAD_COUNT:
        return
    if not _alert_cooldown_ok("异常下载", interaction.guild.id, interaction.user.id):
        return
    await _send_alert(
        interaction.guild,
        "异常下载",
        "异常下载",
        (
            f"**用户:** {interaction.user.mention} `{interaction.user.id}`\n"
            f"**资源:** {record.get('name', '?')}\n"
            f"**文件:** {file_label}\n"
            f"**位置:** {interaction.channel.mention}\n"
            f"**原因:** {ALERT_DOWNLOAD_WINDOW_SEC} 秒内领取 {len(bucket)} 次"
        ),
        discord.Color.orange(),
    )


def _looks_like_ad(text: str) -> bool:
    lowered = (text or "").lower()
    for word in AD_KEYWORDS:
        if word.lower() in lowered:
            return True
    if "http://" in lowered or "https://" in lowered:
        if any(x in lowered for x in ("淘宝", "闲鱼", "咸鱼", "微信", "qq", "代充", "出号")):
            return True
    return False


async def _maybe_alert_message(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    if message.author.id == message.guild.owner_id:
        return
    alert_channel = _find_alert_channel(message.guild)
    if alert_channel and message.channel.id == alert_channel.id:
        return
    now = datetime.now(timezone.utc).timestamp()
    key = (str(message.guild.id), str(message.author.id))
    bucket = _alert_message_times[key]
    bucket.append(now)
    while bucket and now - bucket[0] > ALERT_SPAM_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= ALERT_SPAM_COUNT and _alert_cooldown_ok("刷屏", message.guild.id, message.author.id):
        await _send_alert(
            message.guild,
            "刷屏",
            "刷屏",
            (
                f"**用户:** {message.author.mention} `{message.author.id}`\n"
                f"**位置:** {message.channel.mention}\n"
                f"**原因:** {ALERT_SPAM_WINDOW_SEC} 秒内发了 {len(bucket)} 条消息\n"
                f"**最近内容:** {(message.content or '')[:200] or '（无文字）'}"
            ),
            discord.Color.gold(),
        )
    if _looks_like_ad(message.content) and _alert_cooldown_ok("广告", message.guild.id, message.author.id):
        await _send_alert(
            message.guild,
            "广告",
            "广告",
            (
                f"**用户:** {message.author.mention} `{message.author.id}`\n"
                f"**位置:** {message.channel.mention}\n"
                f"**原因:** 消息疑似广告/买卖\n"
                f"**内容:** {(message.content or '')[:500]}"
            ),
            discord.Color.red(),
        )


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
    embed.set_footer(text="仅岛主可见 | 原文件不在频道内 | 点回溯查看领取记录")
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
            dcode = log.get("downloader_code") or "—"
            lines.append(
                f"{_format_beijing_minute(log.get('timestamp'))} "
                f"<@{log.get('downloader_id', 0)}> `{log.get('downloader_id', '?')}` "
                f"`{dcode}` {log.get('file_label') or log.get('file_name') or '?'}"
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

    @discord.ui.button(
        label="领取",
        style=discord.ButtonStyle.secondary,
        custom_id="storage_card_claim",
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_island_owner(interaction):
            await interaction.response.send_message("仅岛主可从存储频道领取。", ephemeral=True)
            return
        file_id, record = _find_record_by_storage_card(interaction.message.id)
        if not record:
            await interaction.response.send_message("找不到对应资源。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        attachments = record.get("attachments") or []
        await _deliver_selected_files(interaction, record, list(range(len(attachments))))


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


gamble_data: dict = {}
gamble_channel_messages: dict = {}


def load_gamble():
    global gamble_data
    try:
        if os.path.exists(GAMBLE_FILE):
            with open(GAMBLE_FILE, "r", encoding="utf-8") as f:
                gamble_data = json.load(f)
    except Exception:
        gamble_data = {}


def save_gamble():
    try:
        with open(GAMBLE_FILE, "w", encoding="utf-8") as f:
            json.dump(gamble_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存赌王数据失败: {e}")


def load_gamble_channels():
    global gamble_channel_messages
    try:
        if os.path.exists(GAMBLE_CHANNEL_FILE):
            with open(GAMBLE_CHANNEL_FILE, "r", encoding="utf-8") as f:
                gamble_channel_messages = json.load(f)
    except Exception:
        gamble_channel_messages = {}


def save_gamble_channels():
    try:
        with open(GAMBLE_CHANNEL_FILE, "w", encoding="utf-8") as f:
            json.dump(gamble_channel_messages, f)
    except Exception as e:
        logger.error(f"保存赌王频道信息失败: {e}")


giveaway_data: dict = {}


def load_giveaways():
    global giveaway_data
    try:
        if os.path.exists(GIVEAWAY_FILE):
            with open(GIVEAWAY_FILE, "r", encoding="utf-8") as f:
                giveaway_data = json.load(f)
    except Exception:
        giveaway_data = {}


def save_giveaways():
    try:
        with open(GIVEAWAY_FILE, "w", encoding="utf-8") as f:
            json.dump(giveaway_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存红包/抽奖券发放失败: {e}")


music_data: dict = {}
_music_selections: dict = {}


def load_music():
    global music_data
    try:
        if os.path.exists(MUSIC_FILE):
            with open(MUSIC_FILE, "r", encoding="utf-8") as f:
                music_data = json.load(f)
    except Exception:
        music_data = {}


def save_music():
    try:
        with open(MUSIC_FILE, "w", encoding="utf-8") as f:
            json.dump(music_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存贝多芬歌单失败: {e}")


story_data: dict = {}
story_channel_messages: dict = {}
_story_pending: dict = {}


def load_stories():
    global story_data, story_channel_messages
    try:
        if os.path.exists(STORY_FILE):
            with open(STORY_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            story_data = raw.get("posts") or {}
            story_channel_messages = raw.get("cards") or {}
            if not story_data and "posts" not in raw and raw:
                story_data = raw
                story_channel_messages = {}
    except Exception:
        story_data = {}
        story_channel_messages = {}


def save_stories():
    try:
        with open(STORY_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"posts": story_data, "cards": story_channel_messages},
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        logger.error(f"保存故事分享失败: {e}")


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
    load_gamble()
    load_gamble_channels()
    load_giveaways()
    load_music()
    load_stories()
    load_reports()
    load_report_channels()
    load_report_counter()
    load_guide_channels()
    load_download_logs()
    load_channel_published()
    _ensure_file_store()
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

    await setup_gamble_channels()

    await setup_music_channels()

    await setup_story_channels()

    # 在举报频道中发布/更新举报按钮消息
    await setup_report_channels()

    # 在指路频道中发布/更新频道导航
    await setup_guide_channels()

    await setup_alert_channel()


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


@bot.tree.command(name="上传文件", description="表单选文件，再设置条件、改名、打包后发布")
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
    await get_or_create_storage_channel(interaction.guild)
    attachment_records = []
    total_size = 0

    for att in attachments:
        try:
            file_bytes = await att.read()
            storage_path = _save_bytes_to_store(file_bytes, att.filename)
            attachment_records.append({
                "original_name": att.filename,
                "custom_name": att.filename,
                "storage_path": storage_path,
                "size": att.size or len(file_bytes),
            })
            total_size += att.size or len(file_bytes)
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
        await _show_draft_setup(interaction, existing_id, has_previous=True)
        return

    draft_id = _new_file_id()
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
        hint = "\n已并入本帖资源。可继续改条件、打包或快速发布；发布后公开卡片才会更新。"

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
    zip_buffer = io.BytesIO()
    file_map = {}

    for att in attachments:
        data = await _read_attachment_bytes(record, att)
        if data is None:
            await interaction.followup.send(f"❌ 附件 {att['custom_name']} 丢失。", ephemeral=True)
            return
        file_map[att["custom_name"]] = data

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in file_map.items():
            zf.writestr(name, data)

    zip_buffer.seek(0)
    zip_bytes = zip_buffer.getvalue()
    total_zip_size = len(zip_bytes)
    zip_name = f"{record['name']}.zip"
    storage_path = _save_bytes_to_store(zip_bytes, zip_name)
    for att in attachments:
        _delete_stored_file(att)

    record["attachments"] = [{
        "original_name": zip_name,
        "custom_name": zip_name,
        "storage_path": storage_path,
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


def _public_base_url() -> str:
    for key in ("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL", "KOYEB_PUBLIC_DOMAIN"):
        value = (os.getenv(key) or "").strip().rstrip("/")
        if not value:
            continue
        if key == "KOYEB_PUBLIC_DOMAIN" and not value.startswith("http"):
            value = "https://" + value
        return value
    fly_app = (os.getenv("FLY_APP_NAME") or "").strip()
    if fly_app:
        return f"https://{fly_app}.fly.dev"
    return ""


def _purge_claim_tokens():
    now = datetime.now(timezone.utc).timestamp()
    expired = [token for token, item in _claim_tokens.items() if item.get("expires_at", 0) <= now]
    for token in expired:
        _claim_tokens.pop(token, None)


def _make_claim_token(filename: str, data: bytes, user_id: int, downloader_code: str) -> str:
    _purge_claim_tokens()
    token = secrets.token_urlsafe(24)
    _claim_tokens[token] = {
        "user_id": int(user_id),
        "downloader_code": downloader_code,
        "filename": filename or "file",
        "data": data,
        "expires_at": datetime.now(timezone.utc).timestamp() + CLAIM_TTL_SEC,
    }
    return token


def _claim_download_url(token: str) -> str:
    base = _public_base_url()
    path = f"/claim/{token}"
    if base:
        return f"{base}{path}"
    return path


def _build_claim_ticket_embed(record: dict, files: list) -> discord.Embed:
    source = record.get("source_channel_id")
    source_txt = f"<#{source}>" if source else "未知"
    lines = [
        f"**作者:** <@{record.get('uploader_id', 0)}> `{record.get('uploader_id', '?')}`",
        f"**资源:** {record.get('name', '?')}",
        f"**来源帖:** {source_txt}",
        "",
        "点下面蓝色文件名下载。图片和文件都不会在这里直接展开。",
        "",
    ]
    for item in files:
        lines.append(f"- [{item['name']}]({item['url']})（{item['size']}）")
    lines.append("")
    lines.append(f"链接 {CLAIM_TTL_SEC // 60} 分钟内有效，不要转发给别人。")
    return discord.Embed(
        title="领取单",
        description="\n".join(lines)[:4096],
        color=discord.Color.blurple(),
    )


async def _deliver_selected_files(interaction: discord.Interaction, record: dict, indices: list):
    if not _public_base_url():
        await interaction.followup.send(
            "领取链接还没配好公网地址。请设置 PUBLIC_BASE_URL 后重启机器人。",
            ephemeral=True,
        )
        return
    attachments = record.get("attachments") or []
    files = []
    for idx in indices:
        if idx < 0 or idx >= len(attachments):
            continue
        att = attachments[idx]
        file_bytes = await _read_attachment_bytes(record, att)
        if file_bytes is None:
            continue
        downloader_code = _get_or_create_downloader_code(record, interaction.user.id)
        filename = att.get("custom_name") or "file"
        marked = _embed_downloader_code(file_bytes, filename, downloader_code)
        token = _make_claim_token(filename, marked, interaction.user.id, downloader_code)
        files.append({
            "name": filename,
            "size": _format_size(att.get("size", 0)),
            "url": _claim_download_url(token),
        })
        _log_download(interaction, record, filename, downloader_code)
        await _maybe_alert_download(interaction, record, filename)
    if not files:
        await interaction.followup.send("未选择有效文件或文件已丢失。", ephemeral=True)
        return
    _mark_claim_cooldown(interaction, _record_id_of(record) or "")
    await interaction.followup.send(
        embed=_build_claim_ticket_embed(record, files),
        ephemeral=True,
    )


def _user_passed_quiz(user) -> bool:
    guild = getattr(user, "guild", None)
    if guild and getattr(user, "id", None) == guild.owner_id:
        return True
    return discord.utils.get(getattr(user, "roles", []), name=QUIZ_VERIFIED_ROLE) is not None


def _claim_cooldown_key(interaction: discord.Interaction, file_id: str) -> tuple:
    guild_id = str(interaction.guild.id) if interaction.guild else "0"
    return (guild_id, str(interaction.user.id), str(file_id))


def _claim_cooldown_left(interaction: discord.Interaction, file_id: str) -> int:
    now = datetime.now(timezone.utc).timestamp()
    until = _download_cooldowns.get(_claim_cooldown_key(interaction, file_id), 0)
    return max(0, int(until - now))


def _mark_claim_cooldown(interaction: discord.Interaction, file_id: str):
    now = datetime.now(timezone.utc).timestamp()
    _download_cooldowns[_claim_cooldown_key(interaction, file_id)] = now + DOWNLOAD_COOLDOWN_SEC


async def _reply_claim(interaction: discord.Interaction, text: str, already_deferred: bool):
    if already_deferred:
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def _gate_claim(interaction: discord.Interaction, file_id: str, record: dict, already_deferred: bool) -> bool:
    if interaction.guild and interaction.user.id == interaction.guild.owner_id:
        return True
    if not _user_passed_quiz(interaction.user):
        await _reply_claim(interaction, "未过审身份不能领取。", already_deferred)
        if interaction.guild and _alert_cooldown_ok("异常下载", interaction.guild.id, interaction.user.id):
            await _send_alert(
                interaction.guild,
                "异常下载",
                "异常下载",
                (
                    f"**用户:** {interaction.user.mention} `{interaction.user.id}`\n"
                    f"**资源:** {record.get('name', '?')}\n"
                    f"**位置:** {interaction.channel.mention}\n"
                    f"**原因:** 未过审身份尝试领取"
                ),
                discord.Color.orange(),
            )
        return False
    left = _claim_cooldown_left(interaction, file_id)
    if left > 0:
        await _reply_claim(interaction, f"领取冷却中，请 {left} 秒后再试。", already_deferred)
        return False
    now = datetime.now(timezone.utc).timestamp()
    key = (str(interaction.guild.id) if interaction.guild else "0", str(interaction.user.id))
    bucket = _alert_download_times[key]
    recent = [t for t in bucket if now - t <= ALERT_DOWNLOAD_WINDOW_SEC]
    if len(recent) >= ALERT_DOWNLOAD_COUNT:
        await _reply_claim(interaction, "领取过于频繁，已拒绝并通知岛主。", already_deferred)
        await _maybe_alert_download(interaction, record, "领取请求")
        return False
    return True


async def _start_download_flow(interaction: discord.Interaction, file_id: str, record: dict, already_deferred: bool = False):
    if not await _gate_claim(interaction, file_id, record, already_deferred):
        return
    failed = await _check_download_prereqs(interaction, record)
    if failed:
        text = "未达到上传者设置的获取条件：" + "，".join(failed)
        await _reply_claim(interaction, text, already_deferred)
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
        if not await _gate_claim(interaction, self.file_id, record, True):
            return
        await _deliver_selected_files(interaction, record, self.indices)


def _log_download(interaction: discord.Interaction, record: dict, file_label: str, downloader_code: str = ""):
    record_id = _record_id_of(record)
    if not downloader_code:
        downloader_code = _get_or_create_downloader_code(record, interaction.user.id)
    download_logs.append({
        "file_id": record.get("published_msg_id", "?"),
        "record_id": record_id,
        "resource_code": record.get("resource_code"),
        "downloader_code": downloader_code,
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


def _find_record_by_resource_code(code: str):
    needle = (code or "").strip().upper()
    if not needle:
        return None, None
    for fid, rec in file_records.items():
        if str(rec.get("resource_code") or "").upper() == needle:
            return fid, rec
    return None, None


def _find_logs_by_downloader_code(code: str) -> list:
    needle = (code or "").strip().upper()
    if not needle:
        return []
    return [log for log in download_logs if str(log.get("downloader_code") or "").upper() == needle]


def _find_logs_by_user_id(user_id: str) -> list:
    needle = str(user_id or "").strip().replace("<@", "").replace(">", "")
    if not needle:
        return []
    return [log for log in download_logs if str(log.get("downloader_id") or "") == needle]


def _build_downloader_trace_embed(code: str, logs: list) -> discord.Embed:
    if logs:
        lines = []
        for log in logs[-20:]:
            lines.append(
                f"{_format_beijing_minute(log.get('timestamp'))} "
                f"<@{log.get('downloader_id', 0)}> `{log.get('downloader_id', '?')}` "
                f"{log.get('file_name') or '?'} / {log.get('file_label') or '?'}"
            )
        body = "\n".join(lines)
    else:
        body = "暂无领取记录"
    return discord.Embed(
        title="下载人回溯",
        description=f"**下载人码:** `{code}`\n\n**领取记录**\n{body}"[:4000],
        color=discord.Color.orange(),
    )


def _build_user_trace_embed(user_id: str, logs: list) -> discord.Embed:
    if logs:
        lines = []
        for log in logs[-20:]:
            lines.append(
                f"{_format_beijing_minute(log.get('timestamp'))} "
                f"`{log.get('downloader_code') or '—'}` "
                f"{log.get('file_name') or '?'} / {log.get('file_label') or '?'}"
            )
        body = "\n".join(lines)
    else:
        body = "暂无领取记录"
    return discord.Embed(
        title="用户回溯",
        description=f"**用户:** <@{user_id}> `{user_id}`\n\n**领取记录**\n{body}"[:4000],
        color=discord.Color.orange(),
    )


@bot.tree.command(name="回溯", description="按资源码、下载人码、用户ID或流出文件回溯（仅岛主）")
@app_commands.describe(
    查询="资源回溯码、下载人码或用户ID",
    流出文件="把流出的 PNG/ZIP 丢进来抠下载人码",
)
async def trace_resource(
    interaction: discord.Interaction,
    查询: Optional[str] = None,
    流出文件: Optional[discord.Attachment] = None,
):
    if not _is_island_owner(interaction):
        await interaction.response.send_message("仅岛主可回溯。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    query = (查询 or "").strip()
    if 流出文件:
        try:
            file_bytes = await 流出文件.read()
        except Exception as e:
            await interaction.followup.send(f"读取流出文件失败: {e}", ephemeral=True)
            return
        extracted = _extract_downloader_code_from_bytes(file_bytes)
        if not extracted:
            await interaction.followup.send("这份文件里没有抠到下载人码。", ephemeral=True)
            return
        logs = _find_logs_by_downloader_code(extracted)
        await interaction.followup.send(
            content=f"从文件抠到下载人码 `{extracted}`",
            embed=_build_downloader_trace_embed(extracted, logs),
            ephemeral=True,
        )
        return
    if not query:
        await interaction.followup.send(
            "请填写资源回溯码、下载人码、用户ID，或附上流出文件。",
            ephemeral=True,
        )
        return
    upper = query.upper()
    if upper.startswith("R-"):
        file_id, record = _find_record_by_resource_code(upper)
        if not record:
            await interaction.followup.send("找不到这个资源回溯码。", ephemeral=True)
            return
        await interaction.followup.send(
            embed=_build_resource_trace_embed(file_id, record),
            ephemeral=True,
        )
        return
    if upper.startswith("D-"):
        logs = _find_logs_by_downloader_code(upper)
        await interaction.followup.send(
            embed=_build_downloader_trace_embed(upper, logs),
            ephemeral=True,
        )
        return
    digits = query.replace("<@", "").replace(">", "").strip()
    if digits.isdigit():
        logs = _find_logs_by_user_id(digits)
        await interaction.followup.send(
            embed=_build_user_trace_embed(digits, logs),
            ephemeral=True,
        )
        return
    file_id, record = _find_record_by_resource_code(upper)
    if record:
        await interaction.followup.send(
            embed=_build_resource_trace_embed(file_id, record),
            ephemeral=True,
        )
        return
    logs = _find_logs_by_downloader_code(upper)
    if logs:
        await interaction.followup.send(
            embed=_build_downloader_trace_embed(upper, logs),
            ephemeral=True,
        )
        return
    await interaction.followup.send("没有匹配到资源码、下载人码或用户ID。", ephemeral=True)


async def _get_first_floor(channel) -> Optional[discord.Message]:
    if channel is None:
        return None
    if isinstance(channel, discord.Thread):
        starter = getattr(channel, "starter_message", None)
        if starter:
            return starter
        try:
            return await channel.fetch_message(channel.id)
        except Exception:
            pass
    try:
        async for first_msg in channel.history(oldest_first=True, limit=1):
            return first_msg
    except Exception:
        return None
    return None


async def _check_user_liked_first(interaction: discord.Interaction) -> bool:
    try:
        first_msg = await _get_first_floor(interaction.channel)
        if not first_msg:
            return False
        if not first_msg.reactions:
            try:
                first_msg = await interaction.channel.fetch_message(first_msg.id)
            except Exception:
                pass
        for reaction in first_msg.reactions:
            try:
                async for user in reaction.users(limit=None):
                    if user.id == interaction.user.id:
                        return True
            except Exception:
                continue
        return False
    except Exception as e:
        logger.error(f"检查首楼点赞失败: {e}")
        return False


async def _check_user_comment_length(interaction: discord.Interaction, min_length: int) -> bool:
    try:
        first_msg = await _get_first_floor(interaction.channel)
        if not first_msg:
            return False
        need = max(int(min_length or 1), 1)
        async for msg in interaction.channel.history(after=first_msg, limit=300):
            if msg.author.id != interaction.user.id or msg.author.bot:
                continue
            text = (msg.content or "").strip()
            if len(text) >= need:
                return True
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

    if MUSIC_CHANNEL_KEYWORD in (getattr(message.channel, "name", "") or ""):
        ingested = await _ingest_music_message(message)
        if ingested:
            return

    if STORY_CHANNEL_KEYWORD in (getattr(message.channel, "name", "") or ""):
        ingested = await _ingest_story_message(message)
        if ingested:
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

    await _maybe_alert_message(message)

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
#  赌王来一下 - 积分/抽奖券抽奖
# ═══════════════════════════════════════════

GAMBLE_PRIZE_ROLES = ("水仙十字",)
GAMBLE_JUNK_POOL = (
    {"key": "thanks", "name": "谢谢惠顾", "kind": "flavor"},
    {"key": "tissue", "name": "一卷抽卷纸", "kind": "flavor"},
    {"key": "fish", "name": "一条咸鱼", "kind": "flavor"},
    {"key": "shell", "name": "空蚌壳", "kind": "flavor"},
    {"key": "pebble", "name": "潮汐石子", "kind": "flavor"},
    {"key": "receipt", "name": "过期小票", "kind": "flavor"},
    {"key": "fragment", "name": "抽奖券碎片", "kind": "fragment"},
)
GAMBLE_JUNK_WEIGHTS = {
    "thanks": 42,
    "tissue": 10,
    "fish": 10,
    "shell": 8,
    "pebble": 8,
    "receipt": 7,
    "fragment": 15,
}
GAMBLE_ROLE_RATE = {
    "points": 0.001,
    "ticket": 0.007,
}


def _empty_gamble_record() -> dict:
    return {
        "tickets": 0,
        "fragments": 0,
        "owned_roles": [],
        "history": [],
        "junk": {},
    }


def _get_gamble_record(guild_id: str, user_id: str) -> dict:
    guild_data = gamble_data.setdefault(str(guild_id), {})
    rec = guild_data.get(str(user_id)) or {}
    data = _empty_gamble_record()
    data.update(rec)
    data["tickets"] = int(data.get("tickets") or 0)
    data["fragments"] = int(data.get("fragments") or 0)
    data["owned_roles"] = list(data.get("owned_roles") or [])
    data["history"] = list(data.get("history") or [])
    data["junk"] = dict(data.get("junk") or {})
    guild_data[str(user_id)] = data
    return data


def _gamble_embed() -> discord.Embed:
    embed = discord.Embed(
        title="赌王来一下",
        description=(
            "小岛夜场开张。没过审也能抽。\n\n"
            f"**积分抽奖** 单抽 {GAMBLE_DRAW_COST_POINTS} 积分，10 连 {GAMBLE_DRAW_COST_POINTS * GAMBLE_DRAW_TEN} 积分，身份组概率 0.1%\n"
            f"**抽奖券抽奖** 单抽 1 张券，10 连 {GAMBLE_DRAW_TEN} 张券，身份组概率 0.7%\n"
            f"**碎片合成** {GAMBLE_FRAGMENT_PER_TICKET} 个碎片换 1 张抽奖券\n\n"
            "大奖先开放：**水仙十字**（永久，可随时佩戴/卸下）\n"
            "已经抽到的身份组不会再抽到。\n"
            "没中大奖时可能是谢谢惠顾、碎片、抽卷纸、咸鱼一类小玩意。"
        ),
        color=discord.Color.dark_magenta(),
    )
    embed.set_footer(text="先选单抽或 10 连 | 结果仅自己可见 | 身份组可随时切换佩戴")
    return embed


async def _ensure_prize_role(guild: discord.Guild, name: str):
    role = discord.utils.get(guild.roles, name=name)
    if role:
        return role
    try:
        return await guild.create_role(
            name=name,
            mentionable=False,
            hoist=False,
            reason="赌王来一下大奖身份组",
        )
    except Exception as e:
        logger.warning(f"创建奖品身份组 {name} 失败: {e}")
        return None


def _owned_prize_roles(record: dict) -> list:
    owned = []
    for name in GAMBLE_PRIZE_ROLES:
        if name in (record.get("owned_roles") or []):
            owned.append(name)
    return owned


def _available_prize_roles(record: dict) -> list:
    owned = set(record.get("owned_roles") or [])
    return [name for name in GAMBLE_PRIZE_ROLES if name not in owned]


def _pick_junk() -> dict:
    names = [item["key"] for item in GAMBLE_JUNK_POOL]
    weights = [GAMBLE_JUNK_WEIGHTS.get(item["key"], 1) for item in GAMBLE_JUNK_POOL]
    key = random.choices(names, weights=weights, k=1)[0]
    for item in GAMBLE_JUNK_POOL:
        if item["key"] == key:
            return item
    return GAMBLE_JUNK_POOL[0]


def _append_history(record: dict, text: str):
    record.setdefault("history", []).append({
        "time": _beijing_now().isoformat(),
        "text": text,
    })
    record["history"] = record["history"][-30:]


async def _roll_gamble(interaction: discord.Interaction, source: str) -> str:
    record = _get_gamble_record(str(interaction.guild.id), str(interaction.user.id))
    rate = GAMBLE_ROLE_RATE[source]
    available = _available_prize_roles(record)
    if available and random.random() < rate:
        prize = random.choice(available)
        record["owned_roles"].append(prize)
        _append_history(record, f"大奖：{prize}")
        save_gamble()
        role = await _ensure_prize_role(interaction.guild, prize)
        if role:
            try:
                await interaction.user.add_roles(role, reason="赌王来一下抽中身份组")
            except Exception as e:
                logger.warning(f"发放身份组 {prize} 失败: {e}")
        return (
            f"中了。永久身份组 **{prize}** 已经到账。\n"
            "点「获得奖品」可以佩戴或卸下。"
        )
    junk = _pick_junk()
    if junk["kind"] == "fragment":
        record["fragments"] = int(record.get("fragments") or 0) + 1
        _append_history(record, "抽奖券碎片 +1")
        save_gamble()
        return (
            f"摸到 **抽奖券碎片** ×1。\n"
            f"当前碎片 **{record['fragments']}** / {GAMBLE_FRAGMENT_PER_TICKET}，"
            "集齐可合成抽奖券。"
        )
    junk_bag = record.setdefault("junk", {})
    junk_bag[junk["key"]] = int(junk_bag.get(junk["key"]) or 0) + 1
    _append_history(record, junk["name"])
    save_gamble()
    flavor = {
        "thanks": "台面空空，庄家朝你点了点头。",
        "tissue": "递过来一卷抽卷纸。大概用得上。",
        "fish": "一条咸鱼拍在桌上，还在反光。",
        "shell": "空蚌壳一枚。里面没有珍珠。",
        "pebble": "潮汐石子一颗，口袋里会响。",
        "receipt": "一张过期小票。金额看不清。",
    }.get(junk["key"], "")
    return f"抽到：**{junk['name']}**\n{flavor}"


def _short_gamble_line(text: str) -> str:
    first = (text or "").split("\n", 1)[0].strip()
    return first[:80] or "抽到一份小玩意"


async def _do_points_draw(interaction: discord.Interaction, times: int = 1):
    times = 1 if times != GAMBLE_DRAW_TEN else GAMBLE_DRAW_TEN
    cost = GAMBLE_DRAW_COST_POINTS * times
    user_data = _get_checkin_record(str(interaction.guild.id), str(interaction.user.id))
    if user_data["points"] < cost:
        await interaction.response.send_message(
            f"积分不够。{times} 连需要 {cost}，当前 **{user_data['points']}**。",
            ephemeral=True,
        )
        return
    user_data["points"] -= cost
    save_points()
    lines = []
    last_full = ""
    for i in range(times):
        result = await _roll_gamble(interaction, "points")
        last_full = result
        lines.append(f"{i + 1}. {_short_gamble_line(result)}")
    rec = _get_gamble_record(str(interaction.guild.id), str(interaction.user.id))
    body = last_full if times == 1 else "\n".join(lines)
    await interaction.response.send_message(
        f"{body}\n剩余积分 **{user_data['points']}**｜抽奖券 **{rec['tickets']}**｜碎片 **{rec['fragments']}**",
        ephemeral=True,
    )


async def _do_ticket_draw(interaction: discord.Interaction, times: int = 1):
    times = 1 if times != GAMBLE_DRAW_TEN else GAMBLE_DRAW_TEN
    rec = _get_gamble_record(str(interaction.guild.id), str(interaction.user.id))
    if rec["tickets"] < times:
        await interaction.response.send_message(
            f"抽奖券不够。{times} 连需要 {times} 张，当前 **{rec['tickets']}**。碎片 **{rec['fragments']}** / {GAMBLE_FRAGMENT_PER_TICKET}。",
            ephemeral=True,
        )
        return
    rec["tickets"] -= times
    save_gamble()
    lines = []
    last_full = ""
    for i in range(times):
        result = await _roll_gamble(interaction, "ticket")
        last_full = result
        lines.append(f"{i + 1}. {_short_gamble_line(result)}")
    rec = _get_gamble_record(str(interaction.guild.id), str(interaction.user.id))
    body = last_full if times == 1 else "\n".join(lines)
    await interaction.response.send_message(
        f"{body}\n剩余抽奖券 **{rec['tickets']}**｜碎片 **{rec['fragments']}**",
        ephemeral=True,
    )


async def _do_craft_ticket(interaction: discord.Interaction):
    rec = _get_gamble_record(str(interaction.guild.id), str(interaction.user.id))
    if rec["fragments"] < GAMBLE_FRAGMENT_PER_TICKET:
        await interaction.response.send_message(
            f"碎片不够。需要 {GAMBLE_FRAGMENT_PER_TICKET}，当前 **{rec['fragments']}**。",
            ephemeral=True,
        )
        return
    rec["fragments"] -= GAMBLE_FRAGMENT_PER_TICKET
    rec["tickets"] += 1
    _append_history(rec, "碎片合成抽奖券 ×1")
    save_gamble()
    await interaction.response.send_message(
        f"合成成功。抽奖券 **{rec['tickets']}**｜碎片 **{rec['fragments']}**",
        ephemeral=True,
    )


class PrizeToggleView(discord.ui.View):
    def __init__(self, owned: list):
        super().__init__(timeout=120)
        for name in owned[:5]:
            self.add_item(PrizeToggleButton(name))


class PrizeToggleButton(discord.ui.Button):
    def __init__(self, role_name: str):
        super().__init__(
            label=f"切换 {role_name}",
            style=discord.ButtonStyle.secondary,
        )
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction):
        rec = _get_gamble_record(str(interaction.guild.id), str(interaction.user.id))
        if self.role_name not in (rec.get("owned_roles") or []):
            await interaction.response.send_message("你还没有这个身份组。", ephemeral=True)
            return
        role = discord.utils.get(interaction.guild.roles, name=self.role_name)
        if not role:
            role = await _ensure_prize_role(interaction.guild, self.role_name)
        if not role:
            await interaction.response.send_message("找不到这个身份组。", ephemeral=True)
            return
        member = interaction.user
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="赌王来一下卸下身份组")
            except Exception as e:
                await interaction.response.send_message(f"卸下失败: {e}", ephemeral=True)
                return
            await interaction.response.send_message(f"已卸下 **{self.role_name}**。", ephemeral=True)
            return
        try:
            await member.add_roles(role, reason="赌王来一下佩戴身份组")
        except Exception as e:
            await interaction.response.send_message(f"佩戴失败: {e}", ephemeral=True)
            return
        await interaction.response.send_message(f"已佩戴 **{self.role_name}**。", ephemeral=True)


async def _do_show_prizes(interaction: discord.Interaction):
    rec = _get_gamble_record(str(interaction.guild.id), str(interaction.user.id))
    user_data = _get_checkin_record(str(interaction.guild.id), str(interaction.user.id))
    owned = _owned_prize_roles(rec)
    junk_lines = []
    for item in GAMBLE_JUNK_POOL:
        count = int((rec.get("junk") or {}).get(item["key"]) or 0)
        if count:
            junk_lines.append(f"{item['name']} ×{count}")
    hist = rec.get("history") or []
    hist_text = "\n".join(
        f"{_format_beijing_minute(item.get('time'))} {item.get('text')}"
        for item in hist[-8:]
    ) or "还没抽过"
    desc = (
        f"**积分:** {user_data['points']}\n"
        f"**抽奖券:** {rec['tickets']}\n"
        f"**碎片:** {rec['fragments']} / {GAMBLE_FRAGMENT_PER_TICKET}\n"
        f"**身份组:** {'、'.join(owned) if owned else '暂无'}\n"
        f"**小玩意:** {'、'.join(junk_lines) if junk_lines else '暂无'}\n\n"
        f"**最近记录**\n{hist_text}"
    )
    embed = discord.Embed(title="我的奖品", description=desc[:4000], color=discord.Color.magenta())
    view = PrizeToggleView(owned) if owned else None
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class GambleTimesView(discord.ui.View):
    def __init__(self, source: str):
        super().__init__(timeout=60)
        self.source = source

    @discord.ui.button(label="单抽", style=discord.ButtonStyle.primary)
    async def draw_one(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.source == "ticket":
            await _do_ticket_draw(interaction, 1)
        else:
            await _do_points_draw(interaction, 1)

    @discord.ui.button(label="10连", style=discord.ButtonStyle.success)
    async def draw_ten(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.source == "ticket":
            await _do_ticket_draw(interaction, GAMBLE_DRAW_TEN)
        else:
            await _do_points_draw(interaction, GAMBLE_DRAW_TEN)


async def _ask_gamble_times(interaction: discord.Interaction, source: str):
    if source == "ticket":
        rec = _get_gamble_record(str(interaction.guild.id), str(interaction.user.id))
        hint = f"当前抽奖券 **{rec['tickets']}**。单抽 1 张，10 连 {GAMBLE_DRAW_TEN} 张。"
    else:
        user_data = _get_checkin_record(str(interaction.guild.id), str(interaction.user.id))
        hint = (
            f"当前积分 **{user_data['points']}**。"
            f"单抽 {GAMBLE_DRAW_COST_POINTS}，10 连 {GAMBLE_DRAW_COST_POINTS * GAMBLE_DRAW_TEN}。"
        )
    title = "抽奖券抽奖" if source == "ticket" else "积分抽奖"
    await interaction.response.send_message(
        f"{title}：选单抽还是 10 连。\n{hint}",
        view=GambleTimesView(source),
        ephemeral=True,
    )


class PersistentGambleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="积分抽奖", style=discord.ButtonStyle.primary, custom_id="gamble_points")
    async def points_draw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _ask_gamble_times(interaction, "points")

    @discord.ui.button(label="抽奖券抽奖", style=discord.ButtonStyle.success, custom_id="gamble_ticket")
    async def ticket_draw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _ask_gamble_times(interaction, "ticket")

    @discord.ui.button(label="获得奖品", style=discord.ButtonStyle.secondary, custom_id="gamble_prizes")
    async def show_prizes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_show_prizes(interaction)

    @discord.ui.button(label="碎片合成", style=discord.ButtonStyle.secondary, custom_id="gamble_craft")
    async def craft_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_craft_ticket(interaction)


async def setup_gamble_channels():
    embed = _gamble_embed()
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if GAMBLE_CHANNEL_KEYWORD not in channel.name:
                continue
            try:
                existing_msg_id = gamble_channel_messages.get(str(channel.id))
                kept = False
                async for old_msg in channel.history(limit=50):
                    if old_msg.author.id != bot.user.id:
                        continue
                    title = old_msg.embeds[0].title if old_msg.embeds else ""
                    if existing_msg_id and str(old_msg.id) == existing_msg_id:
                        try:
                            await old_msg.edit(embed=embed, view=PersistentGambleView())
                            kept = True
                            logger.info(f"更新赌王卡片: #{channel.name}")
                        except Exception:
                            pass
                    elif title == "赌王来一下":
                        try:
                            await old_msg.delete()
                        except Exception:
                            pass
                if not kept:
                    msg = await channel.send(embed=embed, view=PersistentGambleView())
                    gamble_channel_messages[str(channel.id)] = str(msg.id)
                    save_gamble_channels()
                    logger.info(f"发布赌王卡片: #{channel.name}")
            except discord.Forbidden:
                logger.warning(f"无权限在 #{channel.name} 发送消息")
            except Exception as e:
                logger.error(f"赌王频道 #{channel.name} 设置失败: {e}")


def _in_gamble_channel(interaction: discord.Interaction) -> bool:
    channel = interaction.channel
    names = []
    if channel is not None:
        names.append(getattr(channel, "name", "") or "")
        parent = getattr(channel, "parent", None)
        if parent is not None:
            names.append(getattr(parent, "name", "") or "")
    return any(GAMBLE_CHANNEL_KEYWORD in name for name in names)


def _require_owner_gamble(interaction: discord.Interaction):
    if not _is_island_owner(interaction):
        return "只有岛主能用这个指令。"
    if not _in_gamble_channel(interaction):
        return f"请到名称含「{GAMBLE_CHANNEL_KEYWORD}」的频道使用。"
    return ""


def _add_user_points(guild_id: str, user_id: str, amount: int) -> int:
    rec = _get_checkin_record(str(guild_id), str(user_id))
    rec["points"] = int(rec.get("points") or 0) + int(amount)
    save_points()
    return rec["points"]


def _add_user_tickets(guild_id: str, user_id: str, amount: int) -> int:
    rec = _get_gamble_record(str(guild_id), str(user_id))
    rec["tickets"] = int(rec.get("tickets") or 0) + int(amount)
    save_gamble()
    return rec["tickets"]


def _split_redpacket(total: int, people: int) -> list:
    remaining_points = total
    remaining_people = people
    parts = []
    for _ in range(people - 1):
        avg = remaining_points / remaining_people
        high = max(1, min(remaining_points - (remaining_people - 1), int(avg * 2)))
        got = random.randint(1, high)
        parts.append(got)
        remaining_points -= got
        remaining_people -= 1
    parts.append(remaining_points)
    random.shuffle(parts)
    return parts


def _parse_giveaway_deadline(text: str):
    raw = (text or "").strip()
    if not raw:
        return None
    raw = raw.replace("年", "-").replace("月", "-").replace("日", " ").replace("号", " ")
    raw = raw.replace("点", ":00").replace("：", ":")
    raw = " ".join(raw.split())
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if parsed.year == 1900:
                parsed = parsed.replace(year=_beijing_now().year)
            return parsed.replace(tzinfo=BEIJING_TZ)
        except ValueError:
            continue
    raise ValueError("截止时间请写成 2026-09-06 22:00")


def _giveaway_expired(rec: dict) -> bool:
    expire_at = rec.get("expire_at") or ""
    if not expire_at:
        return False
    try:
        deadline = datetime.fromisoformat(expire_at)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=BEIJING_TZ)
        return _beijing_now() >= deadline
    except Exception:
        return False


async def _fetch_guild_member(guild: discord.Guild, user_id: int):
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


def _member_name_hits(member, token: str) -> bool:
    names = [
        (member.display_name or "").lower(),
        (member.name or "").lower(),
        (getattr(member, "global_name", None) or "").lower(),
    ]
    return any(token and token in name for name in names)


async def _resolve_giveaway_users(guild: discord.Guild, text: str) -> list:
    raw = (text or "").strip()
    if not raw:
        return []
    found = []
    seen = set()
    tokens = [part.strip() for part in re.split(r"[,，\n\s]+", raw) if part.strip()]

    def add_member(member):
        if member is None:
            return
        key = str(member.id)
        if key in seen:
            return
        seen.add(key)
        found.append(member)

    leftovers = []
    for token in tokens:
        mention = re.fullmatch(r"<@!?(\d+)>", token)
        if mention:
            add_member(await _fetch_guild_member(guild, int(mention.group(1))))
            continue
        if token.isdigit():
            add_member(await _fetch_guild_member(guild, int(token)))
            continue
        leftovers.append(token.lower())
    for token in leftovers:
        matches = [m for m in guild.members if _member_name_hits(m, token)]
        exact = [
            m for m in matches
            if token in (
                (m.display_name or "").lower(),
                (m.name or "").lower(),
                (getattr(m, "global_name", None) or "").lower(),
            )
        ]
        if exact:
            matches = exact
        if not matches:
            try:
                queried = await guild.query_members(query=token, limit=5)
            except Exception:
                queried = []
            matches = [m for m in queried if _member_name_hits(m, token)] or list(queried)
        if len(matches) == 1:
            add_member(matches[0])
        elif not matches:
            raise ValueError(f"找不到领取人：{token}")
        else:
            raise ValueError(f"「{token}」对上了多人，请改用 @ 或用户ID")
    return found


def _new_giveaway_id() -> str:
    for _ in range(16):
        gid = secrets.token_hex(6)
        if gid not in giveaway_data:
            return gid
    return secrets.token_hex(8)


def _find_giveaway_by_message(message_id) -> tuple:
    target = str(message_id)
    for gid, rec in giveaway_data.items():
        if str(rec.get("message_id") or "") == target:
            return gid, rec
    return None, None


def _redpacket_embed(rec: dict) -> discord.Embed:
    claimed = rec.get("claimed") or {}
    remaining = rec.get("remaining") or []
    total = int(rec.get("total_points") or 0)
    slots = int(rec.get("slots") or 0)
    taken = len(claimed)
    if remaining:
        status = f"还剩 **{len(remaining)}** 份，点拆开随机拿积分。"
        color = discord.Color.red()
    else:
        status = "已经抢完了。"
        color = discord.Color.dark_grey()
    desc = (
        f"**总分:** {total}\n"
        f"**人数:** {slots}\n"
        f"**已拆:** {taken}/{slots}\n\n"
        f"{status}"
    )
    embed = discord.Embed(title="积分红包", description=desc, color=color)
    embed.set_footer(text="每人只能拆一次 | 抢完后再点会提示已经没有了")
    return embed


def _ticket_give_embed(rec: dict) -> discord.Embed:
    claimed = rec.get("claimed") or {}
    amount = int(rec.get("ticket_amount") or 0)
    mode = rec.get("mode") or "all"
    expire_at = rec.get("expire_at") or ""
    expire_txt = "无"
    if expire_at:
        try:
            deadline = datetime.fromisoformat(expire_at)
            expire_txt = deadline.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
        except Exception:
            expire_txt = expire_at
    if mode == "all":
        target_txt = "全员，每人可领一次"
    else:
        allowed = rec.get("allowed_ids") or []
        target_txt = " ".join(f"<@{uid}>" for uid in allowed) or "指定领取人"
    if _giveaway_expired(rec):
        status = "已经过期，不能再领。"
        color = discord.Color.dark_grey()
    else:
        status = "点领取拿抽奖券。过了截止时间就领不到了。"
        color = discord.Color.green()
    desc = (
        f"**每人张数:** {amount}\n"
        f"**领取对象:** {target_txt}\n"
        f"**截止:** {expire_txt}\n"
        f"**已领取:** {len(claimed)}\n\n"
        f"{status}"
    )
    embed = discord.Embed(title="抽奖券发放", description=desc[:4000], color=color)
    embed.set_footer(text="指定的人才能领 | 每人一次")
    return embed


class PersistentRedPacketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="拆开", style=discord.ButtonStyle.danger, custom_id="giveaway_redpacket_open")
    async def open_packet(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid, rec = _find_giveaway_by_message(interaction.message.id)
        if not rec:
            await interaction.response.send_message("这个红包已经失效。", ephemeral=True)
            return
        user_id = str(interaction.user.id)
        claimed = rec.setdefault("claimed", {})
        remaining = rec.setdefault("remaining", [])
        if user_id in claimed:
            await interaction.response.send_message(
                f"你已经拆过了，当时拿到 **{claimed[user_id]}** 积分。",
                ephemeral=True,
            )
            return
        if not remaining:
            await interaction.response.send_message("已经没有了。", ephemeral=True)
            return
        got = remaining.pop(0)
        claimed[user_id] = got
        save_giveaways()
        total_now = _add_user_points(rec.get("guild_id"), user_id, got)
        try:
            await interaction.message.edit(embed=_redpacket_embed(rec), view=PersistentRedPacketView())
        except Exception:
            pass
        await interaction.response.send_message(
            f"拆开红包，拿到 **{got}** 积分。当前积分 **{total_now}**。",
            ephemeral=True,
        )


class PersistentTicketGiveView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="领取", style=discord.ButtonStyle.success, custom_id="giveaway_ticket_claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid, rec = _find_giveaway_by_message(interaction.message.id)
        if not rec:
            await interaction.response.send_message("这次发放已经失效。", ephemeral=True)
            return
        user_id = str(interaction.user.id)
        claimed = rec.setdefault("claimed", {})
        if _giveaway_expired(rec):
            rec["status"] = "expired"
            save_giveaways()
            try:
                await interaction.message.edit(embed=_ticket_give_embed(rec), view=PersistentTicketGiveView())
            except Exception:
                pass
            await interaction.response.send_message("已经过期了。", ephemeral=True)
            return
        allowed = rec.get("allowed_ids") or []
        if rec.get("mode") != "all" and user_id not in [str(x) for x in allowed]:
            await interaction.response.send_message("这不是给你的，领不了。", ephemeral=True)
            return
        if user_id in claimed:
            await interaction.response.send_message(
                f"你已经领过了，当时拿到 **{claimed[user_id]}** 张抽奖券。",
                ephemeral=True,
            )
            return
        amount = int(rec.get("ticket_amount") or 0)
        claimed[user_id] = amount
        save_giveaways()
        total_now = _add_user_tickets(rec.get("guild_id"), user_id, amount)
        try:
            await interaction.message.edit(embed=_ticket_give_embed(rec), view=PersistentTicketGiveView())
        except Exception:
            pass
        await interaction.response.send_message(
            f"领取成功，拿到 **{amount}** 张抽奖券。当前抽奖券 **{total_now}**。",
            ephemeral=True,
        )


class RedPacketModal(discord.ui.Modal, title="发积分红包"):
    def __init__(self):
        super().__init__()
        self.total_input = discord.ui.TextInput(
            label="总分",
            placeholder="例如 100",
            required=True,
            max_length=8,
        )
        self.people_input = discord.ui.TextInput(
            label="允许领取人数",
            placeholder="例如 5",
            required=True,
            max_length=4,
        )
        self.add_item(self.total_input)
        self.add_item(self.people_input)

    async def on_submit(self, interaction: discord.Interaction):
        err = _require_owner_gamble(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        try:
            total = int(str(self.total_input.value).strip())
            people = int(str(self.people_input.value).strip())
        except ValueError:
            await interaction.response.send_message("总分和人数都要填整数。", ephemeral=True)
            return
        if total < 1 or people < 1:
            await interaction.response.send_message("总分和人数都要大于 0。", ephemeral=True)
            return
        if people > 50:
            await interaction.response.send_message("一次最多 50 人抢。", ephemeral=True)
            return
        if total < people:
            await interaction.response.send_message("总分不能少于人数，每人至少 1 积分。", ephemeral=True)
            return
        parts = _split_redpacket(total, people)
        gid = _new_giveaway_id()
        rec = {
            "kind": "redpacket",
            "guild_id": str(interaction.guild.id),
            "channel_id": str(interaction.channel.id),
            "message_id": "",
            "total_points": total,
            "slots": people,
            "remaining": parts,
            "claimed": {},
            "status": "open",
        }
        giveaway_data[gid] = rec
        await interaction.response.send_message("红包已发出。", ephemeral=True)
        msg = await interaction.channel.send(embed=_redpacket_embed(rec), view=PersistentRedPacketView())
        rec["message_id"] = str(msg.id)
        save_giveaways()


class TicketGiveModal(discord.ui.Modal, title="发抽奖券"):
    def __init__(self):
        super().__init__()
        self.amount_input = discord.ui.TextInput(
            label="每人发放张数",
            placeholder="例如 2",
            required=True,
            max_length=4,
        )
        self.mode_input = discord.ui.TextInput(
            label="发放方式",
            placeholder="一人 / 多人 / 全员",
            required=True,
            max_length=8,
        )
        self.targets_input = discord.ui.TextInput(
            label="领取人",
            placeholder="一人或多人时填 @用户、ID 或名字，全员可空",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=400,
        )
        self.deadline_input = discord.ui.TextInput(
            label="截止时间",
            placeholder="2026-09-06 22:00，可空表示不限期",
            required=False,
            max_length=32,
        )
        self.add_item(self.amount_input)
        self.add_item(self.mode_input)
        self.add_item(self.targets_input)
        self.add_item(self.deadline_input)

    async def on_submit(self, interaction: discord.Interaction):
        err = _require_owner_gamble(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        try:
            amount = int(str(self.amount_input.value).strip())
        except ValueError:
            await interaction.response.send_message("张数要填整数。", ephemeral=True)
            return
        if amount < 1 or amount > 99:
            await interaction.response.send_message("张数请填 1 到 99。", ephemeral=True)
            return
        mode_raw = str(self.mode_input.value or "").strip()
        if mode_raw in ("全员", "所有人", "统一分发"):
            mode = "all"
            members = []
        elif mode_raw in ("一人", "多人", "指定", "指定一人", "指定多人"):
            mode = "specific"
            try:
                members = await _resolve_giveaway_users(interaction.guild, str(self.targets_input.value or ""))
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            if not members:
                await interaction.response.send_message("指定发放时请填写领取人。", ephemeral=True)
                return
        else:
            await interaction.response.send_message("发放方式请填：一人、多人或全员。", ephemeral=True)
            return
        try:
            deadline = _parse_giveaway_deadline(str(self.deadline_input.value or ""))
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        gid = _new_giveaway_id()
        rec = {
            "kind": "ticket",
            "guild_id": str(interaction.guild.id),
            "channel_id": str(interaction.channel.id),
            "message_id": "",
            "ticket_amount": amount,
            "mode": mode,
            "allowed_ids": [str(m.id) for m in members],
            "expire_at": deadline.isoformat() if deadline else "",
            "claimed": {},
            "status": "open",
        }
        giveaway_data[gid] = rec
        mention = " ".join(m.mention for m in members)
        await interaction.response.send_message("抽奖券发放已发出。", ephemeral=True)
        msg = await interaction.channel.send(
            content=mention or None,
            embed=_ticket_give_embed(rec),
            view=PersistentTicketGiveView(),
        )
        rec["message_id"] = str(msg.id)
        save_giveaways()


@bot.tree.command(name="积分红包", description="岛主在赌王频道发积分红包")
async def redpacket_command(interaction: discord.Interaction):
    err = _require_owner_gamble(interaction)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return
    await interaction.response.send_modal(RedPacketModal())


@bot.tree.command(name="抽奖券", description="岛主在赌王频道发放抽奖券")
async def ticket_give_command(interaction: discord.Interaction):
    err = _require_owner_gamble(interaction)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return
    await interaction.response.send_modal(TicketGiveModal())


# ═══════════════════════════════════════════
#  贝多芬时代 - 文字频道点唱机
# ═══════════════════════════════════════════

def _music_channel_of(guild: discord.Guild):
    for channel in getattr(guild, "text_channels", []) or []:
        if MUSIC_CHANNEL_KEYWORD in (channel.name or ""):
            return channel
    return None


def _music_state(guild_id) -> dict:
    key = str(guild_id)
    rec = music_data.get(key)
    if not rec:
        rec = {
            "tracks": [],
            "page": 0,
            "now_playing": None,
            "card_message_id": "",
            "play_message_id": "",
            "channel_id": "",
        }
        music_data[key] = rec
    rec.setdefault("tracks", [])
    rec.setdefault("page", 0)
    rec.setdefault("now_playing", None)
    rec.setdefault("card_message_id", "")
    rec.setdefault("play_message_id", "")
    rec.setdefault("channel_id", "")
    return rec


def _music_title_from_filename(name: str) -> str:
    cleaned = " ".join((os.path.basename(name or "未命名") or "未命名").split())
    return cleaned[:100] or "未命名"


def _music_caption_title(text: str) -> str:
    cleaned = " ".join((text or "").split())
    cleaned = MUSIC_URL_RE.sub("", cleaned).strip(" -_|")
    return cleaned[:100]


def _is_music_attachment(att) -> bool:
    name = (getattr(att, "filename", "") or "").lower()
    if any(name.endswith(ext) for ext in MUSIC_AUDIO_EXTS):
        return True
    ctype = (getattr(att, "content_type", "") or "").lower()
    return ctype.startswith("audio/")


def _new_music_path(filename: str) -> str:
    os.makedirs(MUSIC_STORE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return os.path.join(MUSIC_STORE_DIR, f"{stamp}_{secrets.token_hex(4)}_{_safe_filename(filename)}")


def _delete_music_file(track: dict):
    path = track.get("path")
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except Exception:
            pass


def _music_page_count(state: dict) -> int:
    total = len(state.get("tracks") or [])
    if total <= 0:
        return 1
    return max(1, (total + MUSIC_PAGE_SIZE - 1) // MUSIC_PAGE_SIZE)


def _music_page_tracks(state: dict) -> list:
    page = max(0, min(int(state.get("page") or 0), _music_page_count(state) - 1))
    state["page"] = page
    start = page * MUSIC_PAGE_SIZE
    return (state.get("tracks") or [])[start:start + MUSIC_PAGE_SIZE]


def _build_music_embed(state: dict) -> discord.Embed:
    tracks = state.get("tracks") or []
    page = int(state.get("page") or 0)
    pages = _music_page_count(state)
    now = state.get("now_playing") or {}
    if now.get("title"):
        now_txt = f"正在播：**{now['title']}**（{now.get('sharer_name') or '未知'} 分享，{now.get('requester_name') or '未知'} 点的）"
    else:
        now_txt = "还没在播。选一首再点播放。"
    if tracks:
        start = page * MUSIC_PAGE_SIZE
        lines = []
        for i, track in enumerate(_music_page_tracks(state), start + 1):
            lines.append(
                f"{i}. {track.get('title', '?')}  ·  {track.get('sharer_name', '?')}  ·  {_format_size(track.get('size', 0))}"
            )
        list_txt = "\n".join(lines)
    else:
        list_txt = "歌单还空着。把 mp3 / ogg / wav / m4a 丢进这个频道，或发直接音频地址，就会进歌单。"
    embed = discord.Embed(
        title="贝多芬时代",
        description=(
            f"{now_txt}\n\n"
            f"**歌单**（第 {page + 1}/{pages} 页，共 {len(tracks)} 首）\n"
            f"{list_txt}"
        )[:4000],
        color=discord.Color.dark_gold(),
    )
    embed.set_footer(text="丢音频或直接音频链接即可分享 | 选歌后点播放 | 看着播放条才能听")
    return embed


class PersistentMusicView(discord.ui.View):
    def __init__(self, state: dict = None):
        super().__init__(timeout=None)
        tracks = _music_page_tracks(state) if state else []
        options = []
        for i, track in enumerate(tracks[:25]):
            options.append(discord.SelectOption(
                label=str(track.get("title") or "未命名")[:100],
                description=f"{track.get('sharer_name', '?')} · {_format_size(track.get('size', 0))}"[:100],
                value=str(track.get("id") or i),
            ))
        select_kwargs = {
            "placeholder": "选择要播放的歌" if options else "歌单还空着",
            "options": options or [discord.SelectOption(label="暂无歌曲", value="none")],
            "min_values": 1,
            "max_values": 1,
            "row": 0,
            "custom_id": "music_select",
        }
        self.select_menu = discord.ui.Select(**select_kwargs)
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)
        if not options:
            self.select_menu.disabled = True

    async def on_select(self, interaction: discord.Interaction):
        values = []
        if hasattr(interaction, "data") and interaction.data:
            values = list(interaction.data.get("values") or [])
        if not values:
            values = list(self.select_menu.values or [])
        if not values or values[0] == "none":
            await interaction.response.defer()
            return
        _music_selections[(interaction.guild.id, interaction.user.id)] = values[0]
        await interaction.response.defer()

    @discord.ui.button(label="播放", style=discord.ButtonStyle.primary, custom_id="music_play", row=1)
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _play_selected_track(interaction)

    @discord.ui.button(label="上一首", style=discord.ButtonStyle.secondary, custom_id="music_prev_track", row=1)
    async def prev_track_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _play_adjacent_track(interaction, -1)

    @discord.ui.button(label="下一首", style=discord.ButtonStyle.secondary, custom_id="music_next_track", row=1)
    async def next_track_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _play_adjacent_track(interaction, 1)

    @discord.ui.button(label="上一页", style=discord.ButtonStyle.secondary, custom_id="music_prev", row=2)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = _music_state(interaction.guild.id)
        state["page"] = (int(state.get("page") or 0) - 1) % _music_page_count(state)
        save_music()
        await _refresh_music_card(interaction.guild, state)
        await interaction.response.defer()

    @discord.ui.button(label="下一页", style=discord.ButtonStyle.secondary, custom_id="music_next", row=2)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = _music_state(interaction.guild.id)
        state["page"] = (int(state.get("page") or 0) + 1) % _music_page_count(state)
        save_music()
        await _refresh_music_card(interaction.guild, state)
        await interaction.response.defer()

    @discord.ui.button(label="下架这首", style=discord.ButtonStyle.danger, custom_id="music_remove", row=2)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _remove_selected_track(interaction)


async def _music_channel_from_state(guild: discord.Guild, state: dict):
    channel_id = state.get("channel_id")
    if channel_id:
        channel = guild.get_channel(int(channel_id))
        if channel:
            return channel
    return _music_channel_of(guild)


async def _refresh_music_card(guild: discord.Guild, state: dict = None):
    if not guild:
        return
    state = state or _music_state(guild.id)
    channel = await _music_channel_from_state(guild, state)
    if not channel:
        return
    embed = _build_music_embed(state)
    view = PersistentMusicView(state)
    msg_id = state.get("card_message_id")
    if msg_id:
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(embed=embed, view=view)
            return
        except Exception:
            pass
    msg = await channel.send(embed=embed, view=view)
    state["card_message_id"] = str(msg.id)
    state["channel_id"] = str(channel.id)
    save_music()


async def setup_music_channels():
    os.makedirs(MUSIC_STORE_DIR, exist_ok=True)
    for guild in bot.guilds:
        channel = _music_channel_of(guild)
        if not channel:
            continue
        state = _music_state(guild.id)
        state["channel_id"] = str(channel.id)
        try:
            await _refresh_music_card(guild, state)
            logger.info(f"更新贝多芬点唱机: #{channel.name}")
        except Exception as e:
            logger.error(f"贝多芬频道 #{channel.name} 设置失败: {e}")


def _append_music_track(state: dict, user, filename: str, data: bytes, title: str = "") -> str:
    if len(state.get("tracks") or []) >= MUSIC_MAX_TRACKS:
        return "歌单满了，先下架几首再分享。"
    size = len(data or b"")
    if size <= 0:
        return "空文件，没收入。"
    if size > MUSIC_MAX_BYTES:
        return f"{filename} 超过 {MUSIC_MAX_BYTES // (1024 * 1024)}MB，换小一点的。"
    path = _new_music_path(filename)
    with open(path, "wb") as f:
        f.write(data)
    display = (title or "").strip() or _music_title_from_filename(filename)
    track = {
        "id": secrets.token_hex(6),
        "title": display[:100],
        "filename": filename,
        "path": path,
        "size": size,
        "sharer_id": str(user.id),
        "sharer_name": getattr(user, "display_name", None) or str(user),
        "added_at": _beijing_now().isoformat(),
    }
    state.setdefault("tracks", []).append(track)
    state["page"] = _music_page_count(state) - 1
    save_music()
    return ""


async def _add_music_track(guild: discord.Guild, user, att, caption: str = "") -> str:
    state = _music_state(guild.id)
    size = int(getattr(att, "size", 0) or 0)
    if size > MUSIC_MAX_BYTES:
        return f"{att.filename} 超过 {MUSIC_MAX_BYTES // (1024 * 1024)}MB，换小一点的。"
    data = await att.read()
    err = _append_music_track(
        state,
        user,
        att.filename,
        data,
        _music_caption_title(caption) or _music_title_from_filename(att.filename),
    )
    if err:
        return err
    await _refresh_music_card(guild, state)
    return ""


def _filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    name = os.path.basename(path.rstrip("/"))
    if any(name.lower().endswith(ext) for ext in MUSIC_AUDIO_EXTS):
        return name
    return ""


def _looks_like_audio_url(url: str) -> bool:
    return bool(_filename_from_url(url))


def _music_urls_in_text(text: str) -> list:
    found = []
    for raw in MUSIC_URL_RE.findall(text or ""):
        url = raw.rstrip(".,;:!?)]}>\"'")
        if url not in found:
            found.append(url)
    return found


def _music_platform_note(urls: list) -> str:
    platforms = [url for url in urls if MUSIC_PLATFORM_RE.search(url)]
    if not platforms:
        return ""
    return "网易云 / QQ / YouTube 这类页面链接我不会去扒歌，发直接音频地址（以 .mp3 等结尾）才能入库。"


async def _download_direct_audio(url: str):
    filename = _filename_from_url(url)
    if not filename:
        return None, "不是直接音频地址。"
    timeout = aiohttp.ClientTimeout(total=45)
    headers = {"User-Agent": "Mozilla/5.0 Chen-Abot"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    return None, f"{filename} 下载失败（{resp.status}）。"
                ctype = (resp.headers.get("Content-Type") or "").lower()
                clen = resp.headers.get("Content-Length")
                if clen and int(clen) > MUSIC_MAX_BYTES:
                    return None, f"{filename} 超过 {MUSIC_MAX_BYTES // (1024 * 1024)}MB，换小一点的。"
                if ctype and not (
                    ctype.startswith("audio/")
                    or ctype in ("application/octet-stream", "binary/octet-stream")
                    or "mpeg" in ctype
                ):
                    if not _looks_like_audio_url(str(resp.url)):
                        return None, f"{filename} 看起来不是音频文件。"
                data = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MUSIC_MAX_BYTES:
                        return None, f"{filename} 超过 {MUSIC_MAX_BYTES // (1024 * 1024)}MB，换小一点的。"
                if not data:
                    return None, f"{filename} 是空的。"
                final_name = _filename_from_url(str(resp.url)) or filename
                return (final_name, bytes(data)), ""
    except Exception as e:
        return None, f"{filename} 下载失败: {e}"


async def _add_music_bytes(guild: discord.Guild, user, filename: str, data: bytes, caption: str = "") -> str:
    state = _music_state(guild.id)
    err = _append_music_track(
        state,
        user,
        filename,
        data,
        _music_caption_title(caption) or _music_title_from_filename(filename),
    )
    if err:
        return err
    await _refresh_music_card(guild, state)
    return ""


async def _ingest_music_message(message: discord.Message) -> bool:
    audio_atts = [att for att in (message.attachments or []) if _is_music_attachment(att)]
    urls = _music_urls_in_text(message.content or "")
    audio_urls = [url for url in urls if _looks_like_audio_url(url)]
    platform_note = _music_platform_note(urls)
    if not audio_atts and not audio_urls and not platform_note:
        return False
    added = 0
    errors = []
    caption = message.content or ""
    for att in audio_atts:
        err = await _add_music_track(message.guild, message.author, att, caption)
        if err:
            errors.append(err)
        else:
            added += 1
    for url in audio_urls:
        result, err = await _download_direct_audio(url)
        if err:
            errors.append(err)
            continue
        filename, data = result
        err = await _add_music_bytes(message.guild, message.author, filename, data, caption)
        if err:
            errors.append(err)
        else:
            added += 1
    if platform_note:
        errors.append(platform_note)
    try:
        await message.delete()
    except Exception:
        pass
    note = []
    if added:
        note.append(f"已收入歌单 {added} 首。")
    note.extend(errors)
    if note:
        try:
            await message.channel.send(
                f"{message.author.mention} " + " ".join(note),
                delete_after=12,
            )
        except Exception:
            pass
    return True


def _track_by_id(state: dict, track_id) -> Optional[dict]:
    if not track_id:
        return None
    for track in state.get("tracks") or []:
        if str(track.get("id")) == str(track_id):
            return track
    return None


def _selected_music_track(interaction: discord.Interaction, state: dict):
    return _track_by_id(state, _music_selections.get((interaction.guild.id, interaction.user.id)))


def _adjacent_music_track(state: dict, step: int):
    tracks = state.get("tracks") or []
    if not tracks:
        return None
    now = state.get("now_playing") or {}
    current_id = now.get("track_id")
    idx = 0
    for i, track in enumerate(tracks):
        if str(track.get("id")) == str(current_id):
            idx = i
            break
    return tracks[(idx + step) % len(tracks)]


async def _send_music_play(interaction: discord.Interaction, track: dict):
    state = _music_state(interaction.guild.id)
    path = track.get("path")
    if not path or not os.path.isfile(path):
        await interaction.response.send_message("这首的文件丢了，先下架再重新分享。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel
    filename = track.get("filename") or (track.get("title") + ".mp3")
    try:
        audio = discord.File(path, filename=filename)
        play_msg = await channel.send(
            content=f"正在播 **{track.get('title')}**（{track.get('sharer_name')} 分享）",
            file=audio,
        )
    except Exception as e:
        await interaction.followup.send(f"播放失败: {e}", ephemeral=True)
        return
    old_play_id = state.get("play_message_id")
    if old_play_id and str(old_play_id) != str(play_msg.id):
        try:
            old_msg = await channel.fetch_message(int(old_play_id))
            await old_msg.delete()
        except Exception:
            pass
    state["play_message_id"] = str(play_msg.id)
    state["now_playing"] = {
        "title": track.get("title"),
        "sharer_name": track.get("sharer_name"),
        "requester_name": interaction.user.display_name,
        "track_id": track.get("id"),
    }
    _music_selections[(interaction.guild.id, interaction.user.id)] = str(track.get("id"))
    save_music()
    await _refresh_music_card(interaction.guild, state)
    await interaction.followup.send("已切到这首。看着频道里的播放条就能听。", ephemeral=True)


async def _play_selected_track(interaction: discord.Interaction):
    state = _music_state(interaction.guild.id)
    track = _selected_music_track(interaction, state)
    if not track:
        await interaction.response.send_message("先在歌单里选一首。", ephemeral=True)
        return
    await _send_music_play(interaction, track)


async def _play_adjacent_track(interaction: discord.Interaction, step: int):
    state = _music_state(interaction.guild.id)
    track = _adjacent_music_track(state, step)
    if not track:
        await interaction.response.send_message("歌单还空着。", ephemeral=True)
        return
    await _send_music_play(interaction, track)


async def _remove_selected_track(interaction: discord.Interaction):
    state = _music_state(interaction.guild.id)
    track = _selected_music_track(interaction, state)
    if not track:
        await interaction.response.send_message("先选一首再下架。", ephemeral=True)
        return
    owner = _is_island_owner(interaction)
    if str(interaction.user.id) != str(track.get("sharer_id")) and not owner:
        await interaction.response.send_message("只能下架自己分享的歌。", ephemeral=True)
        return
    state["tracks"] = [item for item in state.get("tracks") or [] if item.get("id") != track.get("id")]
    now = state.get("now_playing") or {}
    if now.get("track_id") == track.get("id"):
        state["now_playing"] = None
    pages = _music_page_count(state)
    if int(state.get("page") or 0) >= pages:
        state["page"] = pages - 1
    _delete_music_file(track)
    save_music()
    await _refresh_music_card(interaction.guild, state)
    await interaction.response.send_message(f"已下架 **{track.get('title')}**。", ephemeral=True)


# ═══════════════════════════════════════════
#  故事分享会
# ═══════════════════════════════════════════

STORY_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _story_channel_of(guild: discord.Guild):
    for channel in getattr(guild, "text_channels", []) or []:
        if STORY_CHANNEL_KEYWORD in (channel.name or ""):
            return channel
    return None


def _is_story_image(att) -> bool:
    name = (getattr(att, "filename", "") or "").lower()
    if any(name.endswith(ext) for ext in STORY_IMAGE_EXTS):
        return True
    ctype = (getattr(att, "content_type", "") or "").lower()
    return ctype.startswith("image/")


def _new_story_image_path(filename: str) -> str:
    os.makedirs(STORY_STORE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return os.path.join(STORY_STORE_DIR, f"{stamp}_{secrets.token_hex(4)}_{_safe_filename(filename)}")


def _find_story_by_message(message_id) -> tuple:
    target = str(message_id)
    for pid, post in story_data.items():
        if str(post.get("message_id") or "") == target:
            return pid, post
    return None, None


def _story_author_label(post: dict) -> str:
    if post.get("anonymous"):
        return "一位岛民"
    return post.get("author_name") or "岛民"


def _story_comment_label(post: dict, comment: dict) -> str:
    if post.get("anonymous") and str(comment.get("user_id")) == str(post.get("author_id")):
        return "分享者"
    if comment.get("anonymous"):
        return "一位岛民"
    return comment.get("user_name") or "岛民"


def _build_story_embeds(post: dict) -> list:
    text = (post.get("text") or "").strip() or "（无文字）"
    comments = post.get("comments") or []
    shown = comments[-STORY_COMMENT_SHOW:]
    lines = [text, "", f"— {_story_author_label(post)}"]
    if shown:
        lines.append("")
        lines.append("**评论**")
        hidden_n = max(0, len(comments) - len(shown))
        if hidden_n:
            lines.append(f"更早还有 {hidden_n} 条")
        for item in shown:
            stamp = _format_beijing_minute(item.get("time"))
            body = (item.get("text") or "").strip()
            lines.append(f"- {_story_comment_label(post, item)}　{stamp}\n  {body}")
    else:
        lines.append("")
        lines.append("还没有评论。点下面的按钮写下你的感受。")
    embed = discord.Embed(
        title="一段故事",
        description="\n".join(lines)[:4000],
        color=discord.Color.teal() if post.get("anonymous") else discord.Color.blue(),
    )
    urls = [u for u in (post.get("image_urls") or []) if u][:STORY_MAX_IMAGES]
    if urls:
        embed.set_image(url=urls[0])
    embed.set_footer(text="匿名分享不会露出发布者 | 评论时可自己选匿名或实名")
    embeds = [embed]
    for url in urls[1:]:
        extra = discord.Embed(color=embed.color)
        extra.set_image(url=url)
        embeds.append(extra)
    return embeds


def _story_entry_embed() -> discord.Embed:
    embed = discord.Embed(
        title="故事分享会",
        description=(
            "把游玩时被触动的文字截图、摘抄发到这里。\n"
            "一段文字，最多 4 张图。发布时选匿名或实名。\n\n"
            "**匿名**：公开卡片不露你是谁；你能看见评论并回复，别人始终看不出分享者。\n"
            "**实名**：名字和内容都摊开。\n\n"
            "点「分享一段故事」，或直接把截图丢进这个频道再选匿名/实名。"
        ),
        color=discord.Color.teal(),
    )
    return embed


async def _save_story_images(attachments: list) -> list:
    images = []
    for att in attachments:
        if len(images) >= STORY_MAX_IMAGES:
            break
        if not _is_story_image(att):
            continue
        data = await att.read()
        if not data:
            continue
        path = _new_story_image_path(att.filename)
        with open(path, "wb") as f:
            f.write(data)
        images.append({
            "path": path,
            "filename": att.filename,
            "size": len(data),
        })
    return images


def _story_files(images: list) -> list:
    files = []
    for i, img in enumerate(images or []):
        path = img.get("path")
        if not path or not os.path.isfile(path):
            continue
        ext = os.path.splitext(img.get("filename") or "")[1].lower()
        if ext not in STORY_IMAGE_EXTS:
            ext = ".png"
        files.append(discord.File(path, filename=f"摘抄{i + 1}{ext}"))
    return files


async def _refresh_story_message(channel, post: dict):
    msg_id = post.get("message_id")
    if not msg_id or not channel:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embeds=_build_story_embeds(post), view=PersistentStoryPostView())
    except Exception as e:
        logger.warning(f"刷新故事卡失败: {e}")


async def _publish_story(channel, author, text: str, images: list, anonymous: bool) -> str:
    body = (text or "").strip()
    if not body:
        return "先写一段文字再分享。"
    if len(body) > STORY_TEXT_MAX:
        body = body[:STORY_TEXT_MAX]
    post_id = secrets.token_hex(6)
    post = {
        "id": post_id,
        "guild_id": str(channel.guild.id),
        "channel_id": str(channel.id),
        "message_id": "",
        "author_id": str(author.id),
        "author_name": getattr(author, "display_name", None) or str(author),
        "anonymous": bool(anonymous),
        "text": body,
        "images": images or [],
        "image_urls": [],
        "comments": [],
        "created_at": _beijing_now().isoformat(),
    }
    files = _story_files(post["images"])
    msg = await channel.send(
        embeds=_build_story_embeds(post),
        view=PersistentStoryPostView(),
        files=files or None,
    )
    urls = [att.url for att in (msg.attachments or [])]
    post["image_urls"] = urls
    post["message_id"] = str(msg.id)
    story_data[post_id] = post
    save_stories()
    if urls:
        try:
            await msg.edit(embeds=_build_story_embeds(post), view=PersistentStoryPostView())
        except Exception:
            pass
    return ""


class StoryShareModal(discord.ui.Modal, title="分享一段故事"):
    def __init__(self, anonymous: bool, preset_text: str = "", preset_images: list = None):
        super().__init__()
        self.anonymous = anonymous
        self.preset_images = list(preset_images or [])
        self.body = discord.ui.TextInput(
            label="摘抄或触动你的文字",
            placeholder="一段摘抄、一句对白、当时的感觉……",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=STORY_TEXT_MAX,
            default=(preset_text or "")[:STORY_TEXT_MAX] or None,
        )
        self.add_item(self.body)
        self.file_upload = None
        if _supports_modal_file_upload() and not self.preset_images:
            self.file_upload = discord.ui.FileUpload(
                min_values=0,
                max_values=STORY_MAX_IMAGES,
                required=False,
            )
            self.add_item(discord.ui.Label(
                text="截图（最多 4 张）",
                description="文字摘抄的截图，可不传",
                component=self.file_upload,
            ))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        images = list(self.preset_images)
        if self.file_upload:
            atts = [att for att in (self.file_upload.values or []) if _is_story_image(att)]
            if atts:
                images = await _save_story_images(atts)
        err = await _publish_story(
            interaction.channel,
            interaction.user,
            str(self.body.value or ""),
            images,
            self.anonymous,
        )
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        mode = "匿名" if self.anonymous else "实名"
        await interaction.followup.send(f"已{mode}分享到故事会。", ephemeral=True)


class StoryModeView(discord.ui.View):
    def __init__(self, preset_text: str = "", preset_images: list = None):
        super().__init__(timeout=180)
        self.preset_text = preset_text or ""
        self.preset_images = list(preset_images or [])

    async def _open(self, interaction: discord.Interaction, anonymous: bool):
        await interaction.response.send_modal(
            StoryShareModal(anonymous, self.preset_text, self.preset_images)
        )

    @discord.ui.button(label="匿名分享", style=discord.ButtonStyle.secondary)
    async def anon_share(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, True)

    @discord.ui.button(label="实名分享", style=discord.ButtonStyle.primary)
    async def named_share(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, False)


class PersistentStoryEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="分享一段故事", style=discord.ButtonStyle.primary, custom_id="story_entry_share")
    async def share_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "这次分享要匿名还是实名？",
            view=StoryModeView(),
            ephemeral=True,
        )


class StoryCommentModal(discord.ui.Modal, title="写下评论"):
    def __init__(self, post_id: str, anonymous: bool):
        super().__init__()
        self.post_id = post_id
        self.anonymous = anonymous
        self.body = discord.ui.TextInput(
            label="评论",
            placeholder="你的感受、共鸣、想说的话",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=STORY_COMMENT_MAX,
        )
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        post = story_data.get(self.post_id)
        if not post:
            await interaction.response.send_message("这条分享找不到了。", ephemeral=True)
            return
        comment = {
            "id": secrets.token_hex(4),
            "user_id": str(interaction.user.id),
            "user_name": interaction.user.display_name,
            "anonymous": bool(self.anonymous),
            "text": str(self.body.value or "").strip()[:STORY_COMMENT_MAX],
            "time": _beijing_now().isoformat(),
        }
        if not comment["text"]:
            await interaction.response.send_message("评论是空的。", ephemeral=True)
            return
        post.setdefault("comments", []).append(comment)
        save_stories()
        channel = interaction.channel
        await _refresh_story_message(channel, post)
        await interaction.response.send_message("评论已写下。", ephemeral=True)


class StoryCommentModeView(discord.ui.View):
    def __init__(self, post_id: str):
        super().__init__(timeout=120)
        self.post_id = post_id

    @discord.ui.button(label="匿名评论", style=discord.ButtonStyle.secondary)
    async def anon_comment(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StoryCommentModal(self.post_id, True))

    @discord.ui.button(label="实名评论", style=discord.ButtonStyle.primary)
    async def named_comment(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StoryCommentModal(self.post_id, False))


class PersistentStoryPostView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="评论", style=discord.ButtonStyle.secondary, custom_id="story_post_comment")
    async def comment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pid, post = _find_story_by_message(interaction.message.id)
        if not post:
            await interaction.response.send_message("这条分享找不到了。", ephemeral=True)
            return
        await interaction.response.send_message(
            "这次评论要匿名还是实名？",
            view=StoryCommentModeView(pid),
            ephemeral=True,
        )


class StoryPendingView(discord.ui.View):
    def __init__(self, pending_key: str, user_id: int):
        super().__init__(timeout=300)
        self.pending_key = pending_key
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("这是别人的发布确认。", ephemeral=True)
            return False
        return True

    async def _finish(self, interaction: discord.Interaction, anonymous: bool):
        pending = _story_pending.pop(self.pending_key, None)
        if not pending:
            await interaction.response.send_message("这条待发布已经过期，重新丢一次或点「分享一段故事」。", ephemeral=True)
            return
        text = (pending.get("text") or "").strip()
        images = pending.get("images") or []
        if not text:
            await interaction.response.send_modal(StoryShareModal(anonymous, "", images))
            return
        await interaction.response.defer(ephemeral=True)
        err = await _publish_story(interaction.channel, interaction.user, text, images, anonymous)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        mode = "匿名" if anonymous else "实名"
        await interaction.followup.send(f"已{mode}分享到故事会。", ephemeral=True)
        prompt_id = pending.get("prompt_id")
        if prompt_id:
            try:
                msg = await interaction.channel.fetch_message(int(prompt_id))
                await msg.delete()
            except Exception:
                pass

    @discord.ui.button(label="匿名发布", style=discord.ButtonStyle.secondary)
    async def anon_publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, True)

    @discord.ui.button(label="实名发布", style=discord.ButtonStyle.primary)
    async def named_publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, False)


async def _ingest_story_message(message: discord.Message) -> bool:
    atts = [att for att in (message.attachments or []) if _is_story_image(att)]
    text = (message.content or "").strip()
    if not atts:
        return False
    try:
        images = await _save_story_images(atts)
    except Exception as e:
        logger.warning(f"故事截图保存失败: {e}")
        return False
    if not images:
        return False
    try:
        await message.delete()
    except Exception:
        pass
    key = f"{message.guild.id}:{message.author.id}:{secrets.token_hex(3)}"
    _story_pending[key] = {
        "text": text[:STORY_TEXT_MAX],
        "images": images,
        "prompt_id": "",
    }
    try:
        prompt = await message.channel.send(
            f"{message.author.mention} 截图已收下。选匿名还是实名发布？没有文字的话，选完后会再请你补一段。",
            view=StoryPendingView(key, message.author.id),
        )
        _story_pending[key]["prompt_id"] = str(prompt.id)
    except Exception as e:
        logger.warning(f"故事确认条发送失败: {e}")
        _story_pending.pop(key, None)
        return False
    return True


async def setup_story_channels():
    os.makedirs(STORY_STORE_DIR, exist_ok=True)
    embed = _story_entry_embed()
    for guild in bot.guilds:
        channel = _story_channel_of(guild)
        if not channel:
            continue
        try:
            existing_msg_id = story_channel_messages.get(str(channel.id))
            kept = False
            async for old_msg in channel.history(limit=40):
                if old_msg.author.id != bot.user.id:
                    continue
                title = old_msg.embeds[0].title if old_msg.embeds else ""
                if existing_msg_id and str(old_msg.id) == existing_msg_id:
                    try:
                        await old_msg.edit(embed=embed, view=PersistentStoryEntryView())
                        kept = True
                    except Exception:
                        pass
                elif title == "故事分享会":
                    try:
                        await old_msg.delete()
                    except Exception:
                        pass
            if not kept:
                msg = await channel.send(embed=embed, view=PersistentStoryEntryView())
                story_channel_messages[str(channel.id)] = str(msg.id)
                save_stories()
            logger.info(f"更新故事分享会入口: #{channel.name}")
        except Exception as e:
            logger.error(f"故事分享会 #{channel.name} 设置失败: {e}")


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
        review_msg = await review_channel.send(embeds=_build_review_embeds(rec), view=view)
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
        img_n = 0
        for ev in evidence_list:
            if ev.get("type") == "image":
                img_n += 1
                lines.append(f"{img_n}. 图片（见下方）")
            else:
                lines.append(f"- {str(ev.get('content', ''))[:300]}")
    else:
        lines.append("**已确认补充资料**")
        lines.append("暂无")
    if rec.get("status") == "reviewing" and pending:
        lines.append("")
        lines.append(f"**待确认上传:** {len(pending)} 条（举报人尚未点确认）")
    return "\n".join(lines)


def _evidence_image_urls(rec: dict) -> list:
    urls = []
    for ev in rec.get("evidence") or []:
        if ev.get("type") == "image":
            url = (ev.get("content") or "").strip()
            if url:
                urls.append(url)
    return urls


def _build_review_embeds(rec: dict) -> list:
    embed = _build_review_embed(rec)
    urls = _evidence_image_urls(rec)
    if urls:
        embed.set_image(url=urls[0])
    embeds = [embed]
    for url in urls[1:9]:
        extra = discord.Embed(color=embed.color)
        extra.set_image(url=url)
        embeds.append(extra)
    return embeds


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
        kwargs = {"embeds": _build_review_embeds(rec)}
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
    HIDDEN_GUIDE_KEYWORDS = (
        GUIDE_CHANNEL_KEYWORD,
        ALERT_CHANNEL_KEYWORD,
        BLACKLIST_CHANNEL_KEYWORD,
        "文件存储",
        "举报审核",
    )
    EXCLUDED_NAMES = {
        "📁-文件存储",
        "举报审核",
        "测试",
        "黑户地带",
        ALERT_CHANNEL_NAME,
        BLACKLIST_CHANNEL_NAME,
    }

    def _hidden_from_guide(ch, guide_channel_id: int) -> bool:
        if ch.id == guide_channel_id:
            return True
        if ch.name in EXCLUDED_NAMES:
            return True
        return any(keyword in ch.name for keyword in HIDDEN_GUIDE_KEYWORDS)

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
                filtered = [ch for ch in chs if not _hidden_from_guide(ch, channel.id)]
                if not filtered:
                    continue
                lines.append(f"**📁 {cat_name}**")
                for ch in filtered:
                    lines.append(f"　└ {ch.mention}")
                lines.append("")

            filtered_no_cat = [ch for ch in no_category if not _hidden_from_guide(ch, channel.id)]
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
#  /清理测试数据 - 仅岛主可用，工单、审核频道、黑户地带一并回到初始
# ═══════════════════════════════════════════

async def _wipe_channel_messages(channel) -> int:
    if channel is None:
        return 0
    cleared = 0
    try:
        while True:
            batch = []
            async for msg in channel.history(limit=100):
                batch.append(msg)
            if not batch:
                break
            try:
                await channel.delete_messages(batch)
                cleared += len(batch)
                if len(batch) < 100:
                    break
                continue
            except Exception:
                pass
            for msg in batch:
                try:
                    await msg.delete()
                    cleared += 1
                except Exception:
                    pass
            if len(batch) < 100:
                break
    except Exception as e:
        logger.warning(f"清空频道 #{getattr(channel, 'name', '?')} 失败: {e}")
    return cleared


def _purge_guild_reports(guild_id: str):
    gid = str(guild_id)
    for rid, rec in list(report_data.items()):
        if rec.get("guild_id") == gid:
            report_data.pop(rid, None)
    save_reports()
    report_counter.pop(gid, None)
    save_report_counter()


def _purge_guild_files(guild_id: str, extra_channel_ids=None):
    gid = str(guild_id)
    removed_ids = set()
    for fid, rec in list(file_records.items()):
        if str(rec.get("guild_id")) == gid:
            _delete_record_files(rec)
            file_records.pop(fid, None)
            removed_ids.add(str(fid))
    extra = {str(cid) for cid in (extra_channel_ids or [])}
    for cid in list(channel_published.keys()):
        pub = channel_published.get(cid) or {}
        if str(cid) in extra or str(pub.get("file_id") or "") in removed_ids:
            channel_published.pop(cid, None)
    download_logs[:] = [
        log for log in download_logs
        if str(log.get("record_id") or "") not in removed_ids
    ]
    save_records()
    save_channel_published()
    save_download_logs()


@bot.tree.command(name="清理测试数据", description="清空工单、黑户地带、存储文件和帖子公开卡片（仅岛主）")
async def cleanup_reports(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("只有岛主才能使用此命令。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    guild_id = str(guild.id)
    deleted_threads = 0
    deleted_cards = 0
    card_channel_ids = set()

    for rec in list(report_data.values()):
        if rec.get("guild_id") != guild_id:
            continue
        thread_id = rec.get("thread_id")
        if not thread_id:
            continue
        thread = guild.get_thread(int(thread_id))
        if thread is None:
            try:
                thread = await guild.fetch_channel(int(thread_id))
            except Exception:
                thread = None
        if thread:
            try:
                await thread.delete()
                deleted_threads += 1
            except Exception as e:
                logger.warning(f"删除子频道失败: {e}")

    seen_cards = set()
    for rec in list(file_records.values()):
        if str(rec.get("guild_id")) != guild_id:
            continue
        msg_id = rec.get("published_msg_id")
        channel_id = rec.get("source_channel_id")
        if not msg_id or not channel_id:
            continue
        key = (str(channel_id), str(msg_id))
        if key in seen_cards:
            continue
        seen_cards.add(key)
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            continue
        card_channel_ids.add(str(channel_id))
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.delete()
            deleted_cards += 1
        except Exception:
            pass
    for cid, pub in list(channel_published.items()):
        msg_id = (pub or {}).get("message_id")
        if not msg_id:
            continue
        key = (str(cid), str(msg_id))
        if key in seen_cards:
            continue
        channel = guild.get_channel(int(cid)) if str(cid).isdigit() else None
        if channel is None or channel.guild.id != guild.id:
            continue
        seen_cards.add(key)
        card_channel_ids.add(str(cid))
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.delete()
            deleted_cards += 1
        except Exception:
            pass

    review_channel = discord.utils.get(guild.text_channels, name=REPORT_REVIEW_CHANNEL_NAME)
    blacklist_channel = None
    for channel in guild.text_channels:
        if BLACKLIST_CHANNEL_KEYWORD in channel.name:
            blacklist_channel = channel
            break
    storage_channel = None
    storage_id = storage_channels.get(guild_id)
    if storage_id:
        storage_channel = guild.get_channel(int(storage_id))
    if storage_channel is None:
        storage_channel = discord.utils.get(guild.text_channels, name="📁-文件存储")

    cleared_review = await _wipe_channel_messages(review_channel)
    cleared_blacklist = await _wipe_channel_messages(blacklist_channel)
    cleared_storage = await _wipe_channel_messages(storage_channel)
    _purge_guild_reports(guild_id)
    _purge_guild_files(guild_id, extra_channel_ids=card_channel_ids)

    await interaction.followup.send(
        f"清理完成，工单和文件都回到最初状态。\n"
        f"已关闭举报子区 {deleted_threads} 个\n"
        f"审核频道已清空 {cleared_review} 条\n"
        f"黑户地带公示已清空 {cleared_blacklist} 条\n"
        f"存储频道文件已清空 {cleared_storage} 条\n"
        f"帖子公开卡片已删除 {deleted_cards} 张\n"
        f"工单从 #001、资源码从空重新开始",
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
    port = int(os.getenv("SERVER_PORT") or os.getenv("PORT") or 8080)
    app = web.Application()
    
    async def health_check(request):
        return web.Response(text="OK")

    async def handle_claim(request):
        _purge_claim_tokens()
        token = request.match_info.get("token")
        item = _claim_tokens.get(token)
        now = datetime.now(timezone.utc).timestamp()
        if not item:
            return web.Response(status=404, text="链接无效或已过期")
        if item.get("expires_at", 0) <= now:
            _claim_tokens.pop(token, None)
            return web.Response(status=410, text="链接已过期，请重新点下载")
        data = item.get("data")
        filename = item.get("filename") or "file"
        if data is None:
            return web.Response(status=404, text="文件已丢失")
        ascii_name = _safe_filename(filename)
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        }
        return web.Response(body=data, headers=headers)

    app.router.add_get("/", health_check)
    app.router.add_get("/claim/{token}", handle_claim)
    app.router.add_get("/claim/{token}/{filename}", handle_claim)
    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, "0.0.0.0", port)
    await site.start()
    public_url = _public_base_url() or "(未设置 PUBLIC_BASE_URL)"
    logger.info(f"HTTP 已启动，端口: {port}，领取根地址: {public_url}")


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
        bot.add_view(PersistentGambleView())
        bot.add_view(PersistentRedPacketView())
        bot.add_view(PersistentTicketGiveView())
        bot.add_view(PersistentReportEntryView())
        bot.add_view(ThreadReportStartView())
        bot.add_view(PersistentReportReviewView())
        bot.add_view(PersistentEvidenceView())
        bot.add_view(PublishedFileView())
        bot.add_view(PersistentStorageCardView())
        bot.add_view(PersistentMusicView())
        bot.add_view(PersistentStoryEntryView())
        bot.add_view(PersistentStoryPostView())

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