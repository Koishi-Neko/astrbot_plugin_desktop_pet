import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from pet_memory import (
    PetMemoryStore,
    blend_score,
    blob_to_vec,
    build_diary_prompt,
    build_injection,
    build_reflect_prompt,
    extract_terms,
    norm_text,
    parse_dt,
    parse_memories_json,
    strip_leading_tags,
    vec_to_blob,
)
from main import DesktopPetBridge


@pytest.fixture()
def store(tmp_path):
    return PetMemoryStore(tmp_path / "memory.db")


# ---------- 纯函数 ----------


def test_parse_memories_json_plain():
    raw = '[{"kind": "profile", "content": "主人叫小智", "importance": 5}]'
    items = parse_memories_json(raw)
    assert items == [{"kind": "profile", "content": "主人叫小智", "importance": 5}]


def test_parse_memories_json_fenced_and_noisy():
    raw = '好的，抽取结果如下：\n```json\n[{"kind":"event","content":"主人在准备答辩","importance":4}]\n```\n完毕'
    items = parse_memories_json(raw)
    assert len(items) == 1 and items[0]["kind"] == "event"


def test_parse_memories_json_bad_inputs():
    assert parse_memories_json("") == []
    assert parse_memories_json("没有可抽取的内容") == []
    assert parse_memories_json('{"a": 1}') == []
    assert parse_memories_json("[1, 2, null]") == []
    # 未知 kind 归一为 fact，importance 钳位 1~5，坏行跳过
    items = parse_memories_json(
        '[{"kind":"alien","content":"x 轴","importance":99}, {"kind":"fact"},'
        ' {"kind":"mood","content":"主人有点累","importance":0}]'
    )
    assert items == [
        {"kind": "fact", "content": "x 轴", "importance": 5},
        {"kind": "mood", "content": "主人有点累", "importance": 1},
    ]


def test_parse_memories_json_dict_form():
    items = parse_memories_json('{"memories": [{"kind":"mood","content":"主人很开心","importance":2}]}')
    assert len(items) == 1 and items[0]["kind"] == "mood"


def test_norm_and_terms():
    assert norm_text(" 主人 A B\n") == "主人ab"
    assert "生日" in extract_terms("主人的生日是什么时候")
    assert "主人的" in extract_terms("主人的生日是什么时候") or "主人" in extract_terms("主人的生日是什么时候")
    assert "desktop_pet" in extract_terms("Desktop_Pet 项目")
    assert extract_terms("哦") == []


def test_strip_leading_tags():
    assert strip_leading_tags("【高兴】今天真开心") == "今天真开心"
    assert strip_leading_tags("【平静】【略过】") == ""
    assert strip_leading_tags("没有标签") == "没有标签"


def test_blend_score_monotonic():
    base = blend_score(0.5, 3, 10, 0, 0)
    assert blend_score(0.8, 3, 10, 0, 0) > base
    assert blend_score(0.5, 5, 10, 0, 0) > base
    assert blend_score(0.5, 3, 0, 0, 0) > base
    assert blend_score(0.5, 3, 10, 5, 2) > base


def test_build_injection_budget():
    mems = [
        {"kind": "profile", "content": "主人叫小智", "created_at": "2026-09-01 10:00:00"},
        {"kind": "event", "content": "主人在写论文" * 30, "created_at": "2026-09-02 10:00:00"},
    ]
    block = build_injection(mems, 60)
    assert "主人叫小智" in block
    assert "桌宠记忆" in block
    assert "主人在写论文" not in block  # 超预算被截断
    assert build_injection([], 800) == ""


def test_vec_blob_roundtrip():
    v = [0.1, -0.2, 0.3, 0.9]
    out = blob_to_vec(vec_to_blob(v))
    assert len(out) == 4
    assert abs(sum(x * x for x in out) - 1.0) < 1e-5  # 已归一化
    assert out[0] > 0 and out[1] < 0


