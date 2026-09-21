"""pet_memory —— 桌宠插件内置长期记忆（向量召回版）。

与 AstrBot 解耦的纯存储/检索引擎：不 import 任何 astrbot 模块，
embedding / LLM provider 全部由调用方（main.py）注入，便于单元测试。

- 存储：stdlib sqlite3（memory.db），向量以 float32 小端 BLOB 落库（真值来源）。
- 索引：faiss IndexIDMap2(IndexFlatIP)，惰性 import，启动/换模型时从库重建，
  纯内存不落盘——索引是衍生物，损坏即重建，不存在"索引与库不同步"。
- 降级：无 faiss / 无 embedding provider / 向量缺失时自动退化为
  「重要度 + 时效」召回，任何情况下记忆功能都可用。
- 时间戳一律 ISO 文本（%Y-%m-%d %H:%M:%S），绝不写 float（避免解析灾难）。
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 3,
    source TEXT NOT NULL DEFAULT 'chat',
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
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

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


def build_injection(memories: list[dict], max_chars: int) -> str:
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
    return (
        "【桌宠记忆】以下是你与主人之间过去的记忆，仅供你自然地参考与呼应；"
        "不要逐字复述，也不要向主人提及「记忆」机制本身：\n" + "\n".join(lines)
    )


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
    "- importance：1=琐事 … 5=极其重要（生日、重大事件、明确承诺）\n"
    "- 最多 8 条，宁缺毋滥\n"
    "- 只输出 JSON 数组，不要输出任何其他文字"
)


def build_reflect_prompt(rows: list, master_name: str) -> str:
    """rows: [(id, role, content, created_at)]；返回反思用户 prompt。"""
    name = master_name or "主人"
    lines = []
    for _id, role, content, created_at in rows[:40]:
        who = name if role == "user" else "桌宠"
        lines.append(f"[{str(created_at)[:16]}] {who}：{str(content)[:300]}")
    return (
        f"主人的称呼是「{name}」。以下是桌宠与主人最近的对话片段，"
        "请按系统要求抽取长期记忆：\n\n" + "\n".join(lines)
    )


def build_diary_prompt(rows: list, master_name: str, date_str: str) -> str:
    """rows: [(id, role, content, created_at)]；返回日记用户 prompt。"""
    name = master_name or "主人"
    lines = []
    for _id, role, content, created_at in rows[:60]:
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
            "created_at": r[5],
            "last_recalled_at": r[6],
            "recall_count": r[7],
            "emb_dim": r[8],
            "emb_model": r[9],
            "has_embedding": r[8] is not None,
        }

    _COLS = "id, kind, content, importance, source, created_at, last_recalled_at, recall_count, emb_dim, emb_model"

    # ---------- chat_log（反思窗口缓冲） ----------

    def log_message(self, role: str, content: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO chat_log(role, content, created_at) VALUES (?,?,?)",
                (role, (content or "")[:4000], now_str()),
            )

    def delete_last_user_message(self) -> None:
        """【略过】的场景轮整对丢弃：删掉刚落库的最后一条用户消息。"""
        with self._connect() as con:
            con.execute(
                "DELETE FROM chat_log WHERE id = "
                "(SELECT id FROM chat_log WHERE role='user' ORDER BY id DESC LIMIT 1)"
            )

    def unreflected_window(self, max_pairs: int):
        """返回 (rows, max_id)：光标之后的最多 max_pairs*2 条消息与其末行 id。"""
        last = int(self.get_meta("last_reflected_log_id", "0") or 0)
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, role, content, created_at FROM chat_log "
                "WHERE id > ? ORDER BY id LIMIT ?",
                (last, max(2, max_pairs * 2)),
            ).fetchall()
        return rows, (rows[-1][0] if rows else last)

    def unreflected_pairs_count(self) -> int:
        last = int(self.get_meta("last_reflected_log_id", "0") or 0)
        with self._connect() as con:
            n = con.execute(
                "SELECT COUNT(*) FROM chat_log WHERE id > ?", (last,)
            ).fetchone()[0]
        return n // 2

    def mark_reflected(self, max_id: int) -> None:
        self.set_meta("last_reflected_log_id", str(max_id))

    def prune_chat_log(self, keep: int = 200) -> None:
        with self._connect() as con:
            con.execute(
                "DELETE FROM chat_log WHERE id NOT IN "
                "(SELECT id FROM chat_log ORDER BY id DESC LIMIT ?)",
                (keep,),
            )

    def chat_log_of_date(self, date_str: str) -> list:
        with self._connect() as con:
            return con.execute(
                "SELECT id, role, content, created_at FROM chat_log "
                "WHERE created_at LIKE ? ORDER BY id",
                (f"{date_str}%",),
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
    ) -> int:
        if kind not in MEMORY_KINDS:
            kind = "fact"
        blob = dim = None
        if vec:
            blob = vec_to_blob(vec)
            dim = len(vec)
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO memories(kind, content, importance, source, embedding,"
                " emb_dim, emb_model, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (kind, content[:500], min(5, max(1, int(importance))), source,
                 blob, dim, emb_model, now_str()),
            )
            mid = cur.lastrowid
        if blob and self._index is not None and self._index_key == (emb_model, dim):
            self.vec_add(mid, vec)
        return mid

    def get_memories_by_ids(self, ids: list[int]) -> dict:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1 AND id IN ({marks})",
                tuple(ids),
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

    def profile_memories(self, limit: int = 5) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1 AND kind='profile' "
                "ORDER BY importance DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def recent_active(self, limit: int = 10, exclude_kinds=("profile",)) -> list[dict]:
        marks = ",".join("?" for _ in exclude_kinds)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1 "
                f"AND kind NOT IN ({marks}) ORDER BY importance DESC, id DESC LIMIT ?",
                (*exclude_kinds, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_memories(self, q: str = "", offset: int = 0, limit: int = 20):
        """返回 (items, total)；q 为内容子串。"""
        like = f"%{q}%"
        with self._connect() as con:
            total = con.execute(
                "SELECT COUNT(*) FROM memories WHERE active=1 AND content LIKE ?",
                (like,),
            ).fetchone()[0]
            rows = con.execute(
                f"SELECT {self._COLS} FROM memories WHERE active=1 AND content LIKE ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (like, limit, offset),
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
            "with_embedding": with_emb,
            "missing_embedding": total - with_emb,
            "stale_embedding": stale,
            "unreflected_pairs": self.unreflected_pairs_count(),
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

    def apply_decay(self):
        """每日维护：久未召回的非档案类记忆重要度 -1；陈旧的 1 分记忆软删。返回 (decayed, archived)。"""
        from datetime import timedelta

        now = datetime.now()
        before30 = (now - timedelta(days=30)).strftime(_TIME_FMT)
        before180 = (now - timedelta(days=180)).strftime(_TIME_FMT)
        with self._connect() as con:
            cur1 = con.execute(
                "UPDATE memories SET importance=importance-1 WHERE active=1 "
                "AND kind NOT IN ('profile','promise','diary') AND importance > 1 "
                "AND created_at < ? AND (last_recalled_at IS NULL OR last_recalled_at < ?)",
                (before30, before30),
            )
            cur2 = con.execute(
                "UPDATE memories SET active=0 WHERE active=1 AND importance <= 1 "
                "AND created_at < ? AND recall_count = 0",
                (before180,),
            )
        return cur1.rowcount, cur2.rowcount

    # ---------- LivingMemory 导入 ----------

    def fetch_livingmemory_candidates(self, lm_db_path, sid: str, master_name: str) -> dict:
        """只读扫描 LivingMemory documents 表中桌宠会话的记忆，返回去重后的候选条目。"""
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
                "SELECT text, metadata FROM documents "
                "WHERE json_valid(metadata) AND json_extract(metadata, '$.session_id') LIKE ?",
                (f"%{sid}%",),
            ).fetchall()
        except Exception as e:
            con.close()
            return {"found": 0, "items": [], "error": str(e)}
        con.close()
        name = master_name or "主人"
        items = []
        seen = set()
        for text, metadata in rows:
            content = str(text or "").strip()
            if not content:
                continue
            if sid and sid in content:
                content = content.replace(sid, name)
            n = norm_text(content)
            if not n or n in seen:
                continue
            seen.add(n)
            imp = 3
            try:
                meta = json.loads(metadata or "{}")
                raw_imp = meta.get("importance")
                if raw_imp is not None:
                    v = float(raw_imp)
                    imp = min(5, max(1, round(1 + 4 * v))) if v <= 1.0 else min(5, max(1, round(v)))
            except (ValueError, TypeError):
                pass
            items.append({"kind": "fact", "content": content[:500], "importance": imp})
        return {"found": len(rows), "items": items, "error": None}

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
