import sys

sys.path.insert(0, "/tmp/opencode/chen-abot")
import main as m


class FakeRole:
    def __init__(self, rid, name, members=None):
        self.id = rid
        self.name = name
        self.members = members or []


class FakeChannel:
    def __init__(self, name, cid=1):
        self.name = name
        self.id = cid
        self.type = __import__("discord").ChannelType.text


class FakeGuild:
    def __init__(self, gid=999, channels=None, roles=None):
        self.id = gid
        self.owner_id = 1
        self.text_channels = channels or []
        self.roles = roles or []

    def get_role(self, rid):
        for role in self.roles:
            if role.id == rid:
                return role
        return None

    def get_channel(self, cid):
        for channel in self.text_channels:
            if channel.id == cid:
                return channel
        return None


fail = 0


def check(cond, label):
    global fail
    print(f"[{'OK ' if cond else 'FAIL'}] {label}")
    if not cond:
        fail += 1


def test_empty_card():
    m.role_claim_data = {}
    guild = FakeGuild()
    embed = m._build_role_claim_embed(guild)
    check(embed.title == "🎭 领身份组", f"卡片标题: {embed.title}")
    check("还没配置" in embed.description, "空面板提示去 /添加身份")
    view = m.PersistentIdentityView(guild)
    check(len(view.children) == 0, f"空面板无按钮: {len(view.children)}")


def test_card_with_roles():
    m.role_claim_data = {
        "_settings": {
            "999": {
                "roles": [
                    {"role_id": "111", "label": "听歌的", "need_pass": True, "style": "primary"},
                    {"role_id": "222", "label": "夜猫", "need_pass": False, "description": "晚睡的来"},
                ]
            }
        }
    }
    roles = [FakeRole(111, "听歌的", [1, 2]), FakeRole(222, "夜猫")]
    guild = FakeGuild(roles=roles)
    embed = m._build_role_claim_embed(guild)
    check("听歌的" in embed.description, "卡片列出听歌的")
    check("需过审" in embed.description, "默认需过审")
    check("人人可领" in embed.description, "夜猫人人可领")
    check("晚睡的来" in embed.description, "说明出现在卡片")
    check("2 人" in embed.description, "人数统计")
    view = m.PersistentIdentityView(guild)
    check(len(view.children) == 2, f"两个身份对应两个按钮: {len(view.children)}")
    ids = []
    for child in view.children:
        inner = getattr(child, "item", child)
        ids.append(getattr(inner, "custom_id", None) or getattr(child, "custom_id", None))
    check(ids == ["identity_toggle:111", "identity_toggle:222"], f"custom_id: {ids}")
    check(view.timeout is None, "视图无超时")


def test_channel_lookup():
    guild = FakeGuild(channels=[FakeChannel("闲聊"), FakeChannel("领取身份", 42)])
    m.role_claim_data = {}
    ch = m._role_claim_channel_of(guild)
    check(ch is not None and ch.id == 42, f"按名称关键词匹配: {getattr(ch, 'name', None)}")

    m.role_claim_data = {"_settings": {"999": {"channel_id": "77"}}}
    guild2 = FakeGuild(channels=[FakeChannel("闲聊", 3), FakeChannel("身份领取处", 77)])
    ch2 = m._role_claim_channel_of(guild2)
    check(ch2 is not None and ch2.id == 77, f"优先用记下的频道 ID: {getattr(ch2, 'id', None)}")


def test_entry_lookup():
    m.role_claim_data = {
        "_settings": {
            "999": {
                "roles": [
                    {"role_id": "111", "label": "听歌的"},
                ]
            }
        }
    }
    rec = m._role_entry_of(999, "111")
    check(rec is not None and rec.get("label") == "听歌的", "按 role_id 找到条目")
    rec2 = m._role_entry_of(999, "听歌的")
    check(rec2 is rec, "按显示名也能找到")
    check(m._role_entry_of(999, "不存在") is None, "找不到返回 None")


def test_no_stream_helpers():
    check(not hasattr(m, "_resolve_bilibili"), "已移除 B站解析")
    check(not hasattr(m, "_add_stream_track"), "已移除流媒体入库")
    check(not hasattr(m, "_probe_stream_info"), "已移除 yt-dlp 探测")


test_empty_card()
test_card_with_roles()
test_channel_lookup()
test_entry_lookup()
test_no_stream_helpers()
print(f"\n=== 失败项: {fail} ===")
sys.exit(1 if fail else 0)