def test_parse_dt():
    assert parse_dt("2026-09-01 10:00:00") is not None
    assert parse_dt("2026-09-01T10:00:00") is not None
    assert parse_dt("garbage") is None
    assert parse_dt(None) is None


def test_prompt_builders():
    rows = [(1, "user", "我今天生日", "2026-09-10 20:00:00"), (2, "assistant", "生日快乐", "2026-09-10 20:00:05")]
    p = build_reflect_prompt(rows, "小智")
    assert "小智" in p and "我今天生日" in p
    d = build_diary_prompt(rows, "小智", "2026-09-10")
    assert "2026-09-10" in d and "日记" in d


# ---------- PetMemoryStore（tmp sqlite） ----------


def test_store_crud_and_cursor(store):
    store.log_message("user", "你好")
    store.log_message("assistant", "你好呀主人")
    assert store.unreflected_pairs_count() == 1
    rows, max_id = store.unreflected_window(8)
    assert len(rows) == 2
    store.mark_reflected(max_id)
    assert store.unreflected_pairs_count() == 0
    assert store.unreflected_window(8)[0] == []

    mid = store.add_memory("profile", "主人叫小智", 5, "chat")
    assert store.find_duplicate("主人叫 小智") is not None  # 规范化去重
    assert store.find_duplicate("主人爱喝可乐") is None
    store.touch_recalled([mid])
    item, total = store.list_memories("小智")
    assert total == 1 and item[0]["recall_count"] == 1 and item[0]["last_recalled_at"]
    assert store.set_importance(mid, 4)
    assert store.list_memories("小智")[0][0]["importance"] == 4
    assert store.deactivate(mid)
    assert store.list_memories("小智")[1] == 0


def test_store_skip_pair(store):
    store.log_message("user", "（场景）主人在看视频")
    store.delete_last_user_message()
    assert store.unreflected_pairs_count() == 0


def test_store_stats_and_decay(store):
    store.add_memory("profile", "主人叫小智", 5, "chat")
    store.add_memory("event", "主人在准备答辩", 4, "chat")
    st = store.stats()
    assert st["total_active"] == 2
    assert st["by_kind"]["profile"] == 1
    assert st["missing_embedding"] == 2
    # 人为造陈旧数据验证衰减：直接改 created_at（保持 ISO 文本格式）
    old = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    older = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(store.db_path))
    con.execute("UPDATE memories SET created_at=? WHERE kind='event'", (old,))
    con.execute("UPDATE memories SET created_at=?, importance=1 WHERE kind='profile'", (older,))
    con.commit()
    con.close()
    decayed, archived = store.apply_decay()
    assert decayed == 1  # event 4→3
    assert archived == 1  # 200 天未召回的 1 分 profile 软删
    st = store.stats()
    assert st["total_active"] == 1


def test_store_diary_log_query(store):
    today = datetime.now().strftime("%Y-%m-%d")
    store.log_message("user", "早上好")
    rows = store.chat_log_of_date(today)
    assert len(rows) == 1
    assert store.chat_log_of_date("1999-01-01") == []


