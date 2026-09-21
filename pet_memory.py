"""pet_memory —— 桌宠插件内置长期记忆（向量召回版）。

与 AstrBot 解耦的纯存储/检索引擎：不 import 任何 astrbot 模块，
embedding / LLM provider 全部由调用方（main.py）注入，便于单元测试。

- 存储：stdlib sqlite3（memory.db），向量以 float32 小端 BLOB 落库（真值来源）。
- 索引：faiss IndexIDMap2(IndexFlatIP)，惰性 import，启动/换模型时从库重建，
  纯内存不落盘——索引是衍生物，损坏即重建，不存在"索引与库不同步"。
- 降级：无 faiss / 无 embedding provider / 向量缺失时自动退化为
  「重要度 + 时效」召回，任何情况下记忆功能都可用。
- 时间戳一律 ISO 文本（%Y-%m-%d %H:%M:%S），绝不写 float（避免解析灾难）。
- 范围（scope）：pet / private / group。每条记忆与每行 chat_log 记录来源 scope；
  召回池由「独立开关」在召回时动态计算（pool_scopes），切开关即重分区存量数据。
- 反思光标按 scope 拆分（last_reflected_log_id_{scope}），各范围独立攒批触发。
- 耐久度分级：重要度即保质期（1≈15 天 / 2≈30 / 3≈60 / 4≈120 天未召回降级，
  5 永久免疫，1 级超期软删），freshness 时钟 = COALESCE(last_recalled_at, created_at)。
- 「记住 xxx」/「永久记住 xxx」指令解析见 parse_remember_command（纯函数）。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import struct
from datetime import datetime
from pathlib import Path

MEMORY_KINDS = ("profile", "fact", "event", "mood", "promise", "scene", "diary")

KIND_LABELS = {
    "profile": "档案",
    "fact": "事实",
    "event": "事件",
    "mood": "心情",
    "promise": "约定",
    "scene": "观察",
    "diary": "日记",
}

MEMORY_SCOPES = ("pet", "private", "group")

SCOPE_LABELS = {
    "pet": "桌宠",
    "private": "私聊",
    "group": "群聊",
}

# 重要度驱动的耐久度分级：超过对应天数未召回即降 1 级（importance 5 永久免疫；
# 1 级超期软删）。freshness 时钟 = COALESCE(last_recalled_at, created_at)。
DECAY_TIER_DAYS = {4: 120, 3: 60, 2: 30, 1: 15}

_REMEMBER_RE = re.compile(r"^\s*(永久记住|永远记住|记住)\s*[:：,，]?\s*(.{2,})\s*$")


def parse_remember_command(text: str) -> tuple[bool, str] | None:
    """「记住 xxx」/「永久记住 xxx」指令解析；命中返回 (是否永久, 内容)，否则 None。

    「你还记得…」「记住啦」（内容不足 2 字）等不命中。
    """
    m = _REMEMBER_RE.match(text or "")
    if not m:
        return None
    return (m.group(1) != "记住", m.group(2).strip())


def pool_scopes(scope: str, independent) -> list[str]:
    """计算某范围当前可召回的记忆池：独立范围只见自己；否则见所有未独立范围。"""
    indep = frozenset(independent or ())
    if scope in indep:
        return [scope]
    return [s for s in MEMORY_SCOPES if s not in indep]


def scope_of_lm_session(session_id: str, pet_sid: str, master_qq: str):
    """LivingMemory session_id（umo 格式）→ 本库 scope；无法判定返回 None。"""
    sid = str(session_id or "")
    if not sid:
        return None
    if pet_sid and pet_sid in sid:
        return "pet"
    if ":FriendMessage:" in sid:
        if master_qq and sid.rsplit(":", 1)[-1].strip() == str(master_qq).strip():
            return "private"
        return None
    if ":GroupMessage:" in sid:
        return "group"
    return None


class GroupContextBuffer:
    """群聊前文滚动缓冲：每群保留最近 capacity 条消息，供触发 LLM 时拼接落库。

    被动监听（custom_filter 副作用）把群消息 push 进来；触发捕获时 render()
    渲染成 "[前文] 昵称: 内容" 行块。监听先于捕获执行，故 render 会剔除末尾
    与触发消息重复的条目。纯内存，不落盘、不进 chat_log 独立行。
    """

    def __init__(self, capacity: int = 10):
        self._capacity = max(0, int(capacity))
        self._bufs: dict[str, list] = {}

    def set_capacity(self, capacity: int) -> None:
        self._capacity = max(0, int(capacity))
        if self._capacity <= 0:
            self._bufs.clear()
            return
        for buf in self._bufs.values():
            if len(buf) > self._capacity:
                del buf[: len(buf) - self._capacity]

    def push(self, group_key: str, sender: str, text: str) -> None:
        if self._capacity <= 0:
            return
        group_key = str(group_key or "").strip()
        sender = str(sender or "").strip()
        text = str(text or "").strip()
        if not group_key or not text:
            return
        buf = self._bufs.setdefault(group_key, [])
        buf.append((sender, text))
        if len(buf) > self._capacity:
            del buf[: len(buf) - self._capacity]

    def render(
        self, group_key: str, trigger_sender: str = "", trigger_text: str = ""
    ) -> str:
        """渲染前文块；剔除末尾与触发消息重复的条目（无前文返回空串）。"""
        buf = self._bufs.get(str(group_key or "").strip())
        if not buf:
            return ""
        items = list(buf)
        trigger_sender = str(trigger_sender or "").strip()
        trigger_text = str(trigger_text or "").strip()
        if items and trigger_text:
            last_sender, last_text = items[-1]
            if last_sender == trigger_sender and (
                last_text == trigger_text
                or last_text in trigger_text
                or trigger_text in last_text
            ):
                items.pop()
        return "\n".join(f"[前文] {sender}: {text}" for sender, text in items)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 3,
    source TEXT NOT NULL DEFAULT 'chat',
    scope TEXT NOT NULL DEFAULT 'pet',
    embedding BLOB,
    emb_dim INTEGER,
    emb_model TEXT,
    created_at TEXT NOT NULL,
    last_recalled_at TEXT,
    recall_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active, kind);
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'pet',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# 老库升级：缺 scope 列则补（存量数据全部是桌宠会话产物，默认 pet 正确）
_MIGRATIONS = (
    ("memories", "scope", "ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'pet'"),
    ("chat_log", "scope", "ALTER TABLE chat_log ADD COLUMN scope TEXT NOT NULL DEFAULT 'pet'"),
)

_TIME_FMT = "%Y-%m-%d %H:%M:%S"

_TERM_RE = re.compile(r"[一-鿿]+|[A-Za-z0-9_]{2,}")
_WS_RE = re.compile(r"\s+")
_LEAD_TAG_RE = re.compile(r"^(\s*【[^】\n]{1,12}】)+")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def now_str() -> str:
    return datetime.now().strftime(_TIME_FMT)


def parse_dt(s):
    """宽容解析 ISO 文本时间，失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        try:
            return datetime.strptime(str(s)[:19], _TIME_FMT)
        except (ValueError, TypeError):
            return None


