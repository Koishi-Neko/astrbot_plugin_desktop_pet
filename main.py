"""astrbot_plugin_desktop_pet

为自研桌面桌宠壳（pet_shell/）提供对话接口的 AstrBot 插件。

桌宠对话走 AstrBot open API（webchat 管道）：壳端调 /api/v1/chat，
本插件经 on_llm_request 钩子识别桌宠会话并注入格式要求（情绪/日语配音/主人身份）。

自有路由（挂在 dashboard 插件扩展路径下，需带 plugin scope 的 API Key 鉴权）：
- GET  /api/v1/plugins/extensions/desktop_pet/pet/health        探活，返回 JSON
- POST /api/v1/plugins/extensions/desktop_pet/pet/tts           日语 TTS 合成（壳端按句调用）
- GET  /api/v1/plugins/extensions/desktop_pet/pet/personas      列出 AstrBot 人格
- GET  /api/v1/plugins/extensions/desktop_pet/pet/scene_config  桌面感知配置（壳端远程拉取）
- POST /api/v1/plugins/extensions/desktop_pet/pet/status_report 壳端状态上报（监控用）
- astrbot_plugin_desktop_pet/page/*                             WebUI 控制页后端

内置长期记忆（pet_memory.py，独立于 LivingMemory）：桌宠/私聊/群聊三范围消息落
chat_log（群聊经被动监听滚动缓冲拼接触发前文），每个范围各自攒满
memory_reflect_batch_messages 条消息后由 LLM 反思抽取记忆条目（档案/事件/心情/约定/观察），
嵌入模型向量入库；对话时按余弦相似度 + 重要度/时效混合重排召回（默认 3 条，
独立开关可隔离范围召回池），经 extra_user_content_parts 瞬时注入（不落会话历史）。
主人发「记住 xxx」/「永久记住 xxx」可直接下令写入（普通=重要档/永久=永不遗忘），
固定回复并终止后续管线。重要度即耐久度：1/2/3/4 档约 15/30/60/120 天未召回即降级，
1 档超期软删，5 档永久免疫。无 embedding provider 时自动降级为「重要度+时效」召回，
功能不拒用。每日 04:40 维护（衰减/软删/补嵌/桌宠日记）。

TTS：配置 tts_enabled=true 后，要求模型输出「【情绪】中文正文【JP】日语配音稿」，
壳端解析出日语句后逐句调 pet/tts，插件转发 Style-Bert-VITS2（server_fastapi）合成返回 base64 wav。
QQ 日语配音（qq_jp_dub_enabled）：on_decorating_result 把回复拆成 Plain(中文)+Record(日语 wav)。
"""

import asyncio
import base64
import json
import os
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import CustomFilter
from astrbot.api.message_components import Plain, Record
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, request
from starlette.responses import JSONResponse

try:  # AstrBot 以包方式加载插件；pytest 平面布局回退为同级导入
    from .pet_memory import (
        MEMORY_SCOPES,
        REFLECT_SYSTEM,
        REFLECT_SYSTEM_GROUP_ADDENDUM,
        GroupContextBuffer,
        PetMemoryStore,
        blend_score,
        build_diary_prompt,
        build_injection,
        build_reflect_prompt,
        extract_terms,
        norm_text,
        now_str,
        parse_dt,
        parse_memories_json,
        parse_remember_command,
        pool_scopes,
        strip_leading_tags,
    )
except ImportError:
    from pet_memory import (
        MEMORY_SCOPES,
        REFLECT_SYSTEM,
        REFLECT_SYSTEM_GROUP_ADDENDUM,
        GroupContextBuffer,
        PetMemoryStore,
        blend_score,
        build_diary_prompt,
        build_injection,
        build_reflect_prompt,
        extract_terms,
        norm_text,
        now_str,
        parse_dt,
        parse_memories_json,
        parse_remember_command,
        pool_scopes,
        strip_leading_tags,
    )

# 情绪集合需与 pet_shell/assets/ 下的立绘文件名一一对应
EMOTIONS = ["平静", "高兴", "生气", "害羞", "惊讶", "难过", "疑惑", "调皮"]

EMOTION_INSTRUCTION = (
    "\n\n【输出格式要求】每次回复必须以情绪标签开头，格式为「【情绪】正文」，"
    "情绪只能从以下列表中选择一个：{emotions}。"
    "标签之后紧接回复正文。正文要口语化、简短（1~3 句），"
    "就像桌宠气泡里说的话。不要使用 markdown、列表或代码块，"
    "除开头的情绪标签外不要输出任何其他方括号标记。"
)

EMOTION_INSTRUCTION_TTS = (
    "\n\n【输出格式要求·必须严格遵守】每次回复必须同时包含以下三部分，缺一不可："
    "①情绪标签：回复以「【情绪】」开头，情绪只能从以下列表中选择一个：{emotions}；"
    "②中文正文：口语化、简短（1~3 句），就像桌宠气泡里说的话；"
    "③日语配音稿：以「【JP】」开头，紧接与中文正文意思对应的日语，必须是纯日语口语短句，"
    "用于语音合成朗读，不含中文、不含任何方括号标记。"
    "完整格式示例：「【高兴】今天也好想你呀，主人！【JP】今日も会いたかったよ、ご主人様！」"
    "禁止省略【JP】部分。不要使用 markdown、列表或代码块，"
    "除开头的情绪标签和【JP】外不要输出任何其他方括号标记。"
)

JP_DUB_INSTRUCTION = (
    "\n\n【输出格式要求·必须严格遵守】每次回复必须同时包含两部分，缺一不可："
    "①中文正文：你的回复内容；"
    "②日语配音稿：以「【JP】」开头，紧接与中文正文意思对应的日语，必须是纯日语口语短句，"
    "用于语音合成朗读，不含中文、不含任何方括号标记。"
    "完整格式示例：「早上好呀！【JP】おはよう！」"
    "禁止省略【JP】部分。除【JP】外不要输出任何其他方括号标记。"
)

_JP_TAG = re.compile(r"\s*【\s*JP\s*】\s*", re.IGNORECASE)
_IDENT_REMINDER = re.compile(r"User ID: [^\n,]*, Nickname: [^\n]*")

TTS_CONFIG_KEYS = (
    "tts_enabled",
    "tts_base_url",
    "tts_model_id",
    "tts_speaker_id",
    "tts_style",
    "tts_length",
)


def _strip_image_parts(history) -> int:
    removed = 0
    for msg in history:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            kept = [
                p
                for p in content
                if not (isinstance(p, dict) and p.get("type") in ("image_url", "image"))
            ]
            if len(kept) != len(content):
                removed += len(content) - len(kept)
                msg["content"] = kept if kept else "[图片]"
    return removed


def _strip_think_parts(history) -> int:
    removed = 0
    for msg in history:
        if not isinstance(msg, dict):
            continue
        if "reasoning_content" in msg:
            del msg["reasoning_content"]
            removed += 1
    return removed

PAGE_CONFIG_KEYS = (
    "master_name",
    "master_qq",
    "qq_jp_dub_enabled",
)

# 桌面感知/主动对话（壳端远程拉取，控制页编辑；服务侧配置统一收口此处下发）
SCENE_CONFIG_KEYS = (
    "scene_provider",
    "scene_blocklist",
    "proactive_enabled",
    "scene_enabled",
    "scene_interval_min",
    "intent_perceive_enabled",
    "intent_perceive_keywords",
)

# 语音输入（壳端远程拉取，控制页编辑；独立模式无插件时壳端设置面板兜底）
ASR_CONFIG_KEYS = (
    "voice_input_enabled",
    "asr_url",
)

# 内置长期记忆（控制页编辑；向量召回 + LLM 反思，独立于 LivingMemory）
MEMORY_CONFIG_KEYS = (
    "memory_enabled",
    "memory_embedding_provider_id",
    "memory_provider_id",
    "memory_reflect_batch_messages",
    "memory_recall_top_k",
    "memory_recall_min_score",
    "memory_recall_max_chars",
    "memory_diary_enabled",
    "memory_scope_private_enabled",
    "memory_scope_group_enabled",
    "memory_scope_pet_independent",
    "memory_scope_private_independent",
    "memory_scope_group_independent",
    "memory_group_context_count",
)

# 记忆配置里的布尔键（page_memory_config 类型转换用）
MEMORY_CONFIG_BOOL_KEYS = (
    "memory_enabled",
    "memory_diary_enabled",
    "memory_scope_private_enabled",
    "memory_scope_group_enabled",
    "memory_scope_pet_independent",
    "memory_scope_private_independent",
    "memory_scope_group_independent",
)

DEFAULT_PROACTIVE_ENABLED = True
DEFAULT_SCENE_ENABLED = False
DEFAULT_SCENE_INTERVAL_MIN = 30
DEFAULT_ASR_URL = "http://127.0.0.1:15055"
DEFAULT_SCENE_BLOCKLIST = (
    "weixin.exe, wechat.exe, wechatappex.exe, wechatplayer.exe, "
    "qq.exe, tim.exe, wxwork.exe, dingtalk.exe, wemeetapp.exe, "
    "winword.exe, excel.exe, powerpnt.exe"
)
DEFAULT_INTENT_PERCEIVE_ENABLED = True
# 指令感知关键词（一行一个，子串匹配，大小写/空白不敏感；壳端另有否定护栏）
DEFAULT_INTENT_PERCEIVE_KEYWORDS = (
    "看看屏幕\n看我的屏幕\n看看我在\n我在干嘛\n我在做什么\n我在干什么\n"
    "看看桌面\n看看窗口\n当前窗口\n屏幕上\n看看这个\n"
    "look at my screen\nwhat's on my screen\nwhat am i doing"
)
DEFAULT_MEMORY_REFLECT_BATCH_MESSAGES = 8
DEFAULT_MEMORY_RECALL_TOP_K = 3
DEFAULT_MEMORY_RECALL_MIN_SCORE = 0.35
DEFAULT_MEMORY_RECALL_MAX_CHARS = 800
DEFAULT_MEMORY_GROUP_CONTEXT_COUNT = 10

