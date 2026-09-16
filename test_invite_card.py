import asyncio
import sys

sys.path.insert(0, "/tmp/opencode/chen-abot")
import main as m


class FakeChannel:
    def __init__(self, name, cid=1):
        self.name = name
        self.id = cid


class FakeGuild:
    def __init__(self, gid=999, channels=None):
        self.id = gid
        self.owner_id = 1
        self.text_channels = channels or []
        self.roles = []


class FakeResponse:
    def __init__(self):
        self.sent = []
        self.deferred = False

    async def send_message(self, content=None, **kwargs):
        self.sent.append(content if content is not None else kwargs)

    async def defer(self, **kwargs):
        self.deferred = True


class FakeInteraction:
    def __init__(self, guild, user_id=1):
        self.guild = guild
        self.user = type("U", (), {"id": user_id, "display_name": "tester", "mention": "<@1>"})()
        self.response = FakeResponse()

    async def followup(self):
        raise AssertionError("不应走到 followup")


fail = 0


def check(cond, label):
    global fail
    print(f"[{'OK ' if cond else 'FAIL'}] {label}")
    if not cond:
        fail += 1


def test_card():
    m.invite_data = {
        "_settings": {},
        "999": {
            "abc123": {"code": "abc123", "inviter_id": "1", "inviter_name": "阿蔡", "joined": [{"user_name": "x"}]},
            "def456": {"code": "def456", "inviter_id": "2", "inviter_name": "小明", "joined": []},
        },
    }
    guild = FakeGuild()
    embed = m._build_invite_card(guild)
    check(embed.title == "邀请函发送处", f"卡片标题: {embed.title}")
    desc = embed.description
    check("**2** 条" in desc, "卡片统计邀请条数 = 2")
    check("**1** 人" in desc, "卡片统计带来成员 = 1")
    check("岛主" in desc, "未设身份组时显示生成权限=岛主")
    check("有效期最长 7 天" in (embed.footer.text or ""), f"页脚: {embed.footer.text}")


def test_channel_keyword():
    guild = FakeGuild(channels=[FakeChannel("闲聊"), FakeChannel("邀请函发送处", 42)])
    ch = m._invite_card_channel_of(guild)
    check(ch is not None and ch.id == 42, f"频道匹配: {getattr(ch, 'name', None)}")
    guild2 = FakeGuild(channels=[FakeChannel("闲聊")])
    check(m._invite_card_channel_of(guild2) is None, "无匹配频道时返回 None")


def test_view():
    view = m.PersistentInviteView()
    labels = [c.label for c in view.children]
    ids = [getattr(c, "custom_id", None) for c in view.children]
    expected = ["生成邀请函", "我的邀请函", "邀请记录", "邀请设置", "作废邀请"]
    check(labels == expected, f"按钮顺序: {labels}")
    check(all(i for i in ids), f"全部有 custom_id: {ids}")
    check(view.timeout is None, "视图无超时，可重启恢复")
    rows = sorted({c.row for c in view.children})
    check(rows == [0, 1, 2], f"按钮分三行: {rows}")


def test_create_validation():
    async def run():
        modal = m.InviteCreateModal()
        modal.hours._value = "abc"
        it = FakeInteraction(FakeGuild())
        await modal.on_submit(it)
        check("数字" in (it.response.sent[-1] or ""), f"非数字被拦: {it.response.sent[-1]}")

        modal = m.InviteCreateModal()
        modal.hours._value = str(m.INVITE_MAX_HOURS + 1)
        it = FakeInteraction(FakeGuild())
        await modal.on_submit(it)
        check("0 到 168" in (it.response.sent[-1] or ""), f"有效期超限被拦: {it.response.sent[-1]}")

        modal = m.InviteCreateModal()
        modal.hours._value = "1"
        modal.max_uses._value = str(m.INVITE_MAX_USES_LIMIT + 1)
        it = FakeInteraction(FakeGuild())
        await modal.on_submit(it)
        check("0 到 100" in (it.response.sent[-1] or ""), f"次数超限被拦: {it.response.sent[-1]}")

    asyncio.run(run())


def test_defaults():
    modal = m.InviteCreateModal()
    check(modal.hours.default == str(m.INVITE_DEFAULT_HOURS), f"默认有效期: {modal.hours.default}")
    check(modal.max_uses.default == "0", f"默认次数: {modal.max_uses.default}")


def test_modal_kinds():
    check(isinstance(m.InviteCreateModal(), __import__("discord").ui.Modal), "生成弹窗是 Modal")
    check(isinstance(m.InviteSettingsModal(), __import__("discord").ui.Modal), "设置弹窗是 Modal")
    check(isinstance(m.InviteRecordsModal(), __import__("discord").ui.Modal), "记录弹窗是 Modal")
    check(isinstance(m.InviteRevokeModal(), __import__("discord").ui.Modal), "作废弹窗是 Modal")


def test_cache_key_and_persistence():
    m._invite_cache = {}
    key = m._invite_cache_key(999, "abc123")
    check(key == "999:abc123", f"缓存键格式: {key}")
    m._invite_cache[key] = 5
    m.save_invite_cache()
    m._invite_cache = {}
    m.load_invite_cache()
    check(m._invite_cache.get(key) == 5, f"缓存落盘后可恢复: {m._invite_cache.get(key)}")


def test_unknown_join_recorded():
    m.invite_data = {"_settings": {}, "999": {}}
    member = type("M", (), {"id": 7, "display_name": "路人", "guild": FakeGuild()})()
    m._record_unknown_join(member.guild, member, "同时命中多条邀请", candidates=["a", "b"])
    bucket = m.invite_data["999"].get("_unknown")
    check(bucket is not None, "存疑加入进入 _unknown 桶")
    item = (bucket or {}).get("joined", [{}])[-1]
    check(item.get("user_id") == "7", f"留痕用户: {item.get('user_id')}")
    check(item.get("reason") == "同时命中多条邀请", f"留痕原因: {item.get('reason')}")
    check(item.get("candidates") == ["a", "b"], f"留痕候选: {item.get('candidates')}")


def test_isolation_from_inviter_lookup():
    m.invite_data = {
        "_settings": {},
        "999": {
            "_unknown": {"code": "_unknown", "inviter_id": "", "inviter_name": "未溯源", "joined": [{"user_name": "x"}]},
            "abc123": {"code": "abc123", "inviter_id": "", "inviter_name": "阿蔡", "joined": []},
        },
    }
    recs = m._invite_records_of_user(999, "")
    check(all(code != "_unknown" for code, _ in recs), f"空 inviter 不匹配 _unknown: {[c for c, _ in recs]}")
    embed = m._build_invite_card(FakeGuild())
    check("未能溯源" in embed.description, "卡片展示未溯源人数")


test_card()
test_channel_keyword()
test_view()
test_defaults()
test_create_validation()
test_modal_kinds()
test_cache_key_and_persistence()
test_unknown_join_recorded()
test_isolation_from_inviter_lookup()
print(f"\n=== 失败项: {fail} ===")
sys.exit(1 if fail else 0)
