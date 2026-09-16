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


class FakeResp:
    def __init__(self, status=200, ctype="application/json", body="{}"):
        self.status = status
        self.headers = {"Content-Type": ctype}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._body


class FakeSession:
    """按脚本依次返回响应，用来模拟 B站 风控"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "headers": dict(headers or {})})
        return self._responses.pop(0) if self._responses else FakeResp()


def check_risk_control_retry():
    async def run():
        fail = 0

        # 风控页返回 HTML，重试第二次拿到正常 JSON
        session = FakeSession([
            FakeResp(412, "text/html", "<html>412</html>"),
            FakeResp(200, "application/json", '{"code":0,"data":{"title":"ok"}}'),
        ])
        payload = await m._bili_get_json(session, "/x/web-interface/view", {"bvid": "BV1"})
        ok = payload.get("data", {}).get("title") == "ok" and len(session.calls) == 2
        print(f"[{'OK ' if ok else 'FAIL'}] 风控 412 后重试成功，调用次数={len(session.calls)}")
        fail += 0 if ok else 1

        # 两次都被风控 → 抛 _BiliRiskControl
        session = FakeSession([FakeResp(412, "text/html", "<html>412</html>")] * m.BILI_FETCH_RETRIES)
        try:
            await m._bili_get_json(session, "/x/web-interface/view", {"bvid": "BV1"})
            print("[FAIL] 持续风控时没有报错")
            fail += 1
        except m._BiliRiskControl as e:
            print(f"[OK ] 持续风控抛出 _BiliRiskControl: {e}")

        # 返回非 JSON 文本（yt-dlp 当年炸的那个场景）也不该抛出解析异常
        session = FakeSession([FakeResp(200, "text/plain", "not json")] * m.BILI_FETCH_RETRIES)
        try:
            await m._bili_get_json(session, "/x/web-interface/view", {"bvid": "BV1"})
            print("[FAIL] 非 JSON 内容没有报错")
            fail += 1
        except m._BiliRiskControl:
            print("[OK ] 非 JSON 内容被识别为风控，未抛 JSONDecodeError")

        return fail

    return asyncio.run(run())


def check_cookie_header():
    fail = 0
    m.BILI_COOKIE = "SESSDATA=abc; buvid3=def"
    headers = m._bili_headers()
    ok = headers.get("Cookie") == "SESSDATA=abc; buvid3=def" and headers.get("Referer")
    print(f"[{'OK ' if ok else 'FAIL'}] 配了 Cookie 时请求头带上 Cookie: {bool(headers.get('Cookie'))}")
    fail += 0 if ok else 1
    m.BILI_COOKIE = ""
    headers = m._bili_headers()
    ok = "Cookie" not in headers
    print(f"[{'OK ' if ok else 'FAIL'}] 未配 Cookie 时不带 Cookie 头")
    fail += 0 if ok else 1
    return fail


def main():
    fail = check_regex()
    fail += check_filter()
    fail += check_cookie_header()
    fail += check_risk_control_retry()
    fail += asyncio.run(check_live())[0]
    print(f"\n=== 失败项: {fail} ===")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
