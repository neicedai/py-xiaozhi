import json
from typing import Any, Dict, Optional

from src.constants.constants import AbortReason, DeviceState
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class UIPlugin(Plugin):
    """UI 插件 - 管理 CLI/GUI 显示"""

    name = "ui"

    # 设备状态文本映射
    STATE_TEXT_MAP = {
        DeviceState.IDLE: "待命",
        DeviceState.LISTENING: "聆听中...",
        DeviceState.SPEAKING: "说话中...",
    }

    def __init__(self, mode: Optional[str] = None) -> None:
        super().__init__()
        self.app = None
        self.mode = (mode or "cli").lower()
        self.display = None
        self._is_gui = False
        self.is_first = True

    async def setup(self, app: Any) -> None:
        """
        初始化 UI 插件.
        """
        self.app = app

        # 创建对应的 display 实例
        self.display = self._create_display()

        # 禁用应用内控制台输入
        if hasattr(app, "use_console_input"):
            app.use_console_input = False

    def _create_display(self):
        """
        根据模式创建 display 实例.
        """
        if self.mode == "gui":
            from src.display.gui_display import GuiDisplay

            self._is_gui = True
            return GuiDisplay()
        if self.mode == "web":
            from src.display.web_display import WebDisplay

            self._is_gui = False
            return WebDisplay()
        else:
            from src.display.cli_display import CliDisplay

            self._is_gui = False
            return CliDisplay()

    async def start(self) -> None:
        """
        启动 UI 显示.
        """
        if not self.display:
            return

        # 绑定回调
        await self._setup_callbacks()

        # 启动显示
        self.app.spawn(self.display.start(), name=f"ui:{self.mode}:start")

    async def _setup_callbacks(self) -> None:
        """
        设置 display 回调.
        """
        if self._is_gui:
            # GUI 需要调度到异步任务
            callbacks = {
                "press_callback": self._wrap_callback(self._press),
                "release_callback": self._wrap_callback(self._release),
                "auto_callback": self._wrap_callback(self._auto_toggle),
                "abort_callback": self._wrap_callback(self._abort),
                "send_text_callback": self._send_text,
            }
        else:
            # CLI 直接传递协程函数
            callbacks = {
                "auto_callback": self._auto_toggle,
                "abort_callback": self._abort,
                "send_text_callback": self._send_text,
            }

        await self.display.set_callbacks(**callbacks)

    def _wrap_callback(self, coro_func):
        """
        包装协程函数为可调度的 lambda.
        """
        return lambda: self.app.spawn(coro_func(), name="ui:callback")

    async def on_incoming_json(self, message: Any) -> None:
        """
        处理传入的 JSON 消息.
        """
        if not self.display or not isinstance(message, dict):
            return

        msg_type = message.get("type")

        # tts/stt 都更新文本
        if msg_type in ("tts", "stt"):
            if text := message.get("text"):
                await self.display.update_text(text)

        # llm 更新表情
        elif msg_type == "llm":
            if emotion := message.get("emotion"):
                await self.display.update_emotion(emotion)

    async def on_device_state_changed(self, state: Any) -> None:
        """
        设备状态变化处理.
        """
        if not self.display:
            return

        # 跳过首次调用
        if self.is_first:
            self.is_first = False
            return

        # 更新表情和状态
        await self.display.update_emotion("neutral")
        if status_text := self.STATE_TEXT_MAP.get(state):
            await self.display.update_status(status_text, True)

    async def shutdown(self) -> None:
        """
        清理 UI 资源，关闭窗口.
        """
        if self.display:
            await self.display.close()
            self.display = None

    # ===== 回调函数 =====

    async def _send_text(self, text: str):
        """
        发送文本到服务端.
        """
        if await self._handle_local_command(text):
            return

        if self.app.device_state == DeviceState.SPEAKING:
            audio_plugin = self.app.plugins.get_plugin("audio")
            if audio_plugin:
                await audio_plugin.codec.clear_audio_queue()
            await self.app.abort_speaking(None)
        if await self.app.connect_protocol():
            await self.app.protocol.send_wake_word_detected(text)

    async def _handle_local_command(self, text: str) -> bool:
        """处理无需经过协议的本地指令."""

        normalized = text.strip().lower()
        if not normalized:
            return False

        diagnostics_triggers = {
            "测试课程库",
            "课程库测试",
            "mcp测试",
            "mcp test",
            "#mcp-test",
        }
        if normalized in diagnostics_triggers:
            await self._run_new_concept_diagnostics()
            return True

        start_course_triggers = {
            "开始课程",
            "课程开始",
            "开始上课",
            "start course",
            "start lesson",
            "#start-lesson",
        }
        if normalized in start_course_triggers:
            await self._start_new_concept_course()
            return True

        return False

    async def _run_new_concept_diagnostics(self) -> None:
        """运行新概念课程库的快速诊断，并在界面显示结果."""

        try:
            from src.mcp.tools.new_concept.data_loader import LessonRepository

            repository = LessonRepository()
            repository.reload()
            lessons = repository.list_lessons()
            total_lessons = len(lessons)
            books = repository.books()
            lines = ["[MCP测试] 课程库读取成功", f"总课程数：{total_lessons}"]
            for book in books:
                count = len(repository.list_lessons(book=book))
                lines.append(f"· {book}: {count} 课")
            message = "\n".join(lines)
            logger.info(message)
        except Exception as exc:  # pragma: no cover - 诊断信息主要用于界面反馈
            message = f"[MCP测试] 读取课程库失败：{exc}"
            logger.error(message, exc_info=True)

        if self.display:
            await self.display.update_text(message)

    async def _start_new_concept_course(self) -> None:
        """自动准备并展示第一节新概念课程的教学提示."""

        message = "[新概念课程] 暂未准备任何课程。"

        try:
            from src.mcp.mcp_server import McpServer
            from src.mcp.tools.new_concept.data_loader import LessonRepository

            repository = LessonRepository()
            repository.reload()

            books = repository.books()
            if not books:
                message = "[新概念课程] 未找到可用的教材数据。"
            else:
                book = books[0]
                lessons = repository.list_lessons(book=book)
                if not lessons:
                    message = f"[新概念课程] {book} 暂无课程内容。"
                else:
                    lesson_entry = lessons[0]
                    if lesson_entry.get("lesson_number") is not None:
                        lesson_identifier = str(lesson_entry["lesson_number"])
                    elif lesson_entry.get("lesson_id"):
                        lesson_identifier = str(lesson_entry["lesson_id"])
                    else:
                        message = (
                            "[新概念课程] 找到的课程缺少课次信息，无法自动开始。"
                        )
                        lesson_identifier = ""

                    if lesson_identifier:
                        server = McpServer.get_instance()
                        tool = next(
                            (t for t in server.tools if t.name == "education.new_concept.teach"),
                            None,
                        )

                        if not tool:
                            logger.warning(
                                "[NewConcept] teach tool missing - attempting MCP tool reload"
                            )
                            try:
                                server.add_common_tools()
                            except Exception as exc:  # pragma: no cover - defensive
                                logger.error(
                                    "[NewConcept] Failed to reload MCP tools: %s", exc,
                                    exc_info=True,
                                )
                            tool = next(
                                (
                                    t
                                    for t in server.tools
                                    if t.name == "education.new_concept.teach"
                                ),
                                None,
                            )

                        if not tool:
                            message = "[新概念课程] 教学工具尚未注册。"
                        else:
                            try:
                                from src.mcp.tools.new_concept.deepseek_client import (
                                    get_deepseek_client,
                                )

                                deepseek_client = get_deepseek_client()
                                call_api = bool(
                                    deepseek_client.has_service()
                                    or deepseek_client.has_direct_credentials()
                                )
                            except Exception as exc:  # pragma: no cover - defensive
                                logger.warning(
                                    "[NewConcept] Failed to check DeepSeek availability: %s",
                                    exc,
                                )
                                call_api = False

                            tool_response = await tool.call(
                                {
                                    "book": book,
                                    "lesson": lesson_identifier,
                                    "language": "zh",
                                    "call_api": call_api,
                                }
                            )
                            payload = json.loads(tool_response)
                            if payload.get("isError"):
                                error_text = next(
                                    (
                                        item.get("text", "")
                                        for item in payload.get("content", [])
                                        if item.get("type") == "text"
                                    ),
                                    "",
                                )
                                raise RuntimeError(
                                    error_text or "调用课程工具失败"
                                )

                            data_text = next(
                                (
                                    item.get("text", "")
                                    for item in payload.get("content", [])
                                    if item.get("type") == "text"
                                ),
                                "",
                            )
                            if not data_text:
                                raise ValueError("课程工具返回内容为空")

                            lesson_data = json.loads(data_text)
                            call_api_requested = lesson_data.get("call_api", call_api)
                            prepared_lesson = lesson_data.get("lesson", {})
                            material = lesson_data.get("lesson_material", {})
                            summary = (
                                material.get("summary")
                                or lesson_entry.get("summary")
                                or ""
                            )
                            vocabulary = self._format_brief_list(
                                material.get("vocabulary")
                            )
                            key_sentences = self._format_brief_list(
                                material.get("key_sentences")
                            )
                            model_output = self._extract_model_output(
                                lesson_data.get("deepseek_response")
                            )

                            lesson_ready = lesson_data.get("success") or (
                                call_api_requested
                                and bool(prepared_lesson)
                            )

                            if not lesson_ready:
                                message = (
                                    lesson_data.get("message")
                                    or "[新概念课程] 准备课程失败。"
                                )
                            else:
                                lines = []
                                if lesson_data.get("success"):
                                    lines.append("[新概念课程] 已准备好第一节课程。")
                                else:
                                    fallback_message = (
                                        lesson_data.get("message")
                                        or "DeepSeek API 调用失败，已显示本地课程提示。"
                                    )
                                    lines.append("[新概念课程] 已准备好第一节课程（离线模式）。")
                                    lines.append(f"提示：{fallback_message}")

                                lines.append(
                                    f"教材：{prepared_lesson.get('book', book)}"
                                )
                                if prepared_lesson.get("lesson_number") is not None:
                                    lines.append(
                                        f"课次：Lesson {prepared_lesson['lesson_number']}"
                                    )
                                elif prepared_lesson.get("lesson_id"):
                                    lines.append(
                                        f"课次：{prepared_lesson['lesson_id']}"
                                    )
                                if prepared_lesson.get("title"):
                                    lines.append(
                                        f"标题：{prepared_lesson['title']}"
                                    )
                                if summary:
                                    lines.append(f"概要：{summary}")
                                if vocabulary:
                                    lines.append(f"关键词汇：{vocabulary}")
                                if key_sentences:
                                    lines.append(f"重点句型：{key_sentences}")

                                if model_output:
                                    lines.append("")
                                    lines.append("AI 课堂示范：")
                                    lines.append(model_output)
                                elif lesson_data.get("message") and lesson_data.get("success"):
                                    lines.append("")
                                    lines.append(lesson_data.get("message"))

                                prompt_sent = False
                                if not model_output:
                                    prompt_text = self._build_model_prompt(
                                        prepared_lesson,
                                        material,
                                        lesson_data.get("prompts", {}),
                                        summary=summary,
                                        vocabulary=vocabulary,
                                        key_sentences=key_sentences,
                                    )
                                    if prompt_text:
                                        prompt_sent = await self._dispatch_lesson_prompt(
                                            prompt_text
                                        )
                                if prompt_sent:
                                    lines.append("")
                                    lines.append(
                                        "[新概念课程] 已向教学模型发送课程资料，课堂即将开始。"
                                    )

                                message = "\n".join(lines)
        except Exception as exc:  # pragma: no cover - 主要用于界面反馈
            message = f"[新概念课程] 自动准备课程失败：{exc}"
            logger.error(message, exc_info=True)

        if self.display:
            await self.display.update_text(message)

    @staticmethod
    def _format_brief_list(value: Any, limit: int = 3) -> str:
        """Convert lesson material entries to a short, human-friendly summary."""

        if not value:
            return ""

        items: list[str] = []
        candidates: list[Any]
        if isinstance(value, dict):
            candidates = [value]
        elif isinstance(value, (list, tuple, set)):
            candidates = list(value)
        else:
            candidates = [value]

        for entry in candidates:
            text = ""
            if isinstance(entry, dict):
                text = (
                    str(
                        entry.get("phrase")
                        or entry.get("word")
                        or entry.get("term")
                        or entry.get("text")
                        or entry.get("sentence")
                        or next(iter(entry.values()), "")
                    )
                    if entry
                    else ""
                )
            else:
                text = str(entry)

            text = text.strip()
            if text:
                items.append(text)
            if len(items) >= limit:
                break

        return "、".join(items[:limit]) if items else ""

    def _build_model_prompt(
        self,
        prepared_lesson: Dict[str, Any],
        lesson_material: Dict[str, Any],
        prompts: Dict[str, Any],
        *,
        summary: str,
        vocabulary: str,
        key_sentences: str,
    ) -> str:
        """Compose a detailed instruction message for the conversational model."""

        book = prepared_lesson.get("book") or lesson_material.get("book") or ""
        lesson_number = (
            prepared_lesson.get("lesson_number")
            or prepared_lesson.get("lesson_id")
            or lesson_material.get("lesson_id")
        )
        title = prepared_lesson.get("title") or lesson_material.get("title") or ""

        header_lines = [
            "课程开始。请立即以耐心、鼓励式的少儿英语老师身份与学生互动上课。",
        ]
        info_parts = []
        if book:
            info_parts.append(str(book))
        if lesson_number:
            info_parts.append(f"Lesson {lesson_number}")
        if title:
            info_parts.append(str(title))
        if info_parts:
            header_lines.append("课程信息：" + " - ".join(info_parts))

        if summary:
            header_lines.append(f"课程概要：{summary}")
        if vocabulary:
            header_lines.append(f"关键词汇：{vocabulary}")
        if key_sentences:
            header_lines.append(f"重点句型：{key_sentences}")

        system_prompt = (prompts.get("system_prompt") or "").strip()
        user_prompt = (
            prompts.get("user_prompt_full")
            or prompts.get("user_prompt")
            or ""
        ).strip()

        body_lines = [
            "请遵循下列教学要求与流程，主动提问并等待学生回应，如无回应请给出示范。",
        ]
        if system_prompt:
            body_lines.append("【教学风格要求】")
            body_lines.append(system_prompt)
        if user_prompt:
            body_lines.append("")
            body_lines.append("【课堂流程提示】")
            body_lines.append(user_prompt)

        extras = []
        activities = lesson_material.get("activities")
        if activities and not user_prompt:
            extras.append(
                "课堂活动建议：" + "；".join(str(item) for item in activities if item)
            )
        parent_extension = lesson_material.get("parent_extension")
        if parent_extension:
            extras.append(f"课后建议：{parent_extension}")
        teaching_tips = lesson_material.get("teaching_tips")
        if teaching_tips:
            extras.append(f"授课提醒：{teaching_tips}")
        if extras:
            body_lines.append("")
            body_lines.extend(extras)

        message = "\n".join(header_lines + [""] + body_lines)
        return message.strip()

    async def _dispatch_lesson_prompt(self, prompt_text: str) -> bool:
        """Send the prepared lesson prompt to the active conversational protocol."""

        if not prompt_text.strip():
            return False
        app = self.app
        if not app or not getattr(app, "protocol", None):
            return False

        try:
            connected = await app.connect_protocol()
        except Exception as exc:  # pragma: no cover - safeguard
            logger.error("[NewConcept] Failed to connect protocol: %s", exc, exc_info=True)
            return False

        if not connected:
            return False

        try:
            await app.protocol.send_wake_word_detected(prompt_text)
            return True
        except Exception as exc:  # pragma: no cover - communication failure
            logger.error("[NewConcept] Failed to dispatch lesson prompt: %s", exc, exc_info=True)
            return False

    @staticmethod
    def _extract_model_output(payload: Any) -> str:
        """Extract a readable lesson script from DeepSeek or service responses."""

        if not payload:
            return ""

        if isinstance(payload, str):
            return payload.strip()

        if isinstance(payload, dict):
            choices = payload.get("choices")
            fragments: list[str] = []
            if isinstance(choices, list):
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    message = choice.get("message")
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, str) and content.strip():
                            fragments.append(content.strip())
                    text = choice.get("text")
                    if isinstance(text, str) and text.strip():
                        fragments.append(text.strip())
                if fragments:
                    # Preserve order while removing duplicates
                    seen = set()
                    ordered = []
                    for fragment in fragments:
                        if fragment in seen:
                            continue
                        seen.add(fragment)
                        ordered.append(fragment)
                    return "\n\n".join(ordered)

            raw_text = payload.get("raw")
            if isinstance(raw_text, str) and raw_text.strip():
                return raw_text.strip()

            message_text = payload.get("message")
            if isinstance(message_text, str) and message_text.strip():
                return message_text.strip()

            data_field = payload.get("data")
            if isinstance(data_field, (dict, list, str)):
                extracted = UIPlugin._extract_model_output(data_field)
                if extracted:
                    return extracted

        if isinstance(payload, list):
            fragments = [UIPlugin._extract_model_output(item) for item in payload]
            fragments = [fragment for fragment in fragments if fragment]
            if fragments:
                return "\n\n".join(fragments)

        return ""

    async def _press(self):
        """
        手动模式：按下开始录音.
        """
        await self.app.start_listening_manual()

    async def _release(self):
        """
        手动模式：释放停止录音.
        """
        await self.app.stop_listening_manual()

    async def _auto_toggle(self):
        """
        自动模式切换.
        """
        await self.app.start_auto_conversation()

    async def _abort(self):
        """
        中断对话.
        """
        await self.app.abort_speaking(AbortReason.USER_INTERRUPTION)