# 当前活动插件实例（供被动监听过滤器取缓冲/配置；initialize 置位，terminate 清空）
_ACTIVE_PLUGIN = None


class _GroupContextCaptureFilter(CustomFilter):
    """被动群消息监听：把群消息推进前文缓冲，永远返回 False 不唤醒消息管线。

    与 LivingMemory 的 PassiveGroupCaptureFilter 同机制：custom_filter 对每个
    事件求值（无需唤醒），副作用在 filter() 里完成，handler 本体不会执行。
    """

    def __init__(self, raise_error: bool = True, **kwargs):
        if not isinstance(raise_error, bool):
            raise_error = True
        super().__init__(raise_error=raise_error, **kwargs)

    def filter(self, event: AstrMessageEvent, cfg) -> bool:
        plugin = _ACTIVE_PLUGIN
        if plugin is not None:
            try:
                plugin._group_context_push(event)
            except Exception:
                pass
        return False


class DesktopPetBridge(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self._group_ctx = GroupContextBuffer(DEFAULT_MEMORY_GROUP_CONTEXT_COUNT)

    async def initialize(self):
        global _ACTIVE_PLUGIN
        _ACTIVE_PLUGIN = self
        self._shell_report = None  # 壳端最近一次状态上报 {"at": epoch, ...}
        self._mem_store = None  # 内置记忆存储（PetMemoryStore，_init_memory 填充）
        self._mem_reflect_task = None
        self._mem_daily_task = None
        self._mem_reembed_task = None
        self.context.register_web_api(
            "desktop_pet/pet/health",
            self.health,
            ["GET"],
            "桌宠接口探活",
        )
        self.context.register_web_api(
            "desktop_pet/pet/tts",
            self.tts,
            ["POST"],
            "桌宠 TTS 合成接口（管道模式下由壳端按句调用）",
        )
        self.context.register_web_api(
            "desktop_pet/pet/personas",
            self.personas,
            ["GET"],
            "列出 AstrBot 人格",
        )
        self.context.register_web_api(
            "desktop_pet/pet/scene_config",
            self.pet_scene_config,
            ["GET"],
            "桌宠壳远程拉取桌面感知配置（视觉模型/禁止抓取名单）",
        )
        self.context.register_web_api(
            "desktop_pet/pet/asr_config",
            self.pet_asr_config,
            ["GET"],
            "桌宠壳远程拉取语音输入配置（开关/ASR 地址）",
        )
        self.context.register_web_api(
            "desktop_pet/pet/status_report",
            self.pet_status_report,
            ["POST"],
            "桌宠壳状态上报（主动对话/桌面感知监控用）",
        )
        # 控制页 API 前缀必须是插件全名（bridge 按插件名转发）
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/status",
            self.page_status,
            ["GET"],
            "桌宠控制页：状态总览",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/sbv2_models",
            self.page_sbv2_models,
            ["GET"],
            "桌宠控制页：代理 SBV2 模型列表",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/tts_config",
            self.page_tts_config,
            ["GET", "POST"],
            "桌宠控制页：读写 TTS 配置",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/master_config",
            self.page_master_config,
            ["GET", "POST"],
            "桌宠控制页：读写主人身份配置",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/persona_config",
            self.page_persona_config,
            ["GET", "POST"],
            "桌宠控制页：读写桌宠会话人格",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/tts_test",
            self.page_tts_test,
            ["POST"],
            "桌宠控制页：TTS 试听",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/scene_config",
            self.page_scene_config,
            ["GET", "POST"],
            "桌宠控制页：读写桌面感知配置",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/asr_config",
            self.page_asr_config,
            ["GET", "POST"],
            "桌宠控制页：读写语音输入配置（开关/ASR 地址）",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/token_stats",
            self.page_token_stats,
            ["GET"],
            "桌宠控制页：获取 Token 统计",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/memory_config",
            self.page_memory_config,
            ["GET", "POST"],
            "桌宠控制页：读写内置记忆配置",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/memory_query",
            self.page_memory_query,
            ["GET"],
            "桌宠控制页：记忆统计与浏览",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/memory_op",
            self.page_memory_op,
            ["POST"],
            "桌宠控制页：记忆操作（添加/删除/导入/立即反思/重建向量）",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/memory_recall_test",
            self.page_memory_recall_test,
            ["POST"],
            "桌宠控制页：记忆召回测试",
        )
        self.context.register_web_api(
            "astrbot_plugin_desktop_pet/page/memory_graph",
            self.page_memory_graph,
            ["GET"],
            "桌宠控制页：记忆图谱（向量近邻边）",
        )
        logger.info(
            "[desktop_pet] web api registered: desktop_pet/pet/*, desktop_pet/page/*"
        )
        self._gc_task = asyncio.create_task(self._history_gc_loop())
        self._init_memory()
        if self._mem_store is not None:
            self._mem_daily_task = asyncio.create_task(self._memory_daily_loop())

    async def terminate(self):
        global _ACTIVE_PLUGIN
        if _ACTIVE_PLUGIN is self:
            _ACTIVE_PLUGIN = None
        for attr in ("_gc_task", "_mem_daily_task", "_mem_reflect_task", "_mem_reembed_task"):
            task = getattr(self, attr, None)
            if task:
                task.cancel()
        logger.info("[desktop_pet] plugin terminated")

    # ---------- 路由处理 ----------

    async def health(self):
        prov = self.context.get_using_provider()
        return {
            "status": "ok",
            "plugin": "astrbot_plugin_desktop_pet",
            "default_provider_available": prov is not None,
            "emotions": EMOTIONS,
            "tts_enabled": self._tts_enabled(),
            "qq_jp_dub_enabled": self._qq_jp_dub_enabled(),
            "pet_session_id": self._pet_session_id(),
        }

    async def tts(self):
        """TTS 合成：POST {"text": "日语文本"} -> {"audio": "<base64 wav>"}"""
        raw = await request.body()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        text = str(body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)
        if not self._tts_enabled():
            return JSONResponse({"error": "tts is disabled"}, status_code=400)
        audio = await self._synthesize(text)
        if audio is None:
            return JSONResponse({"error": "synthesize failed"}, status_code=502)
        return {"audio": audio, "format": "wav"}

    async def personas(self):
        """列出 AstrBot 人格（供桌宠选用参考）。"""
        mgr = self.context.persona_manager
        out = []
        try:
            for p in mgr.personas_v3 or []:
                name = p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
                prompt = p.get("prompt") if isinstance(p, dict) else getattr(p, "prompt", "")
                if name:
                    out.append({"name": name, "prompt_preview": (prompt or "")[:80]})
        except Exception as e:
            logger.warning(f"[desktop_pet] list personas failed: {e}")
        return {"default": mgr.default_persona, "personas": out}

    # ---------- 桌面感知配置（壳端远程拉取） ----------

    def _scene_provider(self) -> str:
        # 空串 = 跟随会话默认模型（壳端不传 selected_provider）
        return str(self.config.get("scene_provider") or "").strip()

    def _scene_blocklist_str(self) -> str:
        return str(self.config.get("scene_blocklist") or DEFAULT_SCENE_BLOCKLIST).strip()

    def _proactive_enabled(self) -> bool:
        return bool(self.config.get("proactive_enabled", DEFAULT_PROACTIVE_ENABLED))

    def _scene_enabled(self) -> bool:
        return bool(self.config.get("scene_enabled", DEFAULT_SCENE_ENABLED))

    def _scene_interval_min(self) -> int:
        try:
            v = int(self.config.get("scene_interval_min") or DEFAULT_SCENE_INTERVAL_MIN)
            return max(1, v)
        except (TypeError, ValueError):
            return DEFAULT_SCENE_INTERVAL_MIN

    @staticmethod
    def _parse_blocklist(raw: str) -> list[str]:
        return [s.strip().lower() for s in re.split(r"[,，\s]+", raw or "") if s.strip()]

    def _intent_perceive_enabled(self) -> bool:
        return bool(self.config.get("intent_perceive_enabled", DEFAULT_INTENT_PERCEIVE_ENABLED))

    def _intent_perceive_keywords_str(self) -> str:
        return str(
            self.config.get("intent_perceive_keywords") or DEFAULT_INTENT_PERCEIVE_KEYWORDS
        ).strip()

    @staticmethod
    def _parse_keywords(raw: str) -> list[str]:
        # 一行一个子串（保留行内空格，如 "look at my screen"）；壳端匹配时再归一化
        return [s.strip() for s in (raw or "").splitlines() if s.strip()]

    def _scene_payload(self) -> dict:
        return {
            "provider": self._scene_provider(),
            "blocklist": self._parse_blocklist(self._scene_blocklist_str()),
            "proactive_enabled": self._proactive_enabled(),
            "scene_enabled": self._scene_enabled(),
            "scene_interval_min": self._scene_interval_min(),
            "intent_perceive_enabled": self._intent_perceive_enabled(),
            "intent_perceive_keywords": self._parse_keywords(
                self._intent_perceive_keywords_str()
            ),
        }

    def _asr_payload(self) -> dict:
        return {
            "voice_input_enabled": self._voice_input_enabled(),
            "asr_url": str(self.config.get("asr_url") or DEFAULT_ASR_URL).strip(),
        }

    def _voice_input_enabled(self) -> bool:
        return bool(self.config.get("voice_input_enabled", True))

    async def pet_scene_config(self):
        """壳端拉取主动对话/桌面感知配置（服务侧统一下发）。"""
        return self._scene_payload()

    async def pet_asr_config(self):
        """壳端拉取语音输入配置（开关/ASR 地址）。"""
        return self._asr_payload()

    async def pet_status_report(self):
        """壳端状态上报（主动对话/桌面感知/语音输入监控），仅存内存，重启即清。"""
        raw = await request.body()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        events = body.get("events")
        last_scene = body.get("last_scene")
        asr = body.get("asr")
        self._shell_report = {
            "at": time.time(),
            "proactive_enabled": bool(body.get("proactive_enabled")),
            "scene_enabled": bool(body.get("scene_enabled")),
            "scene_interval_min": body.get("scene_interval_min"),
            "events": events[-20:] if isinstance(events, list) else [],
            "last_scene": last_scene if isinstance(last_scene, dict) else None,
            "asr": asr if isinstance(asr, dict) else None,
        }
        return {"ok": True}

    # ---------- 控制页（pages/pet）后端 ----------

    def _tts_base_url(self) -> str:
        return str(self.config.get("tts_base_url") or "http://172.18.0.1:5000").rstrip("/")

    def _config_path(self) -> str:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return os.path.join(
            get_astrbot_data_path(), "config", "astrbot_plugin_desktop_pet_config.json"
        )

    def _persist_config(self) -> None:
        path = self._config_path()
        try:
            data = {}
            if os.path.exists(path):
                with open(path, encoding="utf-8-sig") as f:
                    data = json.load(f)
            for k in (
                TTS_CONFIG_KEYS
                + PAGE_CONFIG_KEYS
                + SCENE_CONFIG_KEYS
                + ASR_CONFIG_KEYS
                + MEMORY_CONFIG_KEYS
            ):
                if k in self.config:
                    data[k] = self.config[k]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[desktop_pet] persist config failed: {e}")

    async def _sbv2_status(self) -> dict:
        t0 = time.time()
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(f"{self._tts_base_url()}/status") as resp:
                    if resp.status != 200:
                        return {"reachable": False, "error": f"HTTP {resp.status}"}
                    data = await resp.json()
            data["reachable"] = True
            data["latency_ms"] = round((time.time() - t0) * 1000)
            return data
        except Exception as e:
            return {"reachable": False, "error": str(e)}

    async def page_status(self):
        default_persona = None
        try:
            default_persona = self.context.persona_manager.default_persona
        except Exception:
            pass
        return {
            "plugin": "astrbot_plugin_desktop_pet",
            "tts_enabled": self._tts_enabled(),
            "pet_session_id": self._pet_session_id(),
            "master_name": self._master_name(),
            "master_qq": self._master_qq(),
            "qq_jp_dub_enabled": self._qq_jp_dub_enabled(),
            "default_persona": default_persona,
            "sbv2": await self._sbv2_status(),
            "scene": self._scene_payload(),
            "asr": self._asr_payload(),
            "asr_state": (self._shell_report or {}).get("asr"),
            "memory": self._memory_summary(),
            "shell_report": self._shell_report,
            "shell_report_age_s": (
                round(time.time() - self._shell_report["at"]) if self._shell_report else None
            ),
        }

    async def page_sbv2_models(self):
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(f"{self._tts_base_url()}/models/info") as resp:
                    if resp.status != 200:
                        return error_response(f"SBV2 HTTP {resp.status}", status_code=502)
                    return {"models": await resp.json()}
        except Exception as e:
            return error_response(f"SBV2 不可达: {e}", status_code=502)

    async def page_tts_config(self):
        if request.method == "GET":
            return {k: self.config.get(k) for k in TTS_CONFIG_KEYS}
        payload = await request.json(default={})
        updated = {}
        for k in TTS_CONFIG_KEYS:
            if k not in payload:
                continue
            v = payload[k]
            try:
                if k == "tts_enabled":
                    v = bool(v)
                elif k in ("tts_model_id", "tts_speaker_id"):
                    v = int(v)
                elif k == "tts_length":
                    v = float(v)
                else:
                    v = str(v)
            except (TypeError, ValueError):
                return error_response(f"invalid value for {k}", status_code=400)
            self.config[k] = v
            updated[k] = v
        self._persist_config()
        return {"saved": True, "updated": updated}

    async def page_master_config(self):
        if request.method == "GET":
            return {k: self.config.get(k, "") for k in PAGE_CONFIG_KEYS}
        payload = await request.json(default={})
        updated = {}
        for k in PAGE_CONFIG_KEYS:
            if k not in payload:
                continue
            v = payload[k]
            if k == "qq_jp_dub_enabled":
                v = bool(v)
            else:
                v = str(v).strip()
            self.config[k] = v
            updated[k] = v
        self._persist_config()
        return {"saved": True, "updated": updated}

    # ---------- 桌宠会话人格（控制页直接设置，无需进隐藏 /chat 页） ----------

    def _pet_umo(self) -> str:
        sid = self._pet_session_id()
        return f"webchat:FriendMessage:webchat!{sid}!{sid}"

    async def _pet_conversation(self):
        """返回 (conversation_id, Conversation) 或 (None, None)（桌宠尚未发言时无会话）。"""
        try:
            umo = self._pet_umo()
            cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if not cid:
                return None, None
            conv = await self.context.conversation_manager.get_conversation(umo, cid)
            return cid, conv
        except Exception as e:
            logger.warning(f"[desktop_pet] get pet conversation failed: {e}")
            return None, None

    def _persona_names(self) -> list[str]:
        mgr = self.context.persona_manager
        names = []
        try:
            for p in mgr.personas_v3 or []:
                name = p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
                if name:
                    names.append(name)
        except Exception as e:
            logger.warning(f"[desktop_pet] list personas failed: {e}")
        if "default" not in names:
            names.insert(0, "default")
        return names

    async def page_persona_config(self):
        mgr = self.context.persona_manager
        cid, conv = await self._pet_conversation()
        if request.method == "GET":
            return {
                "conversation_exists": conv is not None,
                "current_persona_id": getattr(conv, "persona_id", None) if conv else None,
                "default_persona": getattr(mgr, "default_persona", None),
                "personas": self._persona_names(),
            }
        payload = await request.json(default={})
        pid = str(payload.get("persona_id") or "").strip()
        if not pid:
            return error_response("persona_id is required", status_code=400)
        known = set(self._persona_names())
        if known and pid not in known:
            return error_response(f"人格「{pid}」不存在", status_code=400)
        if not cid:
            return error_response(
                "桌宠会话尚不存在：请先让桌宠发一条消息，再来设置人格", status_code=400
            )
        try:
            await self.context.conversation_manager.update_conversation(
                self._pet_umo(), conversation_id=cid, persona_id=pid
            )
        except Exception as e:
            logger.warning(f"[desktop_pet] set pet persona failed: {e}")
            return error_response(f"设置失败: {e}", status_code=500)
        logger.info(f"[desktop_pet] pet persona set to {pid} (cid={cid})")
        return {"saved": True, "current_persona_id": pid}

    async def page_tts_test(self):
        payload = await request.json(default={})
        text = str(payload.get("text") or "").strip() or "こんにちは"
        audio = await self._synthesize(text, overrides=payload)
        if audio is None:
            return error_response("合成失败，请检查 SBV2 服务与参数", status_code=502)
        return {"audio": audio, "format": "wav"}

    def _list_providers(self) -> list[dict]:
        """枚举已配置的 LLM provider（控制页视觉模型下拉用）。
        modalities 为空列表 = 全支持（AstrBot 迁移兼容语义）。"""
        out = []
        try:
            for p in self.context.get_all_providers():
                try:
                    meta = p.meta()
                    modalities = list(p.provider_config.get("modalities") or [])
                    out.append(
                        {
                            "id": meta.id,
                            "model": meta.model,
                            "modalities": modalities,
                            "supports_image": (not modalities) or ("image" in modalities),
                        }
                    )
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"[desktop_pet] list providers failed: {e}")
        return out

    async def page_scene_config(self):
        if request.method == "GET":
            return {
                "scene_provider": self._scene_provider(),
                "scene_blocklist": self._scene_blocklist_str(),
                "proactive_enabled": self._proactive_enabled(),
                "scene_enabled": self._scene_enabled(),
                "scene_interval_min": self._scene_interval_min(),
                "intent_perceive_enabled": self._intent_perceive_enabled(),
                "intent_perceive_keywords": self._intent_perceive_keywords_str(),
                "providers": self._list_providers(),
            }
        payload = await request.json(default={})
        updated = {}
        if "scene_provider" in payload:
            v = str(payload["scene_provider"]).strip()  # 允许留空（跟随会话默认模型）
            if v:
                known = {p["id"] for p in self._list_providers()}
                if known and v not in known:
                    return error_response(
                        f"provider「{v}」不在已配置列表中，请检查拼写", status_code=400
                    )
            self.config["scene_provider"] = v
            updated["scene_provider"] = v
        if "scene_blocklist" in payload:
            v = str(payload["scene_blocklist"]).strip() or DEFAULT_SCENE_BLOCKLIST
            self.config["scene_blocklist"] = v
            updated["scene_blocklist"] = v
        if "proactive_enabled" in payload:
            v = bool(payload["proactive_enabled"])
            self.config["proactive_enabled"] = v
            updated["proactive_enabled"] = v
        if "scene_enabled" in payload:
            v = bool(payload["scene_enabled"])
            self.config["scene_enabled"] = v
            updated["scene_enabled"] = v
        if "scene_interval_min" in payload:
            try:
                v = max(1, int(payload["scene_interval_min"]))
            except (TypeError, ValueError):
                return error_response("invalid value for scene_interval_min", status_code=400)
            self.config["scene_interval_min"] = v
            updated["scene_interval_min"] = v
        if "intent_perceive_enabled" in payload:
            v = bool(payload["intent_perceive_enabled"])
            self.config["intent_perceive_enabled"] = v
            updated["intent_perceive_enabled"] = v
        if "intent_perceive_keywords" in payload:
            v = str(payload["intent_perceive_keywords"]).strip() or DEFAULT_INTENT_PERCEIVE_KEYWORDS
            self.config["intent_perceive_keywords"] = v
            updated["intent_perceive_keywords"] = v
        self._persist_config()
        return {"saved": True, "updated": updated}

    async def page_asr_config(self):
        if request.method == "GET":
            return {
                "voice_input_enabled": self._voice_input_enabled(),
                "asr_url": str(self.config.get("asr_url") or DEFAULT_ASR_URL).strip(),
            }
        payload = await request.json(default={})
        updated = {}
        if "voice_input_enabled" in payload:
            v = bool(payload["voice_input_enabled"])
            self.config["voice_input_enabled"] = v
            updated["voice_input_enabled"] = v
        if "asr_url" in payload:
            v = str(payload["asr_url"]).strip() or DEFAULT_ASR_URL
            self.config["asr_url"] = v
            updated["asr_url"] = v
        self._persist_config()
        return {"saved": True, "updated": updated}

    async def page_token_stats(self):
        return await asyncio.to_thread(self._get_provider_stats)

    # ---------- 管道模式：给桌宠 webchat 会话追加输出格式要求 ----------

    # 桌宠 webchat 会话的发送者是内部账号 "desktop_pet"：若不修正，任何读取
    # sender 昵称的下游（会话上下文、本插件群聊式归属、历史上的 LivingMemory）
    # 都会把 "desktop_pet" 当成主人昵称。这里统一改写为主人的 QQ 号/称呼。
    @filter.on_llm_request(priority=10)
    async def pre_fix_pet_sender(self, event: AstrMessageEvent, req: ProviderRequest):
        umo = event.unified_msg_origin or ""
        sid = self._pet_session_id()
        # 桌宠会话 umo 形如 webchat:FriendMessage:webchat!{username}!{conversation_id}
        if umo.startswith("webchat:") and umo.endswith(f"!{sid}"):
            qq = self._master_qq() or "master"
            name = self._master_name() or "主人"
            try:
                from astrbot.core.platform.astrbot_message import MessageMember
                mo = getattr(event, "message_obj", None)
                if mo is not None and getattr(mo, "sender", None) is not None:
                    mo.sender = MessageMember(qq, name)
            except Exception as e:
                logger.warning(f"[desktop_pet] pre-fix pet sender failed: {e}")

    @filter.on_llm_request(priority=5)
    async def strip_history_images(self, event: AstrMessageEvent, req: ProviderRequest):
        contexts = getattr(req, "contexts", None)
        if isinstance(contexts, list):
            _strip_image_parts(contexts)
            _strip_think_parts(contexts)

    @staticmethod
    def _restore_pet_sender(event: AstrMessageEvent, sid: str) -> None:
        """还原发送者：助手消息入库仍用原 sender（总结 prompt 靠 [Bot:] 前缀区分自己），
        仅用户消息以主人身份入库。"""
        try:
            from astrbot.core.platform.astrbot_message import MessageMember
            mo = getattr(event, "message_obj", None)
            if mo is not None and getattr(mo, "sender", None) is not None:
                mo.sender = MessageMember(sid, sid)
        except Exception as e:
            logger.warning(f"[desktop_pet] restore pet sender failed: {e}")

    # priority=-10：必须后于记忆类插件（如 LivingMemory，默认 0）等注入型插件执行，
    # 否则其注入内容（可能含旧的 desktop_pet 身份文本）绕过身份改写
    @filter.on_llm_request(priority=-10)
    async def inject_pet_format(self, event: AstrMessageEvent, req: ProviderRequest):
        umo = event.unified_msg_origin or ""
        sid = self._pet_session_id()
        # 桌宠会话 umo 形如 webchat:FriendMessage:webchat!{username}!{conversation_id}
        if umo.startswith("webchat:") and umo.endswith(f"!{sid}"):
            self._rewrite_pet_identity(req)
            self._restore_pet_sender(event, sid)
            tpl = EMOTION_INSTRUCTION_TTS if self._tts_enabled() else EMOTION_INSTRUCTION
            req.system_prompt = (req.system_prompt or "") + self._master_identity_note(
                for_pet=True
            ) + tpl.format(emotions="、".join(EMOTIONS))
            if self._tts_enabled():
                # 长人格 prompt 会稀释 system 侧格式要求，在用户消息末尾再提醒一次关键格式
                reminder = (
                    "\n（格式提醒：本次回复必须包含【情绪】中文正文和【JP】日语配音稿三部分，"
                    "【JP】为纯日语，缺一不可。）"
                )
                req.prompt = (req.prompt or "") + reminder
            return
        # 以下仅处理 QQ（aiocqhttp）会话
        if event.get_platform_name() != "aiocqhttp":
            return
        # 主人本人发送的消息标注身份，与桌宠用户视为同一人
        master_qq = self._master_qq()
        if master_qq and str(event.get_sender_id()) == master_qq:
            req.system_prompt = (req.system_prompt or "") + self._master_identity_note(
                for_pet=False
            )
        elif master_qq:
            nickname = (event.get_sender_name() or str(event.get_sender_id())).strip()
            req.system_prompt = (req.system_prompt or "") + (
                f"\n\n【身份说明】本条消息的发送者不是你的主人"
                f"（你的主人只有{self._master_name() or '主人'}，QQ {master_qq} 一人）。"
                f"当前发送者是「{nickname}」，请用其昵称或“你”称呼对方，不要称呼主人。"
            )
        # QQ 日语配音：要求回复带【JP】日语配音稿（on_decorating_result 里合成语音）
        if self._qq_jp_dub_enabled():
            req.system_prompt = (req.system_prompt or "") + JP_DUB_INSTRUCTION
            # 长人格 prompt 会稀释 system 侧格式要求，在用户消息末尾再提醒一次
            req.prompt = (req.prompt or "") + (
                "\n（格式提醒：本次回复必须包含中文正文和【JP】日语配音稿两部分，"
                "【JP】为纯日语，缺一不可。）"
            )
            logger.info(f"[desktop_pet] qq jp dub injected: {event.unified_msg_origin}")

    @filter.on_decorating_result()
    async def attach_jp_voice(self, event: AstrMessageEvent):
        """QQ 日语配音：把回复拆成「中文文字 + 日语配音语音」。"""
        if not self._qq_jp_dub_enabled():
            return
        if event.get_platform_name() != "aiocqhttp":
            return
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return
        new_chain = []
        changed = False
        for comp in result.chain:
            if isinstance(comp, Plain) and _JP_TAG.search(comp.text or ""):
                changed = True
                zh, jp = self._split_jp(comp.text)
                if not jp:
                    new_chain.append(comp)
                    continue
                path = None
                audio_b64 = await self._synthesize(jp)
                if audio_b64:
                    path = self._write_temp_wav(audio_b64)
                if path:
                    if zh:
                        new_chain.append(Plain(zh))
                    new_chain.append(Record(file=path, url=path))
                    try:
                        event.track_temporary_local_file(path)
                    except Exception:
                        pass
                else:
                    # 合成失败：降级为纯中文文字，不把【JP】日语稿泄漏到群里
                    logger.warning("[desktop_pet] qq jp dub synth failed, text only")
                    new_chain.append(Plain(zh or comp.text))
                continue
            new_chain.append(comp)
        if changed:
            result.chain = new_chain

    # ---------- 内置长期记忆（向量召回 + LLM 反思，独立于 LivingMemory） ----------

    def _memory_enabled(self) -> bool:
        return bool(self.config.get("memory_enabled", True))

    def _memory_reflect_batch_messages(self) -> int:
        try:
            return max(
                2,
                int(
                    self.config.get("memory_reflect_batch_messages")
                    or DEFAULT_MEMORY_REFLECT_BATCH_MESSAGES
                ),
            )
        except (TypeError, ValueError):
            return DEFAULT_MEMORY_REFLECT_BATCH_MESSAGES

    def _memory_recall_top_k(self) -> int:
        try:
            return max(
                1,
                int(self.config.get("memory_recall_top_k") or DEFAULT_MEMORY_RECALL_TOP_K),
            )
        except (TypeError, ValueError):
            return DEFAULT_MEMORY_RECALL_TOP_K

    def _memory_recall_min_score(self) -> float:
        try:
            return float(
                self.config.get("memory_recall_min_score")
                if self.config.get("memory_recall_min_score") is not None
                else DEFAULT_MEMORY_RECALL_MIN_SCORE
            )
        except (TypeError, ValueError):
            return DEFAULT_MEMORY_RECALL_MIN_SCORE

    def _memory_recall_max_chars(self) -> int:
        try:
            return max(
                100,
                int(
                    self.config.get("memory_recall_max_chars")
                    or DEFAULT_MEMORY_RECALL_MAX_CHARS
                ),
            )
        except (TypeError, ValueError):
            return DEFAULT_MEMORY_RECALL_MAX_CHARS

    def _memory_diary_enabled(self) -> bool:
        return bool(self.config.get("memory_diary_enabled", True))

    def _memory_scope_private_enabled(self) -> bool:
        return bool(self.config.get("memory_scope_private_enabled", True))

    def _memory_scope_group_enabled(self) -> bool:
        return bool(self.config.get("memory_scope_group_enabled", True))

    def _memory_group_context_count(self) -> int:
        try:
            v = self.config.get("memory_group_context_count")
            if v is None:
                return DEFAULT_MEMORY_GROUP_CONTEXT_COUNT
            return max(0, int(v))
        except (TypeError, ValueError):
            return DEFAULT_MEMORY_GROUP_CONTEXT_COUNT

    def _memory_scope_independent(self) -> frozenset:
        """当前被标记为「独立」的范围集合。"""
        return frozenset(
            s
            for s in MEMORY_SCOPES
            if self.config.get(f"memory_scope_{s}_independent", False)
        )

    def _memory_scope_of(self, event: AstrMessageEvent):
        """事件 → 记忆范围（pet/private/group）；未启用或不属于捕获范围返回 None。

        pet：桌宠 webchat 会话；private：主人 master_qq 的私聊；group：群聊。
        """
        umo = event.unified_msg_origin or ""
        if self._is_pet_umo(umo):
            return "pet"
        try:
            mt = event.get_message_type()
            mtype = getattr(mt, "value", None) or str(mt)
        except Exception:
            mtype = ""
        if mtype == "FriendMessage":
            if not self._memory_scope_private_enabled():
                return None
            master = self._master_qq()
            try:
                sender = str(event.get_sender_id() or "").strip()
            except Exception:
                sender = ""
            return "private" if master and sender == master else None
        if mtype == "GroupMessage":
            return "group" if self._memory_scope_group_enabled() else None
        return None

    def _init_memory(self) -> None:
        try:
            try:
                from astrbot.api.star import StarTools

                data_dir = Path(StarTools.get_data_dir("astrbot_plugin_desktop_pet"))
            except Exception:
                data_dir = (
                    Path(__file__).resolve().parents[2]
                    / "plugin_data"
                    / "astrbot_plugin_desktop_pet"
                )
            self._mem_store = PetMemoryStore(data_dir / "memory.db")
            logger.info(f"[desktop_pet] memory store ready: {self._mem_store.db_path}")
        except Exception as e:
            logger.warning(f"[desktop_pet] memory store init failed: {e}")
            self._mem_store = None

    def _is_pet_umo(self, umo: str) -> bool:
        sid = self._pet_session_id()
        return umo.startswith("webchat:") and umo.endswith(f"!{sid}")

    def _memory_embed_provider(self):
        """当前可用的 embedding provider；未配置/失效返回 None（记忆降级，绝不拒用）。"""
        pid = str(self.config.get("memory_embedding_provider_id") or "").strip()
        try:
            if pid:
                p = self.context.get_provider_by_id(pid)
                return p if p is not None and hasattr(p, "get_embedding") else None
            get_all = getattr(self.context, "get_all_embedding_providers", None)
            if get_all is None:
                return None
            provs = get_all() or []
            return provs[0] if provs else None
        except Exception as e:
            logger.warning(f"[desktop_pet] resolve embedding provider failed: {e}")
            return None

    @staticmethod
    def _memory_embed_model_id(prov) -> str:
        try:
            return getattr(prov.meta(), "id", None) or "unknown"
        except Exception:
            return "unknown"

    async def _memory_llm_provider(self):
        """反思/日记用 LLM：memory_provider_id 指定，留空跟随桌宠会话当前模型。"""
        pid = str(self.config.get("memory_provider_id") or "").strip()
        if pid:
            try:
                p = self.context.get_provider_by_id(pid)
                if p is not None:
                    return p
            except Exception:
                pass
        try:
            return await self.context.get_using_provider_async(self._pet_umo())
        except AttributeError:
            return self.context.get_using_provider(self._pet_umo())
        except Exception as e:
            logger.warning(f"[desktop_pet] resolve memory llm provider failed: {e}")
            return None

    async def _memory_embed_texts(self, texts: list[str]):
        """批量嵌入，返回 (vectors, model_id)；provider 不可用/失败返回 None。"""
        if not texts:
            return None
        prov = self._memory_embed_provider()
        if prov is None:
            return None
        try:
            chunk = 16  # 部分服务商(如阿里云)限制单批 ≤20 条
            batch = [str(t)[:500] for t in texts]
            if hasattr(prov, "get_embeddings"):
                vecs = []
                for i in range(0, len(batch), chunk):
                    part = await prov.get_embeddings(batch[i : i + chunk])
                    if not part:
                        return None
                    vecs.extend(part)
            else:
                vecs = [await prov.get_embedding(t) for t in batch]
            if not vecs or len(vecs) != len(texts):
                return None
            return vecs, self._memory_embed_model_id(prov)
        except Exception as e:
            logger.warning(f"[desktop_pet] memory embed failed: {e}")
            return None

    def _memory_rewrite_identity(self, text: str) -> str:
        """记忆文本里的 pet_session_id 一律改写为主人称呼。"""
        sid = self._pet_session_id()
        name = self._master_name() or "主人"
        if sid and sid in text:
            text = text.replace(sid, name)
        return text

    # ---- 捕获钩子：用户消息(+10) / 助手回复(on_llm_response) 落 chat_log ----

    @filter.custom_filter(_GroupContextCaptureFilter, False)
    async def group_context_passive_capture(self, event: AstrMessageEvent):
        """被动群消息监听占位：过滤器副作用已入缓冲，本 handler 永不执行。"""
        return

    def _group_context_push(self, event: AstrMessageEvent) -> None:
        """被动监听到的群消息入前文缓冲（custom_filter 副作用，绝不抛异常）。"""
        buf = getattr(self, "_group_ctx", None)
        if buf is None or self._mem_store is None or not self._memory_enabled():
            return
        if not self._memory_scope_group_enabled():
            return
        n = self._memory_group_context_count()
        if n <= 0:
            return
        try:
            mt = event.get_message_type()
            mtype = getattr(mt, "value", None) or str(mt)
        except Exception:
            return
        if mtype != "GroupMessage":
            return
        try:
            sid = str(event.get_sender_id() or "").strip()
            self_id = str(getattr(event.message_obj, "self_id", "") or "").strip()
            if self_id and sid and self_id == sid:
                return  # 自己的发言不进前文（回复已由响应钩子成对落库）
            gid = str(event.get_group_id() or "").strip()
            who = str(event.get_sender_name() or "").strip() or sid
            text = str(event.get_message_str() or "").strip()
        except Exception:
            return
        buf.set_capacity(n)
        buf.push(gid, who, text)

    def _group_context_prefix(self, event: AstrMessageEvent, who: str, text: str) -> str:
        """群聊触发消息的前文块（无则空串）。"""
        buf = getattr(self, "_group_ctx", None)
        if buf is None:
            return ""
        try:
            gid = str(event.get_group_id() or "").strip()
        except Exception:
            return ""
        return buf.render(gid, trigger_sender=who, trigger_text=text)

    @filter.on_llm_request(priority=10)
    async def pet_mem_capture_req(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._memory_enabled() or self._mem_store is None:
            return
        scope = self._memory_scope_of(event)
        if scope is None:
            return
        text = _IDENT_REMINDER.sub("", getattr(req, "prompt", None) or "").strip()
        if not text:
            return
        if scope == "group":
            # 群聊：说话人前缀落库，反思时事实才能归属到具体的人
            try:
                who = str(event.get_sender_name() or "").strip()
            except Exception:
                who = ""
            if not who:
                try:
                    who = str(event.get_sender_id() or "").strip()
                except Exception:
                    who = ""
            # 拼接前文（被动监听的滚动缓冲），让反思看懂前因后果
            ctx = self._group_context_prefix(event, who, text)
            if who:
                text = f"{who}: {text}"
            if ctx:
                text = f"{ctx}\n{text}"
        try:
            self._mem_store.log_message("user", text, scope)
        except Exception as e:
            logger.warning(f"[desktop_pet] memory log user msg failed: {e}")

    @filter.on_llm_response()
    async def pet_mem_capture_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        if not self._memory_enabled() or self._mem_store is None:
            return
        scope = self._memory_scope_of(event)
        if scope is None:
            return
        if getattr(resp, "tools_call_name", None):
            return  # 工具循环中间响应不入记忆
        zh, _jp = self._split_jp(getattr(resp, "completion_text", "") or "")
        core = strip_leading_tags(zh)
        if not core:
            if scope == "pet" and "略过" in zh:
                # 【略过】的场景轮没有记忆价值，整对丢弃（桌宠专属机制）
                try:
                    self._mem_store.delete_last_user_message("pet")
                except Exception:
                    pass
            return
        try:
            self._mem_store.log_message("assistant", core, scope)
            batch = self._memory_reflect_batch_messages()
            if self._mem_store.unreflected_count(scope) >= batch:
                task = self._mem_reflect_task
                if task is None or task.done():
                    # 当时所有达到阈值的 scope 一并处理
                    due = [
                        s
                        for s in MEMORY_SCOPES
                        if self._mem_store.unreflected_count(s) >= batch
                    ]
                    self._mem_reflect_task = asyncio.create_task(
                        self._memory_reflect(due)
                    )
        except Exception as e:
            logger.warning(f"[desktop_pet] memory log assistant msg failed: {e}")

    # ---- 「记住 xxx」/「永久记住 xxx」指令：主人直写记忆，固定回复并终止后续管线 ----

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def memory_remember_command(self, event: AstrMessageEvent):
        if not self._memory_enabled() or self._mem_store is None:
            return  # 记忆未启用/未就绪：不拦截，消息正常走 LLM
        umo = event.unified_msg_origin or ""
        try:
            sender = str(event.get_sender_id() or "").strip()
        except Exception:
            sender = ""
        master = self._master_qq()
        if not (self._is_pet_umo(umo) or (master and sender == master)):
            return  # 非主人：不拦截其消息
        parsed = parse_remember_command(str(event.get_message_str() or ""))
        if parsed is None:
            return
        scope = self._memory_scope_of(event)
        if scope is None:
            return
        permanent, content = parsed
        content = self._memory_rewrite_identity(content)
        store = self._mem_store
        dup = await asyncio.to_thread(store.find_duplicate, content)
        if dup is not None:
            yield event.plain_result("这条我已经记着了。")
            event.stop_event()
            return
        vec = model = None
        embedded = await self._memory_embed_texts([content])
        if embedded:
            vec, model = embedded[0][0], embedded[1]
        try:
            await asyncio.to_thread(
                store.add_memory,
                "fact",
                content,
                5 if permanent else 4,
                "command",
                vec,
                model,
                scope,
            )
        except Exception as e:
            logger.warning(f"[desktop_pet] remember command add failed: {e}")
            return
        yield event.plain_result("这条我会永远记住的。" if permanent else "记住啦。")
        event.stop_event()

    # ---- 召回注入钩子：priority=-5，晚于其它注入型插件、早于格式注入(-10) ----

    @filter.on_llm_request(priority=-5)
    async def inject_pet_memory(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._memory_enabled() or self._mem_store is None:
            return
        scope = self._memory_scope_of(event)
        if scope is None:
            return
        result = await self._memory_recall_block(
            getattr(req, "prompt", None) or "", scope=scope
        )
        if not result or not result.get("block"):
            return
        block = result["block"]
        try:
            from astrbot.core.agent.message import TextPart

            parts = getattr(req, "extra_user_content_parts", None)
            if parts is None:
                req.extra_user_content_parts = []
                parts = req.extra_user_content_parts
            parts.append(TextPart(text=block).mark_as_temp())
        except Exception as e:
            logger.warning(f"[desktop_pet] inject memory via extra parts failed: {e}")
            req.prompt = (req.prompt or "") + "\n\n" + block

    async def _memory_recall_block(self, query: str, scope: str = "pet", dry: bool = False):
        """向量召回 + 混合重排 + 档案常驻，返回 {"block", "hits", "vector"} 或 None。

        scope 决定召回池（独立范围只见自己，否则见所有未独立范围）。
        dry=True 时（控制页召回测试）不更新召回统计。
        """
        store = self._mem_store
        if store is None:
            return None
        pool = pool_scopes(scope, self._memory_scope_independent())
        top_k = self._memory_recall_top_k()
        min_score = self._memory_recall_min_score()
        terms = extract_terms(query or "")
        now = datetime.now()
        hits: list[dict] = []
        used_vector = False
        prov = self._memory_embed_provider()
        if prov is not None and (query or "").strip():
            try:
                vec = await prov.get_embedding(query[:800])
                model = self._memory_embed_model_id(prov)
                ok = await asyncio.to_thread(store.ensure_index, model, len(vec))
                if ok:
                    raw = await asyncio.to_thread(store.vec_search, vec, top_k * 4)
                    rows = await asyncio.to_thread(
                        store.get_memories_by_ids, [mid for mid, _ in raw], pool
                    )
                    for mid, cos in raw:
                        row = rows.get(mid)
                        if row is None or cos < min_score or row["kind"] == "profile":
                            continue
                        dt = parse_dt(row["created_at"])
                        age_days = (
                            max(0.0, (now - dt).total_seconds() / 86400) if dt else 0.0
                        )
                        content_l = row["content"].lower()
                        sh = sum(1 for t in terms if t in content_l)
                        row["cos"] = round(cos, 3)
                        row["score"] = blend_score(
                            cos, row["importance"], age_days, row["recall_count"], sh
                        )
                        hits.append(row)
                    used_vector = True
            except Exception as e:
                logger.warning(f"[desktop_pet] memory vector recall failed, fallback: {e}")
        if not hits:
            # 降级：无向量命中时按重要度 + 时效取最近的高分记忆
            rows = await asyncio.to_thread(store.recent_active, top_k * 2, ("profile",), pool)
            for row in rows:
                dt = parse_dt(row["created_at"])
                age_days = max(0.0, (now - dt).total_seconds() / 86400) if dt else 0.0
                row["cos"] = None
                row["score"] = blend_score(
                    0.0, row["importance"], age_days, row["recall_count"], 0
                )
                hits.append(row)
        hits.sort(key=lambda r: r["score"], reverse=True)
        pinned = await asyncio.to_thread(store.profile_memories, 5, pool)
        final: list[dict] = list(pinned)
        seen = {m["id"] for m in final}
        for h in hits:
            if h["id"] in seen:
                continue
            final.append(h)
            seen.add(h["id"])
            if len(final) >= len(pinned) + top_k:
                break
        if not final:
            return None
        if not dry:
            await asyncio.to_thread(store.touch_recalled, [m["id"] for m in final])
        return {
            "block": build_injection(final, self._memory_recall_max_chars(), scope=scope),
            "hits": final,
            "vector": used_vector,
        }

    # ---- 反思：事件驱动，每个 scope 各自攒满 N 条消息抽取一次；失败即跳过、光标照常前进，不留积压 ----

    async def _memory_reflect(self, scopes=None):
        """scopes=None：处理所有有未反思消息的 scope；否则只处理给定 scope。"""
        store = self._mem_store
        if store is None:
            return
        if scopes is None:
            todo = []
            for s in MEMORY_SCOPES:
                try:
                    if await asyncio.to_thread(store.unreflected_count, s) > 0:
                        todo.append(s)
                except Exception:
                    pass
        else:
            todo = [s for s in dict.fromkeys(scopes) if s in MEMORY_SCOPES]
        if not todo:
            return
        max_msgs = min(60, self._memory_reflect_batch_messages() * 4)
        provider = None
        provider_fetched = False
        for scope in todo:
            max_id = None
            try:
                rows, last_id = await asyncio.to_thread(
                    store.unreflected_window, scope, max_msgs
                )
                if not rows:
                    continue
                max_id = last_id
                if not provider_fetched:
                    provider = await self._memory_llm_provider()
                    provider_fetched = True
                if provider is None:
                    logger.warning(
                        "[desktop_pet] memory reflect skipped: no llm provider"
                    )
                    continue
                system = REFLECT_SYSTEM + (
                    REFLECT_SYSTEM_GROUP_ADDENDUM if scope == "group" else ""
                )
                resp = await provider.text_chat(
                    prompt=build_reflect_prompt(rows, self._master_name(), scope=scope),
                    system_prompt=system,
                )
                items = parse_memories_json(getattr(resp, "completion_text", "") or "")
                new_items = []
                seen_norm = set()
                for it in items:
                    it["content"] = self._memory_rewrite_identity(it["content"])
                    n = norm_text(it["content"])
                    if n in seen_norm:
                        continue
                    seen_norm.add(n)
                    dup = await asyncio.to_thread(store.find_duplicate, it["content"])
                    if dup is None:
                        new_items.append(it)
                embedded = await self._memory_embed_texts(
                    [it["content"] for it in new_items]
                )
                for i, it in enumerate(new_items):
                    vec = model = None
                    if embedded:
                        vec, model = embedded[0][i], embedded[1]
                    await asyncio.to_thread(
                        store.add_memory,
                        it["kind"],
                        it["content"],
                        it["importance"],
                        "chat",
                        vec,
                        model,
                        scope,
                    )
                logger.info(
                    f"[desktop_pet] memory reflect[{scope}]: +{len(new_items)} memories "
                    f"({len(items)} extracted, {len(rows)} msgs)"
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"[desktop_pet] memory reflect[{scope}] failed (skipped): {e}"
                )
            finally:
                if max_id:
                    try:
                        await asyncio.to_thread(store.mark_reflected, scope, max_id)
                        await asyncio.to_thread(
                            store.set_meta, "last_reflect_at", now_str()
                        )
                        await asyncio.to_thread(store.prune_chat_log, 200)
                    except Exception:
                        pass

    # ---- 每日维护（04:40）：重要度衰减 / 陈旧软删 / 补嵌向量 / 桌宠日记 ----

    async def _memory_daily_loop(self):
        while True:
            now = datetime.now()
            nxt = now.replace(hour=4, minute=40, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            await asyncio.sleep((nxt - now).total_seconds())
            try:
                await self._memory_daily_maintenance()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[desktop_pet] memory daily maintenance failed: {e}")

    async def _memory_daily_maintenance(self):
        if not self._memory_enabled() or self._mem_store is None:
            return
        store = self._mem_store
        decayed, archived = await asyncio.to_thread(store.apply_decay)
        backfilled = 0
        missing = await asyncio.to_thread(store.memories_missing_embedding, 200)
        if missing:
            embedded = await self._memory_embed_texts([m["content"] for m in missing])
            if embedded:
                vecs, model = embedded
                for m, v in zip(missing, vecs):
                    await asyncio.to_thread(store.set_embedding, m["id"], v, model)
                    backfilled += 1
        diary_id = await self._memory_write_diary()
        logger.info(
            f"[desktop_pet] memory daily: decayed={decayed} archived={archived} "
            f"backfilled={backfilled} diary={diary_id}"
        )

    async def _memory_write_diary(self):
        """每日一条「桌宠日记」（kind=diary），以桌宠人格口吻总结昨日对话。"""
        if not self._memory_diary_enabled():
            return None
        store = self._mem_store
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if await asyncio.to_thread(store.get_meta, "last_diary_date") == yesterday:
            return None
        rows = await asyncio.to_thread(store.chat_log_of_date, yesterday, "pet")
        if len(rows) < 4:  # 不足两轮对话不记
            await asyncio.to_thread(store.set_meta, "last_diary_date", yesterday)
            return None
        provider = await self._memory_llm_provider()
        if provider is None:
            return None
        system = await self._pet_persona_prompt() or "你是一只桌面桌宠。"
        resp = await provider.text_chat(
            prompt=build_diary_prompt(rows, self._master_name(), yesterday),
            system_prompt=system,
        )
        text = strip_leading_tags(
            self._split_jp(getattr(resp, "completion_text", "") or "")[0]
        )
        if len(text) < 10:
            return None
        embedded = await self._memory_embed_texts([text])
        vec = model = None
        if embedded:
            vec, model = embedded[0][0], embedded[1]
        mid = await asyncio.to_thread(
            store.add_memory,
            "diary",
            f"{yesterday} 的日记：{text}"[:400],
            3,
            "diary",
            vec,
            model,
        )
        await asyncio.to_thread(store.set_meta, "last_diary_date", yesterday)
        return mid

    async def _pet_persona_prompt(self):
        """桌宠会话当前人格的 prompt（日记口吻用）。"""
        try:
            _cid, conv = await self._pet_conversation()
            pid = getattr(conv, "persona_id", None) if conv else None
            mgr = self.context.persona_manager
            name = pid or getattr(mgr, "default_persona", None)
            for p in getattr(mgr, "personas_v3", None) or []:
                n = p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
                if n == name:
                    return (
                        p.get("prompt") if isinstance(p, dict) else getattr(p, "prompt", None)
                    )
        except Exception:
            pass
        return None

    async def _memory_reembed_all(self):
        store = self._mem_store
        if store is None:
            return
        try:
            rows = await asyncio.to_thread(store.all_active_contents, 5000)
            total = 0
            for i in range(0, len(rows), 64):
                batch = rows[i : i + 64]
                embedded = await self._memory_embed_texts(
                    [m["content"] for m in batch]
                )
                if not embedded:
                    break
                vecs, model = embedded
                for m, v in zip(batch, vecs):
                    await asyncio.to_thread(store.set_embedding, m["id"], v, model)
                total += len(batch)
            await asyncio.to_thread(store.invalidate_index)
            logger.info(f"[desktop_pet] memory reembed done: {total}/{len(rows)}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[desktop_pet] memory reembed failed: {e}")

    async def _memory_import_livingmemory(self):
        """从 LivingMemory 库只读全量导入（按会话映射 pet/private/group 范围）。"""
        store = self._mem_store
        lm_path = (
            Path(__file__).resolve().parents[2]
            / "plugin_data"
            / "astrbot_plugin_livingmemory"
            / "livingmemory.db"
        )
        result = await asyncio.to_thread(
            store.fetch_livingmemory_candidates,
            lm_path,
            self._pet_session_id(),
            self._master_qq(),
            self._master_name() or "主人",
        )
        if result.get("error"):
            return error_response(
                f"读取 LivingMemory 库失败: {result['error']}", status_code=400
            )
        pending = []
        skipped = 0
        for it in result.get("items") or []:
            dup = await asyncio.to_thread(store.find_duplicate, it["content"])
            if dup is not None:
                skipped += 1
            else:
                pending.append(it)
        imported = 0
        by_scope: dict[str, int] = {}
        for i in range(0, len(pending), 64):
            batch = pending[i : i + 64]
            embedded = await self._memory_embed_texts([it["content"] for it in batch])
            for j, it in enumerate(batch):
                vec = model = None
                if embedded:
                    vec, model = embedded[0][j], embedded[1]
                await asyncio.to_thread(
                    store.add_memory,
                    it["kind"],
                    it["content"],
                    it["importance"],
                    "import",
                    vec,
                    model,
                    it.get("scope") or "pet",
                )
                imported += 1
                sc = it.get("scope") or "pet"
                by_scope[sc] = by_scope.get(sc, 0) + 1
        skipped_sessions = result.get("skipped_sessions") or {}
        logger.info(
            f"[desktop_pet] imported {imported} memories from LivingMemory "
            f"(by_scope={by_scope}, dup_skipped={skipped}, "
            f"unmapped_sessions={skipped_sessions})"
        )
        return {
            "found": result.get("found", 0),
            "imported": imported,
            "skipped": skipped,
            "by_scope": by_scope,
            "skipped_sessions": skipped_sessions,
        }

    # ---- 控制页后端 ----

    def _memory_summary(self) -> dict:
        store = self._mem_store
        if store is None:
            return {"enabled": self._memory_enabled(), "ready": False}
        try:
            prov = self._memory_embed_provider()
            model = self._memory_embed_model_id(prov) if prov else None
            dim = None
            if prov is not None:
                try:
                    dim = prov.get_dim()
                except Exception:
                    pass
            return {
                "enabled": self._memory_enabled(),
                "ready": True,
                "vector": prov is not None and store.vector_available,
                "embedding_provider": model,
                "embedding_dim": dim,
                **store.stats(model),
            }
        except Exception as e:
            return {"enabled": self._memory_enabled(), "ready": False, "error": str(e)}

    def _list_embedding_providers(self) -> list[dict]:
        out = []
        try:
            get_all = getattr(self.context, "get_all_embedding_providers", None)
            for p in (get_all() if get_all else []) or []:
                try:
                    dim = None
                    try:
                        dim = p.get_dim()
                    except Exception:
                        pass
                    out.append({"id": getattr(p.meta(), "id", "?"), "dim": dim})
                except Exception:
                    continue
        except Exception:
            pass
        return out

    async def page_memory_config(self):
        if request.method == "GET":
            return {
                "memory_enabled": self._memory_enabled(),
                "memory_embedding_provider_id": str(
                    self.config.get("memory_embedding_provider_id") or ""
                ),
                "memory_provider_id": str(self.config.get("memory_provider_id") or ""),
                "memory_reflect_batch_messages": self._memory_reflect_batch_messages(),
                "memory_recall_top_k": self._memory_recall_top_k(),
                "memory_recall_min_score": self._memory_recall_min_score(),
                "memory_recall_max_chars": self._memory_recall_max_chars(),
                "memory_diary_enabled": self._memory_diary_enabled(),
                "memory_scope_private_enabled": self._memory_scope_private_enabled(),
                "memory_scope_group_enabled": self._memory_scope_group_enabled(),
                "memory_scope_pet_independent": bool(
                    self.config.get("memory_scope_pet_independent", False)
                ),
                "memory_scope_private_independent": bool(
                    self.config.get("memory_scope_private_independent", False)
                ),
                "memory_scope_group_independent": bool(
                    self.config.get("memory_scope_group_independent", False)
                ),
                "memory_group_context_count": self._memory_group_context_count(),
                "embedding_providers": self._list_embedding_providers(),
                "llm_providers": [p["id"] for p in self._list_providers()],
            }
        payload = await request.json(default={})
        updated = {}
        for k in MEMORY_CONFIG_KEYS:
            if k not in payload:
                continue
            v = payload[k]
            try:
                if k in MEMORY_CONFIG_BOOL_KEYS:
                    v = bool(v)
                elif k in (
                    "memory_reflect_batch_messages",
                    "memory_recall_top_k",
                    "memory_recall_max_chars",
                    "memory_group_context_count",
                ):
                    v = int(v)
                elif k == "memory_recall_min_score":
                    v = float(v)
                else:
                    v = str(v).strip()
            except (TypeError, ValueError):
                return error_response(f"invalid value for {k}", status_code=400)
            self.config[k] = v
            updated[k] = v
        self._persist_config()
        return {"saved": True, "updated": updated}

    async def page_memory_query(self):
        if self._mem_store is None:
            return error_response("记忆存储未就绪", status_code=500)
        q, offset, limit, scope = "", 0, 20, ""
        try:
            query = getattr(request, "query", None) or {}
            q = str(query.get("q", "") or "").strip()
            scope = str(query.get("scope", "") or "").strip()
            if scope not in MEMORY_SCOPES:
                scope = ""
            offset = max(0, int(query.get("offset", 0) or 0))
            limit = min(100, max(1, int(query.get("limit", 20) or 20)))
        except (TypeError, ValueError, AttributeError):
            pass
        items, total = await asyncio.to_thread(
            self._mem_store.list_memories, q, offset, limit, scope
        )
        return {
            "summary": self._memory_summary(),
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "scope": scope,
        }

    async def page_memory_op(self):
        if self._mem_store is None:
            return error_response("记忆存储未就绪", status_code=500)
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        store = self._mem_store
        if action == "add":
            content = self._memory_rewrite_identity(
                str(payload.get("content") or "").strip()
            )
            if len(content) < 2:
                return error_response("content is required", status_code=400)
            kind = str(payload.get("kind") or "fact").strip()
            scope = str(payload.get("scope") or "pet").strip()
            if scope not in MEMORY_SCOPES:
                scope = "pet"
            try:
                importance = int(payload.get("importance", 3))
            except (TypeError, ValueError):
                importance = 3
            dup = await asyncio.to_thread(store.find_duplicate, content)
            if dup is not None:
                return {"added": False, "duplicate_of": dup["id"]}
            embedded = await self._memory_embed_texts([content])
            vec = model = None
            if embedded:
                vec, model = embedded[0][0], embedded[1]
            mid = await asyncio.to_thread(
                store.add_memory, kind, content, importance, "manual", vec, model, scope
            )
            return {"added": True, "id": mid}
        if action == "delete":
            try:
                mid = int(payload.get("id"))
            except (TypeError, ValueError):
                return error_response("id is required", status_code=400)
            ok = await asyncio.to_thread(store.deactivate, mid)
            return {"deleted": ok}
        if action == "set_importance":
            try:
                mid = int(payload.get("id"))
                imp = int(payload.get("importance"))
            except (TypeError, ValueError):
                return error_response("id/importance is required", status_code=400)
            ok = await asyncio.to_thread(store.set_importance, mid, imp)
            return {"saved": ok}
        if action == "reflect_now":
            task = self._mem_reflect_task
            if task is not None and not task.done():
                return {"started": False, "reason": "反思任务正在进行中"}
            self._mem_reflect_task = asyncio.create_task(self._memory_reflect())
            return {"started": True}
        if action == "reembed":
            task = getattr(self, "_mem_reembed_task", None)
            if task is not None and not task.done():
                return {"started": False, "reason": "重建任务正在进行中"}
            self._mem_reembed_task = asyncio.create_task(self._memory_reembed_all())
            return {"started": True}
        if action == "import_livingmemory":
            return await self._memory_import_livingmemory()
        return error_response(f"unknown action: {action}", status_code=400)

    async def page_memory_recall_test(self):
        if self._mem_store is None:
            return error_response("记忆存储未就绪", status_code=500)
        payload = await request.json(default={})
        text = str(payload.get("text") or "").strip()
        if not text:
            return error_response("text is required", status_code=400)
        scope = str(payload.get("scope") or "pet").strip()
        if scope not in MEMORY_SCOPES:
            scope = "pet"
        result = await self._memory_recall_block(text, scope=scope, dry=True)
        if not result:
            return {"block": "", "hits": [], "vector": False, "scope": scope}
        hits = [
            {
                "id": h["id"],
                "kind": h["kind"],
                "scope": h.get("scope"),
                "content": h["content"][:80],
                "cos": h.get("cos"),
                "score": round(h.get("score", 0.0), 3),
            }
            for h in result["hits"]
        ]
        return {
            "block": result["block"],
            "hits": hits,
            "vector": result["vector"],
            "scope": scope,
        }

    async def page_memory_graph(self):
        """记忆图谱：节点 + 向量近邻边（无向量索引时仅节点）。"""
        if self._mem_store is None:
            return error_response("记忆存储未就绪", status_code=500)
        prov = self._memory_embed_provider()
        model = self._memory_embed_model_id(prov) if prov else None
        return await asyncio.to_thread(self._mem_store.graph_data, model)

    # ---------- 内部逻辑 ----------

    def _tts_enabled(self) -> bool:
        return bool(self.config.get("tts_enabled", False))

    def _pet_session_id(self) -> str:
        return str(self.config.get("pet_session_id") or "desktop_pet").strip() or "desktop_pet"

    def _master_name(self) -> str:
        return str(self.config.get("master_name") or "").strip()

    def _master_qq(self) -> str:
        return str(self.config.get("master_qq") or "").strip()

    async def _history_gc_loop(self):
        while True:
            now = datetime.now()
            nxt = now.replace(hour=4, minute=30, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            await asyncio.sleep((nxt - now).total_seconds())
            try:
                removed_img, removed_think, saved = await asyncio.to_thread(self._gc_history_images)
                if removed_img or removed_think:
                    logger.info(
                        f"[desktop_pet] history gc: stripped {removed_img} image parts, {removed_think} think parts, saved {saved} chars"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[desktop_pet] history gc failed: {e}")

    def _gc_history_images(self) -> tuple[int, int, int]:
        db_path = Path(__file__).resolve().parents[2] / "data_v4.db"
        total_removed_img = 0
        total_removed_think = 0
        total_saved = 0
        con = sqlite3.connect(str(db_path), timeout=30)
        try:
            cur = con.cursor()
            rows = cur.execute(
                "SELECT conversation_id, content FROM conversations"
            ).fetchall()
            for cid, content in rows:
                try:
                    history = json.loads(content)
                except Exception:
                    continue
                if not isinstance(history, list):
                    continue
                removed_img = _strip_image_parts(history)
                removed_think = _strip_think_parts(history)
                if removed_img or removed_think:
                    new_content = json.dumps(history, ensure_ascii=False)
                    cur.execute(
                        "UPDATE conversations SET content=? WHERE conversation_id=?",
                        (new_content, cid),
                    )
                    total_removed_img += removed_img
                    total_removed_think += removed_think
                    total_saved += len(content) - len(new_content)
            con.commit()
        finally:
            con.close()
        return total_removed_img, total_removed_think, total_saved

    def _get_provider_stats(self) -> dict:
        db_path = Path(__file__).resolve().parents[2] / "data_v4.db"
        if not db_path.exists():
            return {"has_data": False}

        try:
            # Safely open as read-only, fallback to standard connect if URI mode fails
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            except Exception:
                con = sqlite3.connect(str(db_path), timeout=5)
        except Exception:
            return {"has_data": False}

        try:
            cur = con.cursor()
            tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            if "provider_stats" not in tables:
                return {"has_data": False}

            columns = [row[1] for row in cur.execute("PRAGMA table_info(provider_stats)").fetchall()]

            has_timestamp = "created_at" in columns or "timestamp" in columns
            has_input = "token_input_other" in columns
            has_cached = "token_input_cached" in columns or "token_cached" in columns
            has_output = "token_output" in columns
            has_ttft = "time_to_first_token" in columns

            if not (has_input and has_output):
                return {"has_data": False}

            rows = cur.execute("SELECT * FROM provider_stats").fetchall()
            today = datetime.now().date()

            stats = {
                "all_time": {"input": 0, "cached": 0, "output": 0, "ttft_sum": 0.0, "ttft_count": 0},
                "today": {"input": 0, "cached": 0, "output": 0, "ttft_sum": 0.0, "ttft_count": 0}
            }

            for row in rows:
                row_dict = dict(zip(columns, row))

                is_today = False
                if has_timestamp:
                    ts = row_dict.get("created_at") or row_dict.get("timestamp")
                    if ts:
                        try:
                            if isinstance(ts, str):
                                try:
                                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                except ValueError:
                                    dt = datetime.strptime(ts.split(".")[0], "%Y-%m-%d %H:%M:%S")
                            else:
                                dt = datetime.fromtimestamp(float(ts))
                            if dt.date() == today:
                                is_today = True
                        except Exception:
                            pass

                input_other = row_dict.get("token_input_other")
                cached = row_dict.get("token_input_cached") or row_dict.get("token_cached")
                output = row_dict.get("token_output")
                ttft = row_dict.get("time_to_first_token")

                try: input_other = int(input_other) if input_other is not None else 0
                except Exception: input_other = 0

                try: cached = int(cached) if cached is not None else 0
                except Exception: cached = 0

                try: output = int(output) if output is not None else 0
                except Exception: output = 0

                try: ttft = float(ttft) if ttft is not None else 0.0
                except Exception: ttft = 0.0

                stats["all_time"]["input"] += input_other
                stats["all_time"]["cached"] += cached
                stats["all_time"]["output"] += output
                if ttft > 0:
                    stats["all_time"]["ttft_sum"] += ttft
                    stats["all_time"]["ttft_count"] += 1

                if is_today:
                    stats["today"]["input"] += input_other
                    stats["today"]["cached"] += cached
                    stats["today"]["output"] += output
                    if ttft > 0:
                        stats["today"]["ttft_sum"] += ttft
                        stats["today"]["ttft_count"] += 1

            for key in ["all_time", "today"]:
                if stats[key]["ttft_count"] > 0:
                    stats[key]["ttft_avg"] = round(stats[key]["ttft_sum"] / stats[key]["ttft_count"], 2)
                else:
                    stats[key]["ttft_avg"] = 0.0
                del stats[key]["ttft_sum"]
                del stats[key]["ttft_count"]

            return {"has_data": True, "stats": stats}

        except Exception as e:
            logger.warning(f"[desktop_pet] provider_stats aggregation failed: {e}")
            return {"has_data": False}
        finally:
            con.close()

    def _qq_jp_dub_enabled(self) -> bool:
        return bool(self.config.get("qq_jp_dub_enabled", False))

    @staticmethod
    def _write_temp_wav(audio_b64: str) -> str | None:
        try:
            fd, path = tempfile.mkstemp(prefix="pet_dub_", suffix=".wav")
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(audio_b64))
            return path
        except Exception as e:
            logger.warning(f"[desktop_pet] write temp wav failed: {e}")
            return None

    def _rewrite_pet_identity(self, req: ProviderRequest) -> None:
        """把桌宠会话中 AstrBot 注入的用户标识（User ID/Nickname: desktop_pet）
        改写为主人身份，覆盖当前请求 extra_user_content_parts 与历史 contexts。"""
        sid = self._pet_session_id()
        name = self._master_name() or "主人"
        qq = self._master_qq() or "master"
        replacement = f"User ID: {qq}, Nickname: {name}"

        def fix(text):
            if not isinstance(text, str) or sid not in text:
                return text
            text = _IDENT_REMINDER.sub(replacement, text)
            return text.replace(sid, name)

        for part in getattr(req, "extra_user_content_parts", None) or []:
            t = getattr(part, "text", None)
            if isinstance(t, str):
                new = fix(t)
                if new != t:
                    part.text = new
        # 记忆类插件（如 LivingMemory）等按注入模式也可能落进 prompt/system_prompt，一并清洗
        if isinstance(getattr(req, "prompt", None), str):
            req.prompt = fix(req.prompt)
        if isinstance(getattr(req, "system_prompt", None), str):
            req.system_prompt = fix(req.system_prompt)
        contexts = getattr(req, "contexts", None)
        if isinstance(contexts, list):
            for msg in contexts:
                if not isinstance(msg, dict):
                    continue
                c = msg.get("content")
                if isinstance(c, str):
                    msg["content"] = fix(c)
                elif isinstance(c, list):
                    for seg in c:
                        if isinstance(seg, dict) and isinstance(seg.get("text"), str):
                            seg["text"] = fix(seg["text"])

    def _master_identity_note(self, for_pet: bool) -> str:
        name = self._master_name()
        qq = self._master_qq()
        if name and qq:
            ident = f"（{name}，QQ {qq}）"
        elif name:
            ident = f"（{name}）"
        elif qq:
            ident = f"（QQ {qq}）"
        else:
            ident = ""
        if for_pet:
            return (
                f"\n\n【身份说明】当前通过电脑桌面桌宠与你对话的用户就是你的主人本人{ident}，"
                "与在 QQ 群聊/私聊中和你说话的是同一个人，只是换到了桌面上。"
                "请像对待主人一样对待他，不要把他当成陌生用户或其他人。"
            )
        return (
            f"\n\n【身份说明】本条消息的发送者就是你的主人本人{ident}，"
            "桌面上桌宠里与你对话的用户也是他，二者是同一个人。"
        )

    @staticmethod
    def _split_jp(body: str) -> tuple[str, str]:
        """把正文按【JP】拆成 (中文正文, 日语配音稿)；无【JP】时日语稿为空。"""
        parts = _JP_TAG.split(body, maxsplit=1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return body.strip(), ""

    async def _synthesize(self, jp_text: str, overrides: dict | None = None) -> str | None:
        """调用 Style-Bert-VITS2 合成一句日语，返回 base64 编码的 wav；失败返回 None。

        overrides: 可选的临时 tts_* 参数（控制页试听时用），不写入配置。
        """
        cfg = dict(self.config)
        if overrides:
            for k in TTS_CONFIG_KEYS:
                if overrides.get(k) is not None:
                    cfg[k] = overrides[k]
        base_url = str(cfg.get("tts_base_url") or "http://172.18.0.1:5000").rstrip("/")
        text = jp_text
        if len(text) > 100:
            text = re.sub(r"([。！？!?])", r"\1\n", text)
        try:
            params = {
                "text": text,
                "model_id": int(cfg.get("tts_model_id", 0)),
                "speaker_id": int(cfg.get("tts_speaker_id", 0)),
                "style": str(cfg.get("tts_style") or "Neutral"),
                "language": "JP",
                "length": float(cfg.get("tts_length", 1.0)),
            }
        except (TypeError, ValueError):
            logger.warning("[desktop_pet] tts config invalid")
            return None
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(f"{base_url}/voice", params=params) as resp:
                    if resp.status != 200:
                        logger.warning(f"[desktop_pet] tts http {resp.status}: {jp_text[:30]}")
                        return None
                    data = await resp.read()
            return base64.b64encode(data).decode("ascii")
        except Exception as e:
            logger.warning(f"[desktop_pet] tts failed: {e} (text={jp_text[:30]})")
            return None