def norm_text(s: str) -> str:
    """规范化文本（去空白、小写），用于精确去重。"""
    return _WS_RE.sub("", s or "").lower()


def strip_leading_tags(text: str) -> str:
    """剥掉回复开头连续的【情绪】/【略过】等方括号标签。"""
    return _LEAD_TAG_RE.sub("", text or "").strip()


def extract_terms(text: str, limit: int = 16) -> list[str]:
    """提取查询里的候选词，用于召回的专名子串加成。

    拉丁/数字词整体保留；CJK 连续段 ≤4 字整体保留，更长则拆为滑动二字组
    （「主人的生日是什么时候」→ 主人/人的/的生/生日/…），保证中文专名可命中。
    """
    seen: list[str] = []
    for raw in _TERM_RE.findall(text or ""):
        t = raw.lower()
        if re.fullmatch(r"[A-Za-z0-9_]+", t):
            cands = [t]
        elif 2 <= len(t) <= 4:
            cands = [t]
        elif len(t) > 4:
            cands = [t[i : i + 2] for i in range(len(t) - 1)]
        else:
            cands = []
        for c in cands:
            if c not in seen:
                seen.append(c)
            if len(seen) >= limit:
                return seen
    return seen


def blend_score(
    cosine: float,
    importance: int,
    age_days: float,
    recall_count: int,
    substr_hits: int,
) -> float:
    """混合重排分：向量相似度为主，重要度/时效/召回强化/专名命中为辅。"""
    return (
        0.70 * cosine
        + 0.12 * max(0.0, min(1.0, (importance - 1) / 4.0))
        + 0.08 * math.exp(-max(0.0, age_days) / 30.0)
        + 0.05 * min(recall_count, 10) / 10.0
        + 0.05 * min(substr_hits, 3) / 3.0
    )