def test_import_livingmemory(store, tmp_path):
    # 造一个迷你 LivingMemory 库：桌宠会话 + 主人私聊 + 陌生人私聊 + 群聊
    lm = tmp_path / "livingmemory.db"
    con = sqlite3.connect(str(lm))
    con.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)")
    meta1 = json.dumps({"session_id": "webchat:FriendMessage:webchat!desktop_pet!desktop_pet", "importance": 0.9})
    meta2 = json.dumps({"session_id": "napcat:FriendMessage:1819987185", "importance": 0.5})
    meta3 = json.dumps({"session_id": "napcat:FriendMessage:999999", "importance": 0.5})
    meta4 = json.dumps({"session_id": "napcat:GroupMessage:123", "importance": 0.5})
    con.execute("INSERT INTO documents(text, metadata) VALUES (?, ?)", ("desktop_pet 喜欢智乃", meta1))
    con.execute("INSERT INTO documents(text, metadata) VALUES (?, ?)", ("1819987185 的生日是 6 月 1 日", meta2))
    con.execute("INSERT INTO documents(text, metadata) VALUES (?, ?)", ("陌生人的事", meta3))
    con.execute("INSERT INTO documents(text, metadata) VALUES (?, ?)", ("群友约定周五开黑", meta4))
    con.commit()
    con.close()
    result = store.fetch_livingmemory_candidates(lm, "desktop_pet", "1819987185", "小智")
    assert result["error"] is None
    assert result["found"] == 4  # 全量扫描
    by_scope = {it["scope"]: it for it in result["items"]}
    assert by_scope["pet"]["content"] == "小智 喜欢智乃"
    assert by_scope["pet"]["importance"] == 5  # 0.9 → 5
    assert by_scope["private"]["content"] == "小智 的生日是 6 月 1 日"  # QQ 号改写为主人称呼
    assert by_scope["group"]["content"] == "群友约定周五开黑"
    assert result["skipped_sessions"] == {"napcat:FriendMessage:999999": 1}  # 陌生人私聊跳过
    # 不存在的库
    bad = store.fetch_livingmemory_candidates(tmp_path / "nope.db", "desktop_pet", "1819987185", "小智")
    assert bad["error"] and bad["items"] == []


def test_vector_path_if_faiss(store):
    pytest.importorskip("faiss")
    pytest.importorskip("numpy")
    assert store.vector_available
    v1 = [1.0] + [0.0] * 15
    v2 = [0.9, 0.1] + [0.0] * 14
    v3 = [0.0, 1.0] + [0.0] * 14
    m1 = store.add_memory("fact", "主人喜欢苹果", 3, "chat", v1, "test-emb")
    m2 = store.add_memory("fact", "主人喜欢苹果手机", 3, "chat", v2, "test-emb")
    store.add_memory("fact", "主人讨厌下雨", 3, "chat", v3, "test-emb")
    assert store.ensure_index("test-emb", 16)
    hits = store.vec_search(v1, 3)
    assert hits[0][0] == m1  # 完全同向排第一
    assert hits[1][0] == m2  # 近似的第二
    store.deactivate(m1)
    hits = store.vec_search(v1, 3)
    assert all(mid != m1 for mid, _ in hits)  # 软删后索引同步移除
    # 换模型触发重建
    assert store.ensure_index("other-emb", 16)
    assert store.vec_search(v1, 3) == []  # 旧模型向量不进新索引


# ---------- main.py 侧引擎（stub provider） ----------


class _FakeResp:
    def __init__(self, text):
        self.completion_text = text


class _FakeLLM:
    def __init__(self, text):
        self._text = text
        self.calls = []

    async def text_chat(self, prompt=None, system_prompt=None, **kw):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        return _FakeResp(self._text)


def _make_bridge(tmp_path, llm_text="[]", master_name="小智"):
    bridge = DesktopPetBridge(MagicMock())
    bridge.config = {
        "memory_enabled": True,
        "master_name": master_name,
        "pet_session_id": "desktop_pet",
        "memory_reflect_rounds": 8,
    }
    bridge._mem_store = PetMemoryStore(tmp_path / "memory.db")
    bridge._mem_reflect_task = None
    llm = _FakeLLM(llm_text)
    bridge.context.get_using_provider_async = AsyncMock(return_value=llm)
    bridge.context.get_provider_by_id = MagicMock(return_value=None)
    bridge.context.get_all_embedding_providers = MagicMock(return_value=[])
    return bridge, llm


