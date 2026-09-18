import sys

sys.path.insert(0, "/tmp/opencode/chen-abot")
import main as m

fail = 0


def check(cond, label):
    global fail
    print(f"[{'OK ' if cond else 'FAIL'}] {label}")
    if not cond:
        fail += 1


def test_stream_removed():
    check(not hasattr(m, "_resolve_bilibili"), "无 _resolve_bilibili")
    check(not hasattr(m, "_resolve_stream"), "无 _resolve_stream")
    check(not hasattr(m, "_add_stream_track"), "无 _add_stream_track")
    check(not hasattr(m, "MUSIC_BILI_RE"), "无 MUSIC_BILI_RE")
    check(not hasattr(m, "MUSIC_STREAM_RE"), "无 MUSIC_STREAM_RE")
    check(bool(m.MUSIC_URL_RE.search("https://www.bilibili.com/video/BV1")), "链接仍能被识别以便提示用户")


test_stream_removed()
print(f"\n=== 失败项: {fail} ===")
sys.exit(1 if fail else 0)
