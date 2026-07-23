"""astrbot_plugin_desktop_pet

为自研桌面桌宠壳（pet_shell/）提供对话接口的 AstrBot 插件。

路由（挂在 dashboard 插件扩展路径下，需带 plugin scope 的 API Key 鉴权）：
- POST /api/v1/plugins/extensions/desktop_pet/pet/chat
    请求体: {"message": "...", "history": [{"role": "user"|"assistant", "content": "..."}]}
    响应: text/event-stream，帧序列：
      data: {"type": "emotion", "label": "高兴"}            # 首帧，情绪标签
      data: {"type": "delta", "text": "……"}                 # 正文分句，若干帧
      data: {"type": "audio", "format": "wav", "data": "…"}  # （可选）TTS 音频帧，base64，与对应句的 delta 帧相邻
      data: {"type": "done"}                                # 结束
      （异常时先补一帧 {"type": "error", "message": "..."}）
- GET  /api/v1/plugins/extensions/desktop_pet/pet/health
    探活，返回 JSON。

TTS：配置 tts_enabled=true 后，要求模型输出「【情绪】中文正文【JP】日语配音稿」，
插件将日语配音稿分句发给 Style-Bert-VITS2（server_fastapi）合成语音，
每句音频作为 audio 帧随 SSE 流返回；TTS 失败仅记日志，不影响文字对话。
"""

import asyncio
import base64
import json
import re
from collections.abc import AsyncGenerator

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import request
from starlette.responses import JSONResponse, StreamingResponse

# 情绪集合需与 pet_shell/assets/ 下的立绘文件名一一对应
EMOTIONS = ["平静", "高兴", "生气", "害羞", "惊讶", "难过", "疑惑", "调皮"]

DEFAULT_PERSONA = (
    "你是住在用户电脑桌面上的小桌宠，性格活泼粘人，把用户当作最重要的人。"
)

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

_EMOTION_TAG = re.compile(r"^\s*【([^】]{1,8})】\s*")
_JP_TAG = re.compile(r"\s*【\s*JP\s*】\s*", re.IGNORECASE)
_SENTENCES = re.compile(r"[^。！？!?；;\n]+[。！？!?；;\n]*")

