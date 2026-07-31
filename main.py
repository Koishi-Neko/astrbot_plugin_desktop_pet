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

TTS：配置 tts_enabled=true 后，要求模型输出「【情绪】中文正文【JP】日语配音稿」，
壳端解析出日语句后逐句调 pet/tts，插件转发 Style-Bert-VITS2（server_fastapi）合成返回 base64 wav。
QQ 日语配音（qq_jp_dub_enabled）：on_decorating_result 把回复拆成 Plain(中文)+Record(日语 wav)。
"""

import base64
import json
import os
import re
import tempfile
import time

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, request
from starlette.responses import JSONResponse

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
    "完整格式示例：「早上好呀，主人！【JP】おはよう、ご主人様！」"
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
)

DEFAULT_PROACTIVE_ENABLED = True
DEFAULT_SCENE_ENABLED = False
DEFAULT_SCENE_INTERVAL_MIN = 30
DEFAULT_SCENE_BLOCKLIST = (
    "weixin.exe, wechat.exe, wechatappex.exe, wechatplayer.exe, "
    "qq.exe, tim.exe, wxwork.exe, dingtalk.exe, wemeetapp.exe, "
    "winword.exe, excel.exe, powerpnt.exe"
)


class DesktopPetBridge(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}

    async def initialize(self):
        self._shell_report = None  # 壳端最近一次状态上报 {"at": epoch, ...}
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
        logger.info(
            "[desktop_pet] web api registered: desktop_pet/pet/*, desktop_pet/page/*"
        )

    async def terminate(self):
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

    def _scene_payload(self) -> dict:
        return {
            "provider": self._scene_provider(),
            "blocklist": self._parse_blocklist(self._scene_blocklist_str()),
            "proactive_enabled": self._proactive_enabled(),
            "scene_enabled": self._scene_enabled(),
            "scene_interval_min": self._scene_interval_min(),
        }

    async def pet_scene_config(self):
        """壳端拉取主动对话/桌面感知配置（服务侧统一下发）。"""
        return self._scene_payload()

    async def pet_status_report(self):
        """壳端状态上报（主动对话/桌面感知监控），仅存内存，重启即清。"""
        raw = await request.body()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        events = body.get("events")
        last_scene = body.get("last_scene")
        self._shell_report = {
            "at": time.time(),
            "proactive_enabled": bool(body.get("proactive_enabled")),
            "scene_enabled": bool(body.get("scene_enabled")),
            "scene_interval_min": body.get("scene_interval_min"),
            "events": events[-20:] if isinstance(events, list) else [],
            "last_scene": last_scene if isinstance(last_scene, dict) else None,
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
            for k in TTS_CONFIG_KEYS + PAGE_CONFIG_KEYS + SCENE_CONFIG_KEYS:
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
        self._persist_config()
        return {"saved": True, "updated": updated}

    # ---------- 管道模式：给桌宠 webchat 会话追加输出格式要求 ----------

    # priority=10：先于 LivingMemory（priority 0）执行。LivingMemory 在 on_llm_request
    # 里把用户消息连同发送者信息存入自己的会话库，此时若发送者仍是 webchat 的
    # "desktop_pet"，其记忆反思（总结 prompt 强制使用消息前缀昵称）会把 "desktop_pet"
    # 当主人昵称写进长期记忆——该问题曾两次修复（身份改写/数据清洗）均因晚于存储而复发。
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

    # ---------- 内部逻辑 ----------

    def _tts_enabled(self) -> bool:
        return bool(self.config.get("tts_enabled", False))

    def _pet_session_id(self) -> str:
        return str(self.config.get("pet_session_id") or "desktop_pet").strip() or "desktop_pet"

    def _master_name(self) -> str:
        return str(self.config.get("master_name") or "").strip()

    def _master_qq(self) -> str:
        return str(self.config.get("master_qq") or "").strip()

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
        try:
            params = {
                "text": jp_text,
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