def build_injection(memories: list[dict], max_chars: int, scope: str = "pet") -> str:
    """把选中的记忆格式化为注入块；max_chars 为硬预算（按字符截断列表）。"""
    lines = []
    used = 0
    for m in memories:
        label = KIND_LABELS.get(m.get("kind"), "记忆")
        date = str(m.get("created_at") or "")[:10]
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        line = f"- [{date}·{label}] {content}"
        if lines and used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    if scope == "pet":
        preface = (
            "【桌宠记忆】以下是你与主人之间过去的记忆，仅供你自然地参考与呼应；"
            "不要逐字复述，也不要向主人提及「记忆」机制本身：\n"
        )
    else:
        preface = (
            "【长期记忆】以下是你从过往聊天中记住的事情，仅供你自然地参考与呼应；"
            "不要逐字复述，也不要向对方提及「记忆」机制本身：\n"
        )
    return preface + "\n".join(lines)


def parse_memories_json(raw: str) -> list[dict]:
    """把反思 LLM 的输出解析为 [{kind, content, importance}]。

    防御式：容忍 ```json 围栏、前后杂质文本、坏行跳过；解析失败返回 []。
    """
    if not raw:
        return []
    text = str(raw).strip()
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    data = None
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            data = None
    if data is None:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                data = obj.get("memories") or obj.get("items")
        except ValueError:
            data = None
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if len(content) < 2:
            continue
        kind = str(item.get("kind") or "fact").strip()
        if kind not in MEMORY_KINDS:
            kind = "fact"
        try:
            imp = int(item.get("importance", 3))
        except (TypeError, ValueError):
            imp = 3
        out.append({"kind": kind, "content": content[:300], "importance": min(5, max(1, imp))})
    return out[:20]


REFLECT_SYSTEM = (
    "你在为一只桌面桌宠整理它与主人的长期记忆。从对话片段中抽取值得长期记住的条目，"
    "输出严格的 JSON 数组，每个元素形如 {\"kind\": \"...\", \"content\": \"...\", \"importance\": 1-5}。\n"
    "kind 取值：\n"
    "- profile：主人的稳定档案（称呼、生日、喜好、习惯、重要的个人日期）\n"
    "- event：主人近期发生的事情或正在做的事（带具体日期）\n"
    "- mood：主人明显的情绪状态\n"
    "- promise：主人或桌宠许下的承诺、约定、待办\n"
    "- scene：桌宠观察到的值得记住的桌面情形（主人在用什么应用、看什么内容）\n"
    "- fact：其他值得记住的事实\n"
    "规则：\n"
    "- 只抽取有长期价值的条目；寒暄、闲聊、一次性问答不要抽取；没有值得记的就输出 []\n"
    "- content 用简洁的陈述句；提到主人时一律使用给出的主人称呼，绝对不要出现 "
    "\"desktop_pet\"、「用户」这类称呼\n"
    "- 相对时间（今天/昨天/下周）必须按对话时间戳换算成具体日期\n"
    "- importance：1=琐事（约 15 天后被遗忘）… 2=次要（约 30 天）… 3=普通（约 60 天）… "
    "4=重要（约 120 天）… 5=永久记住（永不遗忘，仅用于生日、重大约定、主人明确要求永久记住的事，慎用）\n"
    "- 最多 8 条，宁缺毋滥\n"
    "- 只输出 JSON 数组，不要输出任何其他文字"
)

REFLECT_SYSTEM_GROUP_ADDENDUM = (
    "\n群聊补充规则：\n"
    "- 对话来自群聊，user 行的发言者已以「昵称: 」前缀标出；事实必须归属到具体的人，"
    "content 中保留该人的称呼\n"
    "- 只有明确提及主人的条目才使用主人称呼；其他成员的事实写清成员昵称\n"
    "- 群聊里的灌水/表情包接龙/与任何人都无关的闲聊一律不抽取"
)


