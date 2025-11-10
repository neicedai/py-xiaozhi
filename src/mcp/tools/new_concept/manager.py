"""Manager that exposes New Concept English lessons as MCP tools."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from src.mcp.mcp_server import Property, PropertyList, PropertyType
from src.utils.logging_config import get_logger

from .data_loader import get_repository
from .deepseek_client import DeepSeekClient
from .prompt_builder import build_prompts

logger = get_logger(__name__)


class NewConceptToolsManager:
    """Register MCP tools for New Concept English teaching workflows."""

    def __init__(self) -> None:
        self._repository = get_repository()
        self._client: Optional[DeepSeekClient] = None

    # ------------------------------------------------------------------
    def init_tools(
        self,
        add_tool: Callable[[Any], None],
        property_list_cls: type[PropertyList],
        property_cls: type[Property],
        property_type: type[PropertyType],
    ) -> None:
        """Register tools with the MCP server."""

        logger.info("[NewConcept] Registering lesson tools")

        list_properties = property_list_cls(
            [
                property_cls("book", property_type.STRING, default_value=""),
                property_cls(
                    "limit",
                    property_type.INTEGER,
                    default_value=0,
                    min_value=0,
                    max_value=200,
                ),
            ]
        )

        prompt_properties = property_list_cls(
            [
                property_cls("book", property_type.STRING),
                property_cls("lesson", property_type.STRING),
                property_cls("language", property_type.STRING, default_value="zh"),
                property_cls("student_name", property_type.STRING, default_value=""),
                property_cls("learning_goal", property_type.STRING, default_value=""),
                property_cls("focus", property_type.STRING, default_value=""),
                property_cls("extra_notes", property_type.STRING, default_value=""),
                property_cls(
                    "student_age",
                    property_type.INTEGER,
                    default_value=0,
                    min_value=3,
                    max_value=18,
                ),
            ]
        )

        teach_properties = property_list_cls(
            [
                property_cls("book", property_type.STRING),
                property_cls("lesson", property_type.STRING),
                property_cls("language", property_type.STRING, default_value="zh"),
                property_cls("student_name", property_type.STRING, default_value=""),
                property_cls("learning_goal", property_type.STRING, default_value=""),
                property_cls("focus", property_type.STRING, default_value=""),
                property_cls("extra_notes", property_type.STRING, default_value=""),
                property_cls(
                    "student_age",
                    property_type.INTEGER,
                    default_value=0,
                    min_value=3,
                    max_value=18,
                ),
                property_cls(
                    "temperature",
                    property_type.INTEGER,
                    default_value=70,
                    min_value=0,
                    max_value=100,
                ),
                property_cls("call_api", property_type.BOOLEAN, default_value=True),
            ]
        )

        add_tool(
            (
                "education.new_concept.list_lessons",
                "List available New Concept English lessons with optional book filter.",
                list_properties,
                self._list_lessons,
            )
        )

        add_tool(
            (
                "education.new_concept.generate_prompt",
                "Create DeepSeek-ready teaching prompts for a specific New Concept lesson.",
                prompt_properties,
                self._generate_prompt,
            )
        )

        add_tool(
            (
                "education.new_concept.teach",
                "Generate prompts and optionally call DeepSeek to deliver the lesson.",
                teach_properties,
                self._teach_with_deepseek,
            )
        )

        logger.info("[NewConcept] Lesson tools registered")

    # ------------------------------------------------------------------
    def _list_lessons(self, params: Dict[str, Any]) -> str:
        book = params.get("book") or ""
        limit = params.get("limit", 0)

        lessons = self._repository.list_lessons(book or None)
        if limit and limit > 0:
            lessons = lessons[:limit]

        result = {
            "success": True,
            "book": book,
            "total": len(lessons),
            "available_books": self._repository.books(),
            "lessons": lessons,
        }
        return json.dumps(result, ensure_ascii=False)

    def _generate_prompt(self, params: Dict[str, Any]) -> str:
        prepared = self._prepare_prompt_data(params, include_payload=True)
        return json.dumps(prepared, ensure_ascii=False)

    async def _teach_with_deepseek(self, params: Dict[str, Any]) -> str:
        temperature_value = params.get("temperature", 70)
        temperature = max(0, min(int(temperature_value), 100)) / 100
        prepared = self._prepare_prompt_data(
            params,
            include_payload=True,
            override_temperature=temperature,
        )

        if not prepared.get("success"):
            return json.dumps(prepared, ensure_ascii=False)

        call_api = params.get("call_api", True)
        if not call_api:
            prepared["message"] = "DeepSeek payload generated. API call skipped as requested."
            return json.dumps(prepared, ensure_ascii=False)

        client = self._get_client()
        try:
            response = await client.chat(prepared["deepseek_request"])
            prepared["deepseek_response"] = response
        except Exception as exc:  # pragma: no cover - network/runtime errors
            prepared["success"] = False
            prepared["message"] = f"DeepSeek API 调用失败: {exc}"
            logger.error("[NewConcept] DeepSeek call failed: %s", exc, exc_info=True)
        return json.dumps(prepared, ensure_ascii=False)

    # ------------------------------------------------------------------
    def _prepare_prompt_data(
        self,
        params: Dict[str, Any],
        *,
        include_payload: bool,
        override_temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        book = params.get("book")
        lesson_identifier = params.get("lesson")

        if not book or not lesson_identifier:
            return {
                "success": False,
                "message": "Both 'book' and 'lesson' parameters are required.",
            }

        record = self._repository.find_lesson(book, lesson_identifier)
        if not record:
            return {
                "success": False,
                "message": f"Lesson not found for book '{book}' and lesson '{lesson_identifier}'.",
            }

        language = params.get("language", "zh")
        use_chinese = _should_use_chinese(language)

        student_age = params.get("student_age") or 0
        if isinstance(student_age, (int, float)) and student_age <= 0:
            student_age = None

        prompts = build_prompts(
            record.raw,
            language=language,
            student_name=_normalize_optional(params.get("student_name")),
            student_age=int(student_age) if student_age else None,
            learning_goal=_normalize_optional(params.get("learning_goal")),
            focus=_normalize_optional(params.get("focus")),
            extra_notes=_normalize_optional(params.get("extra_notes")),
        )

        extended_user_prompt = _append_followups(
            prompts["user_prompt"],
            prompts.get("check_in_questions", []),
            prompts.get("home_extension", []),
            use_chinese=use_chinese,
        )

        response: Dict[str, Any] = {
            "success": True,
            "language": language,
            "lesson": {
                "book": record.book,
                "lesson_number": record.lesson_number,
                "lesson_id": record.lesson_id,
                "title": record.title,
            },
            "lesson_material": record.raw,
            "prompts": {
                **prompts,
                "user_prompt_full": extended_user_prompt,
            },
        }

        if include_payload:
            client = self._get_client()
            payload = client.build_payload(
                system_prompt=prompts["system_prompt"],
                user_prompt=extended_user_prompt,
                temperature=override_temperature,
            )
            response["deepseek_request"] = payload
        return response

    def _get_client(self) -> DeepSeekClient:
        if self._client is None:
            self._client = DeepSeekClient()
        return self._client


def get_new_concept_manager() -> NewConceptToolsManager:
    return NewConceptToolsManager()


def _normalize_optional(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = str(value).strip()
    return value or None


def _should_use_chinese(language: str) -> bool:
    if not language:
        return True
    lower = language.lower()
    return lower.startswith("zh") or "chinese" in lower


def _append_followups(
    user_prompt: str,
    questions: Any,
    home_extension: Any,
    *,
    use_chinese: bool,
) -> str:
    text = user_prompt
    if questions:
        label = "课堂互动提问" if use_chinese else "Suggested check-in questions"
        question_lines = "\n".join(f"- {q}" for q in questions)
        text += f"\n\n{label}:\n{question_lines}"
    if home_extension:
        label = "课后延伸建议" if use_chinese else "Home extension ideas"
        home_lines = "\n".join(f"- {item}" for item in home_extension)
        text += f"\n\n{label}:\n{home_lines}"
    return text
