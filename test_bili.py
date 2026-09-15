import asyncio
import sys

sys.path.insert(0, "/tmp/opencode/chen-abot")
import main as m


def check_filter():
    cases = [
        ("MV 在音乐区", 193, "【官方 MV】Never Gonna Give You Up", "", True),
        ("游戏解说", 17, "战地鸡记者直播B52大战黄瓜君", "", False),
        ("游戏区带 MV 字样", 17, "【MV】某游戏主题曲", "", True),
        ("游戏区解说实况", 17, "通关实况 第3期", "", False),
        ("非音乐区靠标题", 171, "【高音质】无损纯音乐合集", "", True),
        ("强制入库", 17, "游戏解说", "强制入库", True),
        ("无关内容", 4, "今天打了一把排位", "", False),
    ]
    fail = 0
    for name, tid, title, caption, want in cases:
        got = m._bili_looks_like_music(tid, title, caption)
        flag = "OK " if got == want else "FAIL"
        if got != want:
            fail += 1
        print(f"[{flag}] {name}: 期望={want} 实际={got}")
    return fail


def check_regex():
    fail = 0
    urls = [
        "https://www.bilibili.com/video/BV1GJ411x7h7",
        "https://b23.tv/abcd123",
        "https://www.bilibili.com/video/av80433022",
    ]
    for u in urls:
        if not m._looks_like_stream_url(u):
            print(f"[FAIL] 白名单未命中: {u}")
            fail += 1
    blocked = [
        "https://music.163.com/song?id=1959009067",
        "https://y.qq.com/n/ryqq/songDetail/xxx",
        "https://open.spotify.com/track/xxx",
    ]
    for u in blocked:
        if not m._music_blocked_note([u]):
            print(f"[FAIL] 黑名单未命中: {u}")
            fail += 1
    if not fail:
        print("[OK ] 正则路由：B站进白名单，网易云/QQ/Spotify仍拦截")
    return fail


async def check_live():
    fail = 0
    print("\n--- 实测：音乐 MV (BV1GJ411x7h7) ---")
    try:
        title, url, webpage, headers = await m._resolve_bilibili(
            "https://www.bilibili.com/video/BV1GJ411x7h7"
        )
        print(f"[OK ] title={title}")
        print(f"[OK ] webpage={webpage}")
        print(f"[OK ] referer={headers.get('Referer')}")
        print(f"[OK ] audio_host={url.split('/')[2]}")
    except Exception as e:
        print(f"[FAIL] 音乐视频解析失败: {type(e).__name__}: {e}")
        fail += 1
        url = ""

    print("\n--- 实测：游戏解说应被拒 (BV1y4411Y7ug) ---")
    try:
        await m._resolve_bilibili("https://www.bilibili.com/video/BV1y4411Y7ug")
        print("[FAIL] 游戏解说没有被拒绝")
        fail += 1
    except m._MusicRejected as e:
        print(f"[OK ] 已拒绝: {e}")
    except Exception as e:
        print(f"[FAIL] 非预期异常: {type(e).__name__}: {e}")
        fail += 1

    print("\n--- 实测：b23.tv 短链展开 ---")
    try:
        expanded = await m._bili_expand("https://b23.tv/BV1GJ411x7h7")
        ok = "BV1GJ411x7h7" in expanded
        print(f"[{'OK ' if ok else 'FAIL'}] {expanded}")
        if not ok:
            fail += 1
    except Exception as e:
        print(f"[FAIL] 短链展开失败: {e}")
        fail += 1

    return fail, url


def main():
    fail = check_regex()
    fail += check_filter()
    fail += asyncio.run(check_live())[0]
    print(f"\n=== 失败项: {fail} ===")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