def build_reflect_prompt(rows: list, master_name: str, scope: str = "pet") -> str:
    """rows: [(id, role, content, created_at, scope)]；返回反思用户 prompt。"""
    name = master_name or "主人"
    lines = []
    for row in rows[:40]:
        _id, role, content, created_at = row[0], row[1], row[2], row[3]
        if scope == "group":
            who = "群成员" if role == "user" else "桌宠"
        else:
            who = name if role == "user" else "桌宠"
        lines.append(f"[{str(created_at)[:16]}] {who}：{str(content)[:300]}")
    if scope == "group":
        intro = (
            f"主人的称呼是「{name}」（如果发言者昵称与主人对应，用主人称呼）。"
            "以下是群聊里最近的对话片段，请按系统要求（含群聊补充规则）抽取长期记忆：\n\n"
        )
    else:
        intro = (
            f"主人的称呼是「{name}」。以下是桌宠与主人最近的对话片段，"
            "请按系统要求抽取长期记忆：\n\n"
        )
    return intro + "\n".join(lines)


def build_diary_prompt(rows: list, master_name: str, date_str: str) -> str:
    """rows: [(id, role, content, created_at, ...)]（容忍多余列）；返回日记用户 prompt。"""
    name = master_name or "主人"
    lines = []
    for row in rows[:60]:
        _id, role, content, created_at = row[0], row[1], row[2], row[3]
        who = name if role == "user" else "桌宠"
        lines.append(f"[{str(created_at)[:16]}] {who}：{str(content)[:200]}")
    return (
        f"以上是 {date_str} 你与主人（{name}）的对话记录。"
        "以桌宠第一人称写一段不超过 150 字的当日日记，记录与主人相处中值得记住的事；"
        "没有特别的事就写平常的一天。直接输出日记正文，不要输出任何其他内容。\n\n"
        + "\n".join(lines)
    )


# ---------- 向量工具（纯 Python，归一化后 float32 小端落库） ----------


def normalize_vec(vec) -> list[float]:
    v = [float(x) for x in vec]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def vec_to_blob(vec) -> bytes:
    v = normalize_vec(vec)
    return struct.pack("<%df" % len(v), *v)


def blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack("<%df" % n, blob))