def test_reflect_flow_no_embedding(tmp_path):
    llm_text = (
        '[{"kind":"profile","content":"主人叫 desktop_pet","importance":5},'
        '{"kind":"event","content":"主人在准备升职答辩","importance":4},'
        '{"kind":"event","content":"主人在准备升职答辩","importance":4}]'
    )
    bridge, llm = _make_bridge(tmp_path, llm_text)
    for i in range(8):
        bridge._mem_store.log_message("user", f"消息 {i}")
        bridge._mem_store.log_message("assistant", f"回复 {i}")
    asyncio.run(bridge._memory_reflect())
    items, total = bridge._mem_store.list_memories("")
    contents = [m["content"] for m in items]
    # desktop_pet 被改写为主人称呼；批内+库内去重后只落一条
    assert any("小智" in c and "desktop_pet" not in c for c in contents)
    assert contents.count("主人在准备升职答辩") == 1
    assert total == 2
    assert bridge._mem_store.unreflected_pairs_count() == 0  # 光标前进
    assert bridge._mem_store.get_meta("last_reflect_at")
    assert "小智" in llm.calls[0]["prompt"]


def test_reflect_failure_skips_without_backlog(tmp_path):
    bridge, llm = _make_bridge(tmp_path, "这不是 JSON")
    bridge._mem_store.log_message("user", "hi")
    bridge._mem_store.log_message("assistant", "hello")
    asyncio.run(bridge._memory_reflect())
    assert bridge._mem_store.list_memories("")[1] == 0
    assert bridge._mem_store.unreflected_pairs_count() == 0  # 失败也跳过，不积压


def test_recall_block_fallback_no_embedding(tmp_path):
    """无 embedding provider：降级为重要度+时效召回，且档案常驻。"""
    bridge, _ = _make_bridge(tmp_path)
    bridge._mem_store.add_memory("profile", "主人叫小智", 5, "chat")
    bridge._mem_store.add_memory("event", "主人在准备升职答辩", 4, "chat")
    bridge._mem_store.add_memory("fact", "主人今天午饭吃了拉面", 1, "chat")
    result = asyncio.run(bridge._memory_recall_block("最近在忙什么"))
    assert result is not None and result["vector"] is False
    block = result["block"]
    assert "主人叫小智" in block  # 档案常驻
    assert "升职答辩" in block  # 高重要度优先
    # dry 不更新召回统计
    before = [m["recall_count"] for m in bridge._mem_store.list_memories("")[0]]
    asyncio.run(bridge._memory_recall_block("测试", dry=True))
    after = [m["recall_count"] for m in bridge._mem_store.list_memories("")[0]]
    assert before == after
    # 非 dry 会更新召回统计
    asyncio.run(bridge._memory_recall_block("再测", dry=False))
    assert any(m["recall_count"] >= 1 for m in bridge._mem_store.list_memories("")[0])


def test_memory_disabled_hook_noop(tmp_path):
    bridge, _ = _make_bridge(tmp_path)
    bridge.config["memory_enabled"] = False
    bridge._mem_store.log_message("user", "不应被反思")  # 直接落库绕开钩子开关
    # 钩子本体被装饰器 mock 掉，无法直接调；这里验证引擎开关语义：
    # disabled 时 main.py 钩子入口直接 return（由 _memory_enabled 把关）
    assert bridge._memory_enabled() is False


# ---------- 多范围（scope） ----------

from types import SimpleNamespace

from pet_memory import (
    MEMORY_SCOPES,
    REFLECT_SYSTEM_GROUP_ADDENDUM,
    pool_scopes,
    scope_of_lm_session,
)


def test_pool_scopes_semantics():
    assert pool_scopes("pet", frozenset()) == ["pet", "private", "group"]  # 全混合
    assert pool_scopes("group", frozenset()) == ["pet", "private", "group"]
    assert pool_scopes("group", frozenset({"group"})) == ["group"]  # 自己独立
    assert pool_scopes("pet", frozenset({"group"})) == ["pet", "private"]  # 别人独立
    assert pool_scopes("pet", frozenset({"group", "private"})) == ["pet"]
    assert pool_scopes("pet", frozenset({"pet", "group"})) == ["pet"]