ERROR_FALLBACK_TEXT = "呜……好像连不上大脑了，等下再找我聊吧。"


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class DesktopPetBridge(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}

    async def initialize(self):
        self.context.register_web_api(
            "desktop_pet/pet/chat",
            self.chat,
            ["POST"],
            "桌宠对话接口（SSE 流式，旧版直连模式，保留作回退）",
        )
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
        logger.info(
            "[desktop_pet] web api registered: desktop_pet/pet/chat, health, tts, personas"
        )

    async def terminate(self):
        logger.info("[desktop_pet] plugin terminated")

    # ---------- 路由处理 ----------

    async def health(self):
        prov = self.context.get_using_provider()
        return {
            "status": "ok",
            "plugin": "astrbot_plugin_desktop_pet",
            "provider_id": (self.config.get("provider_id") or "").strip() or None,
            "default_provider_available": prov is not None,
            "emotions": EMOTIONS,
            "tts_enabled": self._tts_enabled(),
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

    # ---------- 管道模式：给桌宠 webchat 会话追加输出格式要求 ----------

    @filter.on_llm_request()
    async def inject_pet_format(self, event: AstrMessageEvent, req: ProviderRequest):
        umo = event.unified_msg_origin or ""
        sid = self._pet_session_id()
        # 桌宠会话 umo 形如 webchat:FriendMessage:webchat!{username}!{conversation_id}
        if not (umo.startswith("webchat:") and umo.endswith(f"!{sid}")):
            return
        tpl = EMOTION_INSTRUCTION_TTS if self._tts_enabled() else EMOTION_INSTRUCTION
        req.system_prompt = (req.system_prompt or "") + tpl.format(
            emotions="、".join(EMOTIONS)
        )
        if self._tts_enabled():
            # 长人格 prompt 会稀释 system 侧格式要求，在用户消息末尾再提醒一次关键格式
            reminder = (
                "\n（格式提醒：本次回复必须包含【情绪】中文正文和【JP】日语配音稿三部分，"
                "【JP】为纯日语，缺一不可。）"
            )
            req.prompt = (req.prompt or "") + reminder

    async def chat(self):
        raw = await request.body()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        message = str(body.get("message") or "").strip()
        history = body.get("history") or []
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)
        if not isinstance(history, list):
            history = []

        return StreamingResponse(
            self._stream_reply(message, history),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------- 内部逻辑 ----------

    def _tts_enabled(self) -> bool:
        return bool(self.config.get("tts_enabled", False))

    def _pet_session_id(self) -> str:
        return str(self.config.get("pet_session_id") or "desktop_pet").strip() or "desktop_pet"

    def _build_system_prompt(self) -> str:
        persona = str(self.config.get("persona") or DEFAULT_PERSONA).strip()
        tpl = EMOTION_INSTRUCTION_TTS if self._tts_enabled() else EMOTION_INSTRUCTION
        return persona + tpl.format(emotions="、".join(EMOTIONS))

    def _split_emotion(self, text: str) -> tuple[str, str]:
        """从回复开头解析【情绪】标签，返回 (情绪, 正文)。"""
        default = str(self.config.get("default_emotion") or "平静").strip()
        if default not in EMOTIONS:
            default = "平静"
        m = _EMOTION_TAG.match(text)
        if not m:
            return default, text
        label = m.group(1).strip()
        if label not in EMOTIONS:
            label = default
        return label, text[m.end():].strip()

    @staticmethod
    def _split_jp(body: str) -> tuple[str, str]:
        """把正文按【JP】拆成 (中文正文, 日语配音稿)；无【JP】时日语稿为空。"""
        parts = _JP_TAG.split(body, maxsplit=1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return body.strip(), ""

    async def _synthesize(self, jp_text: str) -> str | None:
        """调用 Style-Bert-VITS2 合成一句日语，返回 base64 编码的 wav；失败返回 None。"""
        base_url = str(self.config.get("tts_base_url") or "http://172.18.0.1:5000").rstrip("/")
        params = {
            "text": jp_text,
            "model_id": int(self.config.get("tts_model_id", 0)),
            "speaker_id": int(self.config.get("tts_speaker_id", 0)),
            "style": str(self.config.get("tts_style") or "Neutral"),
            "language": "JP",
            "length": float(self.config.get("tts_length", 1.0)),
        }
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

    async def _call_llm(self, message: str, contexts: list[dict]) -> str:
        system_prompt = self._build_system_prompt()
        provider_id = (self.config.get("provider_id") or "").strip()
        if provider_id:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=message,
                system_prompt=system_prompt,
                contexts=contexts or None,
            )
        else:
            prov = self.context.get_using_provider()
            if prov is None:
                raise RuntimeError("AstrBot 未配置可用的默认对话模型（LLM Provider）")
            resp = await prov.text_chat(
                prompt=message,
                system_prompt=system_prompt,
                contexts=contexts or None,
            )
        return (resp.completion_text or "").strip()

    async def _stream_reply(
        self, message: str, history: list
    ) -> AsyncGenerator[str, None]:
        # 清洗并裁剪历史
        max_turns = self.config.get("history_turns", 10)
        try:
            max_turns = max(0, int(max_turns))
        except (TypeError, ValueError):
            max_turns = 10
        contexts = [
            {"role": h.get("role"), "content": str(h.get("content"))}
            for h in history
            if isinstance(h, dict)
            and h.get("role") in ("user", "assistant")
            and h.get("content")
        ]
        if max_turns:
            contexts = contexts[-max_turns * 2 :]
        logger.info(f"[desktop_pet] chat: message_len={len(message)} contexts={len(contexts)}")

        try:
            text = await self._call_llm(message, contexts)
            if not text:
                raise RuntimeError("LLM 返回了空内容")
            emotion, body = self._split_emotion(text)
            yield _sse({"type": "emotion", "label": emotion})
            zh_body, jp_body = self._split_jp(body) if self._tts_enabled() else (body, "")
            zh_sents = [s.strip() for s in _SENTENCES.findall(zh_body) if s.strip()]
            jp_sents = [s.strip() for s in _SENTENCES.findall(jp_body) if s.strip()]
            if self._tts_enabled() and not jp_sents:
                logger.warning("[desktop_pet] tts enabled but no JP part in reply")
            for i, seg in enumerate(zh_sents):
                yield _sse({"type": "delta", "text": seg})
                if i < len(jp_sents):
                    audio = await self._synthesize(jp_sents[i])
                    if audio:
                        yield _sse({"type": "audio", "format": "wav", "data": audio})
                await asyncio.sleep(0.05)
            # 日语句数多于中文句时，多余的配音句补在最后
            for jp_seg in jp_sents[len(zh_sents):]:
                audio = await self._synthesize(jp_seg)
                if audio:
                    yield _sse({"type": "audio", "format": "wav", "data": audio})
        except Exception as e:
            logger.error(f"[desktop_pet] chat failed: {e}")
            yield _sse({"type": "emotion", "label": "难过"})
            yield _sse({"type": "delta", "text": ERROR_FALLBACK_TEXT})
            yield _sse({"type": "error", "message": str(e)})
        yield _sse({"type": "done"})