class PetMemoryStore:
    """桌宠记忆的 sqlite + 内存 faiss 索引封装。全部方法同步阻塞，调用方按需 to_thread。"""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._index = None  # faiss.IndexIDMap2 或 None
        self._index_key = None  # (emb_model, dim)
        self._faiss_ok = None
        self._faiss = None
        self._np = None
        self._ensure_schema()

    # ---------- 基础 ----------

    def _connect(self):
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_schema(self):
        with self._connect() as con:
            con.executescript(_SCHEMA)
            for table, col, ddl in _MIGRATIONS:
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    con.execute(ddl)
            # 依赖迁移列的索引必须在其后创建（老库此时才有 scope 列）
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(active, scope)"
            )

    def get_meta(self, key: str, default=None):
        with self._connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    @staticmethod
    def _row_to_dict(r) -> dict:
        return {
            "id": r[0],
            "kind": r[1],
            "content": r[2],
            "importance": r[3],
            "source": r[4],
            "scope": r[5],
            "scope_label": SCOPE_LABELS.get(r[5], r[5]),
            "created_at": r[6],
            "last_recalled_at": r[7],
            "recall_count": r[8],
            "emb_dim": r[9],
            "emb_model": r[10],
            "has_embedding": r[9] is not None,
        }

    _COLS = "id, kind, content, importance, source, scope, created_at, last_recalled_at, recall_count, emb_dim, emb_model"

    @staticmethod
    def _scope_sql(scopes, column: str = "scope"):
        """scopes=None 不过滤；否则生成 (sql_fragment, params)。"""
        if not scopes:
            return "", []
        marks = ",".join("?" for _ in scopes)
        return f" AND {column} IN ({marks})", list(scopes)

    # ---------- chat_log（反思窗口缓冲） ----------

    def log_message(self, role: str, content: str, scope: str = "pet") -> None:
        if scope not in MEMORY_SCOPES:
            scope = "pet"
        with self._connect() as con:
            con.execute(
                "INSERT INTO chat_log(role, content, scope, created_at) VALUES (?,?,?,?)",
                (role, (content or "")[:4000], scope, now_str()),
            )

    def delete_last_user_message(self, scope: str = "pet") -> None:
        """【略过】的场景轮整对丢弃：删掉该 scope 刚落库的最后一条用户消息。"""
        with self._connect() as con:
            con.execute(
                "DELETE FROM chat_log WHERE id = "
                "(SELECT id FROM chat_log WHERE role='user' AND scope=? "
                "ORDER BY id DESC LIMIT 1)",
                (scope,),
            )

    # 反思光标按 scope 拆分：last_reflected_log_id_{scope}
    def _reflect_cursor(self, scope: str) -> int:
        """该 scope 的反思光标；无独立光标时以旧全局光标初始化（避免重反思存量）。"""
        if scope not in MEMORY_SCOPES:
            scope = "pet"
        key = f"last_reflected_log_id_{scope}"
        val = self.get_meta(key)
        if val is None:
            val = self.get_meta("last_reflected_log_id", "0") or "0"
            self.set_meta(key, val)
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    def unreflected_count(self, scope: str) -> int:
        """该 scope 光标之后的 chat_log 条数（按消息条数，不按轮）。"""
        last = self._reflect_cursor(scope)
        with self._connect() as con:
            return con.execute(
                "SELECT COUNT(*) FROM chat_log WHERE scope=? AND id > ?",
                (scope, last),
            ).fetchone()[0]

    def unreflected_window(self, scope: str, max_msgs: int):
        """返回 (rows, max_id)：该 scope 光标之后最多 max_msgs 条消息与其末行 id。

        rows 为 5 元组 (id, role, content, created_at, scope)。
        """
        last = self._reflect_cursor(scope)
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, role, content, created_at, scope FROM chat_log "
                "WHERE scope=? AND id > ? ORDER BY id LIMIT ?",
                (scope, last, max(1, int(max_msgs))),
            ).fetchall()
        return rows, (rows[-1][0] if rows else last)

    def mark_reflected(self, scope: str, max_id: int) -> None:
        if scope not in MEMORY_SCOPES:
            scope = "pet"
        self.set_meta(f"last_reflected_log_id_{scope}", str(max_id))

    def prune_chat_log(self, keep: int = 200) -> None:
        with self._connect() as con:
            con.execute(
                "DELETE FROM chat_log WHERE id NOT IN "
                "(SELECT id FROM chat_log ORDER BY id DESC LIMIT ?)",
                (keep,),
            )

    def chat_log_of_date(self, date_str: str, scope: str = "pet") -> list:
        with self._connect() as con:
            return con.execute(
                "SELECT id, role, content, created_at, scope FROM chat_log "
                "WHERE created_at LIKE ? AND scope=? ORDER BY id",
                (f"{date_str}%", scope),
            ).fetchall()

    # ---------- memories CRUD ----------

    def add_memory(
        self,
        kind: str,
        content: str,
        importance: int = 3,
        source: str = "chat",
        vec=None,
        emb_model: str | None = None,
        scope: str = "pet",
    ) -> int:
        if kind not in MEMORY_KINDS:
            kind = "fact"
        if scope not in MEMORY_SCOPES:
            scope = "pet"
        blob = dim = None
        if vec:
            blob = vec_to_blob(vec)
            dim = len(vec)
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO memories(kind, content, importance, source, scope, embedding,"
                " emb_dim, emb_model, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (kind, content[:500], min(5, max(1, int(importance))), source,
                 scope, blob, dim, emb_model, now_str()),
            )
            mid = cur.lastrowid
        if blob and self._index is not None and self._index_key == (emb_model, dim):
            self.vec_add(mid, vec)
        return mid

    def get_memories_by_ids(self, ids: list[int], scopes=None) -> dict:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        scope_sql, params = self._scope_sql(scopes)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1 AND id IN ({marks})"
                + scope_sql,
                (*ids, *params),
            ).fetchall()
        return {r[0]: self._row_to_dict(r) for r in rows}

    def find_duplicate(self, content: str):
        """规范化精确去重：命中返回既有行 dict，否则 None。"""
        n = norm_text(content)
        if not n:
            return None
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1"
            ).fetchall()
        for r in rows:
            if norm_text(r[2]) == n:
                return self._row_to_dict(r)
        return None

    def deactivate(self, mid: int) -> bool:
        with self._connect() as con:
            cur = con.execute("UPDATE memories SET active=0 WHERE id=?", (mid,))
        self.vec_remove(mid)
        return cur.rowcount > 0

    def set_importance(self, mid: int, importance: int) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "UPDATE memories SET importance=? WHERE id=? AND active=1",
                (min(5, max(1, int(importance))), mid),
            )
        return cur.rowcount > 0

    def touch_recalled(self, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        with self._connect() as con:
            con.execute(
                f"UPDATE memories SET last_recalled_at=?, recall_count=recall_count+1 "
                f"WHERE id IN ({marks})",
                (now_str(), *ids),
            )

    def profile_memories(self, limit: int = 5, scopes=None) -> list[dict]:
        scope_sql, params = self._scope_sql(scopes)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1 AND kind='profile'"
                + scope_sql
                + " ORDER BY importance DESC, id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def recent_active(self, limit: int = 10, exclude_kinds=("profile",), scopes=None) -> list[dict]:
        marks = ",".join("?" for _ in exclude_kinds)
        scope_sql, params = self._scope_sql(scopes)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1 "
                f"AND kind NOT IN ({marks})"
                + scope_sql
                + " ORDER BY importance DESC, id DESC LIMIT ?",
                (*exclude_kinds, *params, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_memories(self, q: str = "", offset: int = 0, limit: int = 20, scope: str = ""):
        """返回 (items, total)；q 为内容子串，scope 为空串表示全部范围。"""
        like = f"%{q}%"
        scope_sql = ""
        params: list = [like]
        if scope in MEMORY_SCOPES:
            scope_sql = " AND scope=?"
            params.append(scope)
        with self._connect() as con:
            total = con.execute(
                "SELECT COUNT(*) FROM memories WHERE active=1 AND content LIKE ?" + scope_sql,
                params,
            ).fetchone()[0]
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1 AND content LIKE ?"
                + scope_sql
                + " ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows], total

    def stats(self, emb_model: str | None = None) -> dict:
        with self._connect() as con:
            total = con.execute(
                "SELECT COUNT(*) FROM memories WHERE active=1"
            ).fetchone()[0]
            by_kind = dict(
                con.execute(
                    "SELECT kind, COUNT(*) FROM memories WHERE active=1 GROUP BY kind"
                ).fetchall()
            )
            by_scope = dict(
                con.execute(
                    "SELECT scope, COUNT(*) FROM memories WHERE active=1 GROUP BY scope"
                ).fetchall()
            )
            with_emb = con.execute(
                "SELECT COUNT(*) FROM memories WHERE active=1 AND embedding IS NOT NULL"
            ).fetchone()[0]
            stale = 0
            if emb_model:
                stale = con.execute(
                    "SELECT COUNT(*) FROM memories WHERE active=1 AND embedding IS NOT NULL "
                    "AND (emb_model IS NULL OR emb_model != ?)",
                    (emb_model,),
                ).fetchone()[0]
            log_rows = con.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0]
        return {
            "total_active": total,
            "by_kind": by_kind,
            "by_scope": by_scope,
            "with_embedding": with_emb,
            "missing_embedding": total - with_emb,
            "stale_embedding": stale,
            "unreflected": {s: self.unreflected_count(s) for s in MEMORY_SCOPES},
            "chat_log_rows": log_rows,
            "last_reflect_at": self.get_meta("last_reflect_at"),
            "last_diary_date": self.get_meta("last_diary_date"),
        }

    def memories_missing_embedding(self, limit: int = 200) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, content FROM memories WHERE active=1 AND embedding IS NULL "
                "ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]

    def all_active_contents(self, limit: int = 5000) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, content FROM memories WHERE active=1 ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]

    def set_embedding(self, mid: int, vec, emb_model: str) -> None:
        blob = vec_to_blob(vec)
        with self._connect() as con:
            con.execute(
                "UPDATE memories SET embedding=?, emb_dim=?, emb_model=? WHERE id=?",
                (blob, len(vec), emb_model, mid),
            )
        if self._index is not None and self._index_key == (emb_model, len(vec)):
            self.vec_remove(mid)
            self.vec_add(mid, vec)

    def apply_decay(self, now=None) -> tuple[int, int]:
        """每日维护：重要度即耐久度——超过档位天数未召回降 1 级，1 级超期软删。

        freshness 时钟 = COALESCE(last_recalled_at, created_at)；importance 5 永久免疫。
        档位见 DECAY_TIER_DAYS（diary 固定 3 分 = 60 天档）。返回 (decayed, archived)。
        """
        now = now or datetime.now()
        decay_ids: list[int] = []
        archive_ids: list[int] = []
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, importance, created_at, last_recalled_at FROM memories "
                "WHERE active=1 AND importance<5"
            ).fetchall()
            for mid, imp, created, recalled in rows:
                dt = parse_dt(recalled) or parse_dt(created)
                if dt is None:
                    continue
                days = (now - dt).total_seconds() / 86400.0
                tier = DECAY_TIER_DAYS.get(int(imp))
                if tier is None or days < tier:
                    continue
                if int(imp) <= 1:
                    archive_ids.append(mid)
                else:
                    decay_ids.append(mid)
            for mid in decay_ids:
                con.execute(
                    "UPDATE memories SET importance=importance-1 WHERE id=? AND active=1",
                    (mid,),
                )
            for mid in archive_ids:
                con.execute("UPDATE memories SET active=0 WHERE id=?", (mid,))
        for mid in archive_ids:
            self.vec_remove(mid)  # 索引只含 active 行，软删同步移除
        return len(decay_ids), len(archive_ids)

    # ---------- 记忆图谱（控制页可视化） ----------

    def graph_data(self, emb_model: str | None = None, limit: int = 200,
                   neighbors: int = 3, min_cos: float = 0.5) -> dict:
        """返回 {"nodes", "edges", "vector", "reason"?}；向量不可用时仅节点。

        节点取 active 且已向量的记忆（emb_model 给定时只取该模型），按重要度排序。
        边为 faiss 近邻（排除自身与 cos<min_cos，按 (a<b) 去重，w 保留 3 位）。
        """
        sql = (
            "SELECT id, kind, scope, importance, content, created_at, embedding, "
            "emb_dim, emb_model FROM memories "
            "WHERE active=1 AND embedding IS NOT NULL"
        )
        params: list = []
        if emb_model:
            sql += " AND emb_model=?"
            params.append(emb_model)
        sql += " ORDER BY importance DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()

        def _node(r) -> dict:
            return {
                "id": r[0],
                "kind": r[1],
                "scope": r[2],
                "importance": r[3],
                "content": str(r[4] or "")[:40],
                "created_at": r[5],
            }

        # 边只在同一嵌入空间内有意义：emb_model 未给定时取行数最多的模型
        if not emb_model and rows:
            counts: dict = {}
            for r in rows:
                counts[r[8]] = counts.get(r[8], 0) + 1
            emb_model = max(counts, key=counts.get)
        cands = [r for r in rows if not emb_model or r[8] == emb_model]
        if len(cands) < 3:
            return {
                "nodes": [],
                "edges": [],
                "vector": False,
                "reason": "可入图的向量记忆不足 3 条",
            }
        nodes = [_node(r) for r in cands]
        dim = cands[0][7] or 0
        if not self._load_faiss() or not self.ensure_index(emb_model, dim):
            return {
                "nodes": nodes,
                "edges": [],
                "vector": False,
                "reason": "向量索引不可用，仅显示节点",
            }
        edges: dict[tuple[int, int], float] = {}
        k = max(1, int(neighbors)) + 1
        for r in cands:
            try:
                hits = self.vec_search(blob_to_vec(r[6]), k)
            except Exception:
                continue
            for mid, cos in hits:
                if mid == r[0] or cos < min_cos:
                    continue
                a, b = (r[0], mid) if r[0] < mid else (mid, r[0])
                if (a, b) not in edges or cos > edges[(a, b)]:
                    edges[(a, b)] = cos
        return {
            "nodes": nodes,
            "edges": [
                {"a": a, "b": b, "w": round(w, 3)}
                for (a, b), w in sorted(edges.items(), key=lambda kv: -kv[1])
            ],
            "vector": True,
        }

    # ---------- LivingMemory 导入 ----------

    def fetch_livingmemory_candidates(
        self, lm_db_path, pet_sid: str, master_qq: str, master_name: str
    ) -> dict:
        """只读全量扫描 LivingMemory documents 表，按会话映射 scope 后返回候选条目。

        映射规则见 scope_of_lm_session；无法判定 scope 的会话（如陌生人私聊）跳过。
        返回 {"found", "items"(带 scope), "skipped_sessions", "error"}。
        """
        path = Path(lm_db_path)
        if not path.exists():
            return {"found": 0, "items": [], "error": f"未找到 {path}"}
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        except Exception as e:
            return {"found": 0, "items": [], "error": str(e)}
        try:
            tables = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "documents" not in tables:
                return {"found": 0, "items": [], "error": "documents 表不存在"}
            rows = con.execute(
                "SELECT text, metadata FROM documents WHERE json_valid(metadata)"
            ).fetchall()
        except Exception as e:
            con.close()
            return {"found": 0, "items": [], "error": str(e)}
        con.close()
        name = master_name or "主人"
        items = []
        seen = set()
        skipped_sessions: dict[str, int] = {}
        for text, metadata in rows:
            try:
                meta = json.loads(metadata or "{}")
            except (ValueError, TypeError):
                meta = {}
            lm_sid = str(meta.get("session_id") or "")
            scope = scope_of_lm_session(lm_sid, pet_sid, master_qq)
            if scope is None:
                skipped_sessions[lm_sid or "?"] = skipped_sessions.get(lm_sid or "?", 0) + 1
                continue
            content = str(text or "").strip()
            if not content:
                continue
            if pet_sid and pet_sid in content:
                content = content.replace(pet_sid, name)
            if master_qq and str(master_qq) in content:
                content = content.replace(str(master_qq), name)
            n = norm_text(content)
            if not n or n in seen:
                continue
            seen.add(n)
            imp = 3
            raw_imp = meta.get("importance")
            if raw_imp is not None:
                try:
                    v = float(raw_imp)
                    imp = min(5, max(1, round(1 + 4 * v))) if v <= 1.0 else min(5, max(1, round(v)))
                except (ValueError, TypeError):
                    pass
            items.append(
                {"kind": "fact", "content": content[:500], "importance": imp, "scope": scope}
            )
        return {
            "found": len(rows),
            "items": items,
            "skipped_sessions": skipped_sessions,
            "error": None,
        }

    # ---------- faiss 内存索引（惰性加载，永不落盘） ----------

    def _load_faiss(self) -> bool:
        if self._faiss_ok is not None:
            return self._faiss_ok
        try:
            import faiss  # type: ignore
            import numpy  # type: ignore

            self._faiss = faiss
            self._np = numpy
            self._faiss_ok = True
        except Exception:
            self._faiss_ok = False
        return self._faiss_ok

    @property
    def vector_available(self) -> bool:
        return self._load_faiss()

    def invalidate_index(self) -> None:
        self._index = None
        self._index_key = None

    def ensure_index(self, emb_model: str, dim: int) -> bool:
        """索引与 (emb_model, dim) 不符时从库全量重建。返回向量检索是否可用。"""
        if not self._load_faiss() or not dim:
            return False
        key = (emb_model, int(dim))
        if self._index is not None and self._index_key == key:
            return True
        faiss, np = self._faiss, self._np
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(key[1]))
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, embedding, emb_dim FROM memories "
                "WHERE active=1 AND embedding IS NOT NULL AND emb_model=?",
                (emb_model,),
            ).fetchall()
        vecs, ids = [], []
        for rid, blob, d in rows:
            if d != key[1] or not blob:
                continue
            vecs.append(blob_to_vec(blob))
            ids.append(rid)
        if vecs:
            index.add_with_ids(
                np.array(vecs, dtype="float32"), np.array(ids, dtype="int64")
            )
        self._index = index
        self._index_key = key
        return True

    def vec_search(self, vec, k: int) -> list[tuple[int, float]]:
        """余弦检索，返回 [(memory_id, cos)]，按相似度降序。"""
        if self._index is None or self._index.ntotal == 0:
            return []
        np = self._np
        q = np.array([normalize_vec(vec)], dtype="float32")
        scores, ids = self._index.search(q, min(k, self._index.ntotal))
        out = []
        for i, s in zip(ids[0], scores[0]):
            if int(i) >= 0:
                out.append((int(i), float(s)))
        return out

    def vec_add(self, mid: int, vec) -> None:
        if self._index is None or len(vec) != self._index.d:
            return
        try:
            self._index.add_with_ids(
                self._np.array([normalize_vec(vec)], dtype="float32"),
                self._np.array([mid], dtype="int64"),
            )
        except Exception:
            pass

    def vec_remove(self, mid: int) -> None:
        if self._index is None:
            return
        try:
            self._index.remove_ids(self._np.array([mid], dtype="int64"))
        except Exception:
            pass