def test_scope_of_lm_session():
    pet = scope_of_lm_session("webchat:FriendMessage:webchat!desktop_pet!desktop_pet", "desktop_pet", "1819987185")
    assert pet == "pet"
    assert scope_of_lm_session("napcat:FriendMessage:1819987185", "desktop_pet", "1819987185") == "private"
    assert scope_of_lm_session("napcat:FriendMessage:999999", "desktop_pet", "1819987185") is None  # 陌生人私聊
    assert scope_of_lm_session("aiocqhttp:GroupMessage:123", "desktop_pet", "1819987185") == "group"
    assert scope_of_lm_session("", "desktop_pet", "1819987185") is None
    assert scope_of_lm_session("napcat:FriendMessage:1819987185", "desktop_pet", "") is None  # 未配 master_qq 不猜


def test_schema_migration_adds_scope(tmp_path):
    """旧 schema（无 scope 列）的库打开后自动补列，存量数据归为 pet，且幂等。"""
    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 3,
            source TEXT NOT NULL DEFAULT 'chat',
            embedding BLOB, emb_dim INTEGER, emb_model TEXT,
            created_at TEXT NOT NULL,
            last_recalled_at TEXT,
            recall_count INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE chat_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    con.execute("INSERT INTO memories(kind, content, created_at) VALUES ('fact', '旧记忆', '2026-01-01 00:00:00')")
    con.execute("INSERT INTO chat_log(role, content, created_at) VALUES ('user', '旧消息', '2026-01-01 00:00:00')")
    con.commit()
    con.close()
    store = PetMemoryStore(db)
    item, total = store.list_memories("")
    assert total == 1 and item[0]["scope"] == "pet"
    rows = store.chat_log_of_date("2026-01-01")
    assert len(rows) == 1 and rows[0][4] == "pet"
    store2 = PetMemoryStore(db)  # 幂等：重复打开不报错
    assert store2.list_memories("")[1] == 1


def test_log_message_scope_and_scoped_delete(store):
    store.log_message("user", "桌宠消息", "pet")
    store.log_message("user", "群聊消息", "group")
    store.log_message("assistant", "群聊回复", "group")
    store.delete_last_user_message("pet")  # 只删 pet 的最后一条 user
    rows, _ = store.unreflected_window(10)
    contents = [(r[1], r[2], r[4]) for r in rows]
    assert ("user", "群聊消息", "group") in contents
    assert ("user", "桌宠消息", "pet") not in contents


def test_scope_filtered_queries(store):
    p1 = store.add_memory("profile", "主人叫小智", 5, "chat", scope="pet")
    p2 = store.add_memory("profile", "主人在私聊说过怕黑", 4, "chat", scope="private")
    e1 = store.add_memory("event", "群里周五开黑", 3, "chat", scope="group")
    e2 = store.add_memory("event", "主人桌宠聊天", 3, "chat", scope="pet")
    # 混合池（无独立）：全见
    mixed = ["pet", "private", "group"]
    assert set(store.get_memories_by_ids([p1, p2, e1, e2], mixed)) == {p1, p2, e1, e2}
    # 群独立池：只见 group
    assert set(store.get_memories_by_ids([p1, p2, e1, e2], ["group"])) == {e1}
    assert [m["id"] for m in store.profile_memories(5, ["group"])] == []
    assert {m["id"] for m in store.profile_memories(5, mixed)} == {p1, p2}
    assert {m["id"] for m in store.recent_active(10, ("profile",), ["pet"])} == {e2}
    # list_memories 的 scope 筛选
    assert store.list_memories("", scope="group")[1] == 1
    assert store.list_memories("", scope="")[1] == 4
    # stats 带 by_scope
    assert store.stats()["by_scope"] == {"pet": 2, "private": 1, "group": 1}


def test_build_injection_scope_preface():
    mems = [{"kind": "fact", "content": "主人喜欢智乃", "created_at": "2026-09-01 10:00:00"}]
    pet_block = build_injection(mems, 800, scope="pet")
    group_block = build_injection(mems, 800, scope="group")
    assert "你与主人之间过去的记忆" in pet_block
    assert "从过往聊天中记住的事情" in group_block
    assert "主人喜欢智乃" in group_block


