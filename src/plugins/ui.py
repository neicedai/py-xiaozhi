import json
from typing import Any, Optional

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
                            message = "[新概念课程] 教学工具尚未注册。"
                        else:
                            tool_response = await tool.call(
                                {
                                    "book": book,
                                    "lesson": lesson_identifier,
                                    "language": "zh",
                                    "call_api": False,
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
                            if not lesson_data.get("success"):
                                message = (
                                    lesson_data.get("message")
                                    or "[新概念课程] 准备课程失败。"
                                )
                            else:
                                prepared_lesson = lesson_data.get("lesson", {})
                                summary = (
                                    lesson_data.get("lesson_material", {}).get("summary")
                                    or lesson_entry.get("summary")
                                    or ""
                                )
                                prompts = lesson_data.get("prompts", {})
                                user_prompt = (
                                    prompts.get("user_prompt_full")
                                    or prompts.get("user_prompt")
                                )

                                lines = ["[新概念课程] 已准备好第一节课程。"]
                                lines.append(f"教材：{prepared_lesson.get('book', book)}")
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
                                if user_prompt:
                                    lines.append("")
                                    lines.append("教学指令：")
                                    lines.append(user_prompt)

                                message = "\n".join(lines)
        except Exception as exc:  # pragma: no cover - 主要用于界面反馈
            message = f"[新概念课程] 自动准备课程失败：{exc}"
            logger.error(message, exc_info=True)

        if self.display:
            await self.display.update_text(message)

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