def test_build_reflect_prompt_group():
    rows = [(1, "user", "小明: 周五开黑吗", "2026-09-10 20:00:00", "group")]
    p = build_reflect_prompt(rows, "小智", scope="group")
    assert "群成员" in p and "小明: 周五开黑吗" in p
    assert "群聊补充规则" in REFLECT_SYSTEM_GROUP_ADDENDUM
    p2 = build_reflect_prompt(rows, "小智", scope="pet")
    assert "小智：" in p2  # 非群聊仍用主人称呼


def test_scope_classifier(tmp_path):
    bridge, _ = _make_bridge(tmp_path)
    bridge.config["master_qq"] = "1819987185"
    # 桌宠 umo
    ev = MagicMock()
    ev.unified_msg_origin = "webchat:FriendMessage:webchat!desktop_pet!desktop_pet"
    assert bridge._memory_scope_of(ev) == "pet"
    # 主人私聊（get_message_type 返回带 value 的枚举式对象）
    ev2 = MagicMock()
    ev2.unified_msg_origin = "napcat:FriendMessage:1819987185"
    ev2.get_message_type.return_value = SimpleNamespace(value="FriendMessage")
    ev2.get_sender_id.return_value = "1819987185"
    assert bridge._memory_scope_of(ev2) == "private"
    # 陌生人私聊 → None
    ev2.get_sender_id.return_value = "999999"
    assert bridge._memory_scope_of(ev2) is None
    # 私聊开关关 → None
    bridge.config["memory_scope_private_enabled"] = False
    ev2.get_sender_id.return_value = "1819987185"
    assert bridge._memory_scope_of(ev2) is None
    bridge.config["memory_scope_private_enabled"] = True
    # 群聊（get_message_type 直接返回字符串的兜底路径）
    ev3 = MagicMock()
    ev3.unified_msg_origin = "napcat:GroupMessage:700542954"
    ev3.get_message_type.return_value = "GroupMessage"
    assert bridge._memory_scope_of(ev3) == "group"
    # 群聊开关关 → None
    bridge.config["memory_scope_group_enabled"] = False
    assert bridge._memory_scope_of(ev3) is None
    # 异常兜底
    ev4 = MagicMock()
    ev4.unified_msg_origin = "weird"
    ev4.get_message_type.side_effect = RuntimeError("boom")
    assert bridge._memory_scope_of(ev4) is None


def test_reflect_groups_by_scope(tmp_path):
    """混合 scope 的 chat_log 按组反思，产物落对应 scope。"""
    bridge, llm = _make_bridge(tmp_path, "[]")

    async def chat(prompt=None, system_prompt=None, **kw):
        llm.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if "群成员" in (prompt or ""):
            return _FakeResp('[{"kind":"fact","content":"群里约周五开黑","importance":3}]')
        return _FakeResp('[{"kind":"fact","content":"主人桌宠日常","importance":3}]')

    llm.text_chat = chat
    bridge._mem_store.log_message("user", "桌宠说", "pet")
    bridge._mem_store.log_message("assistant", "桌宠回", "pet")
    bridge._mem_store.log_message("user", "小明: 群里说", "group")
    bridge._mem_store.log_message("assistant", "群里回", "group")
    asyncio.run(bridge._memory_reflect())
    assert len(llm.calls) == 2  # pet / group 各一次 LLM 调用
    items, total = bridge._mem_store.list_memories("")
    assert total == 2
    scopes = {m["content"]: m["scope"] for m in items}
    assert scopes == {"主人桌宠日常": "pet", "群里约周五开黑": "group"}
    group_call = [c for c in llm.calls if "群成员" in c["prompt"]]
    assert group_call and "群聊补充规则" in group_call[0]["system_prompt"]
