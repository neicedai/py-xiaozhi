"""Prompt construction helpers for New Concept lessons."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def build_prompts(
    lesson: Dict[str, Any],
    *,
    language: str = "zh",
    student_name: Optional[str] = None,
    student_age: Optional[int] = None,
    learning_goal: Optional[str] = None,
    focus: Optional[str] = None,
    extra_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Create system & user prompts for DeepSeek based on the lesson content."""

    use_chinese = _use_chinese(language)
    profile_summary = _build_student_profile(
        student_name=student_name,
        student_age=student_age,
        learning_goal=learning_goal,
        focus=focus,
        extra_notes=extra_notes,
        use_chinese=use_chinese,
    )

    system_prompt = _build_system_prompt(use_chinese)
    user_prompt = _build_user_prompt(
        lesson=lesson,
        profile_summary=profile_summary,
        use_chinese=use_chinese,
    )

    suggested_questions = _build_checkin_questions(lesson, use_chinese)
    at_home_tasks = _build_home_tasks(lesson, use_chinese)

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "check_in_questions": suggested_questions,
        "home_extension": at_home_tasks,
    }


# ---------------------------------------------------------------------------
# prompt fragments
# ---------------------------------------------------------------------------


def _use_chinese(language: Optional[str]) -> bool:
    if not language:
        return True
    value = language.lower()
    return value.startswith("zh") or "chinese" in value


def _build_student_profile(
    *,
    student_name: Optional[str],
    student_age: Optional[int],
    learning_goal: Optional[str],
    focus: Optional[str],
    extra_notes: Optional[str],
    use_chinese: bool,
) -> str:
    parts: List[str] = []
    if student_name:
        parts.append(("学生昵称" if use_chinese else "Student nickname") + f": {student_name}")
    if student_age:
        age_label = "年龄" if use_chinese else "Age"
        parts.append(f"{age_label}: {student_age}")
    if learning_goal:
        goal_label = "学习目标" if use_chinese else "Learning goal"
        parts.append(f"{goal_label}: {learning_goal}")
    if focus:
        focus_label = "关注点" if use_chinese else "Focus"
        parts.append(f"{focus_label}: {focus}")
    if extra_notes:
        notes_label = "额外备注" if use_chinese else "Additional notes"
        parts.append(f"{notes_label}: {extra_notes}")

    default_msg = (
        "请以充满活力的少儿英语教师身份授课，语言温柔并鼓励互动。"
        if use_chinese
        else "Teach like an energetic young learner tutor: warm, encouraging, interactive."
    )

    if not parts:
        return default_msg

    joiner = "；" if use_chinese else "; "
    return joiner.join(parts) + ("。" if use_chinese else ".")


def _build_system_prompt(use_chinese: bool) -> str:
    if use_chinese:
        return (
            "你是小智，一位擅长启发式教学的少儿英语老师。"
            "请使用循序渐进的方式，引导孩子多开口。"
            "核心要求：\n"
            "1. 对话以英语为主，关键解释可以穿插简洁中文。\n"
            "2. 经常提问并等待孩子回应，可给出引导式选项。\n"
            "3. 提供及时表扬与具体反馈，鼓励孩子自信表达。\n"
            "4. 每个环节保持 2-3 句英语 + 1 句中文提示的节奏。\n"
            "5. 若孩子明显听不懂，请使用更简单的例句重新说明。"
        )
    return (
        "You are XiaoZhi, a playful bilingual English tutor for kids."
        " Keep the lesson light, encouraging, and highly interactive."
        " Requirements:\n"
        "1. Speak mostly in English; add short Mandarin explanations when essential.\n"
        "2. Ask frequent comprehension checks and wait for replies.\n"
        "3. Celebrate effort with specific praise and offer gentle corrections.\n"
        "4. Each step should have 2-3 short English lines plus one supportive Chinese hint.\n"
        "5. If the learner struggles, simplify immediately and scaffold with examples."
    )


def _build_user_prompt(
    *,
    lesson: Dict[str, Any],
    profile_summary: str,
    use_chinese: bool,
) -> str:
    heading = "课程任务" if use_chinese else "Lesson task"
    summary_label = "课程概要" if use_chinese else "Lesson summary"
    vocab_label = "核心词汇" if use_chinese else "Target vocabulary"
    sentence_label = "重点句型" if use_chinese else "Key sentences"
    grammar_label = "语法要点" if use_chinese else "Grammar focus"
    activity_label = "课堂活动" if use_chinese else "Class activities"
    story_label = "情境/故事" if use_chinese else "Context/Story"
    profile_label = "学员信息" if use_chinese else "Learner profile"
    deliver_label = "请完成下列教学步骤" if use_chinese else "Deliver the following lesson flow"

    lesson_title = lesson.get("title") or lesson.get("lesson_title") or "Lesson"
    book = lesson.get("book") or lesson.get("level") or "New Concept"
    lesson_number = lesson.get("lesson_number") or lesson.get("lesson") or lesson.get("lesson_id")
    summary = lesson.get("summary") or lesson.get("overview") or ""

    vocabulary_block = _format_vocabulary(lesson.get("vocabulary"))
    sentences_block = _format_simple_list(lesson.get("key_sentences"))
    grammar_block = _format_simple_list(lesson.get("grammar_points"))
    activities_block = _format_simple_list(lesson.get("activities"))
    story_block = _format_simple_list(lesson.get("story_outline"))

    parent_tip = lesson.get("parent_extension") or lesson.get("homework") or ""
    tips = lesson.get("teaching_tips") or lesson.get("tips") or ""

    phases_intro = _lesson_structure_instructions(use_chinese)

    info_lines = [
        f"Book: {book}",
        f"Lesson: {lesson_number}" if lesson_number else "",
        f"Title: {lesson_title}",
    ]
    info_text = " | ".join(filter(None, info_lines))

    parent_label = "课后亲子延伸" if use_chinese else "Parent extension"
    tip_label = "授课提醒" if use_chinese else "Teacher tips"

    return (
        f"{heading}: {info_text}\n"
        f"{profile_label}: {profile_summary}\n\n"
        f"{summary_label}:\n{summary if summary else '（资料未提供摘要，可结合课文自拟。）'}\n\n"
        f"{vocab_label}:\n{vocabulary_block}\n\n"
        f"{sentence_label}:\n{sentences_block}\n\n"
        f"{grammar_label}:\n{grammar_block}\n\n"
        f"{activity_label}:\n{activities_block}\n\n"
        f"{story_label}:\n{story_block}\n\n"
        f"{tip_label}: {tips if tips else ('保持课堂节奏轻快。' if use_chinese else 'Keep the pace upbeat and responsive.') }\n"
        f"{parent_label}: {parent_tip if parent_tip else ('建议复述课堂对话并分享给家长。' if use_chinese else 'Encourage the child to retell today\'s story to parents.') }\n\n"
        f"{deliver_label}:\n{phases_intro}"
    )


def _format_vocabulary(items: Any) -> str:
    if not items:
        return "- (暂无资料)"
    lines: List[str] = []
    if isinstance(items, dict):
        items = [items]
    if isinstance(items, (list, tuple, set)):
        for entry in items:
            if isinstance(entry, dict):
                phrase = entry.get("phrase") or entry.get("word") or entry.get("term")
                meaning = entry.get("translation") or entry.get("meaning")
                usage = entry.get("usage") or entry.get("example")
                phrase_text = phrase or "词汇"
                details = []
                if meaning:
                    details.append(meaning)
                if usage:
                    details.append(usage)
                if details:
                    lines.append(f"- {phrase_text}: {' / '.join(details)}")
                else:
                    lines.append(f"- {phrase_text}")
            else:
                lines.append(f"- {entry}")
    if not lines:
        return "- (暂无资料)"
    return "\n".join(lines)


def _format_simple_list(items: Any) -> str:
    if not items:
        return "- (暂无资料)"
    if isinstance(items, str):
        return "- " + items
    if isinstance(items, dict):
        items = list(items.values())
    lines = []
    for value in items:
        if isinstance(value, str):
            lines.append(f"- {value}")
        elif isinstance(value, dict):
            title = value.get("title") or value.get("name") or value.get("label")
            description = value.get("description") or value.get("text")
            if description:
                lines.append(f"- {title}: {description}") if title else lines.append(
                    f"- {description}"
                )
            elif title:
                lines.append(f"- {title}")
        else:
            lines.append(f"- {value}")
    return "\n".join(lines) if lines else "- (暂无资料)"


def _lesson_structure_instructions(use_chinese: bool) -> str:
    if use_chinese:
        return (
            "1. 热身 Warm-up：问候+简单回顾；设计 1 个动作或小游戏。\n"
            "2. 呈现 Presentation：用课堂故事导入目标词汇和句型。\n"
            "3. 练习 Practice：引导孩子造句、角色扮演或完成小任务。\n"
            "4. 巩固 Consolidation：小游戏/问答巩固重点并即时反馈。\n"
            "5. 收尾 Wrap-up：总结收获，布置可完成的家庭练习。"
        )
    return (
        "1. Warm-up: greetings + quick review; add a movement game.\n"
        "2. Presentation: set the scene and model the key phrases.\n"
        "3. Practice: guide the child to speak with role-play or mini tasks.\n"
        "4. Consolidation: quick game or quiz to reinforce learning.\n"
        "5. Wrap-up: celebrate progress and assign a light take-home idea."
    )


def _build_checkin_questions(lesson: Dict[str, Any], use_chinese: bool) -> List[str]:
    base_questions = lesson.get("check_in_questions")
    if isinstance(base_questions, list) and base_questions:
        return [str(q) for q in base_questions]

    prompts = lesson.get("key_sentences") or []
    vocab = lesson.get("vocabulary") or []

    questions: List[str] = []
    if prompts:
        example = prompts[0]
        if isinstance(example, dict):
            example = example.get("text") or example.get("sentence")
        if isinstance(example, str):
            if use_chinese:
                questions.append(f"你能跟我一起说一句：{example} 吗？")
            else:
                questions.append(f"Can you say with me: {example}?")
    if vocab:
        vocab_entry = vocab[0]
        if isinstance(vocab_entry, dict):
            phrase = vocab_entry.get("phrase") or vocab_entry.get("word")
        else:
            phrase = str(vocab_entry)
        if phrase:
            if use_chinese:
                questions.append(f"看到 {phrase} 这个词，你能想起课堂上的哪个情景吗？")
            else:
                questions.append(
                    f"When you hear '{phrase}', what part of today's story do you remember?"
                )
    if use_chinese:
        questions.append("今天的哪一句英语让你最有成就感？")
    else:
        questions.append("Which sentence made you feel proud today?")
    return questions


def _build_home_tasks(lesson: Dict[str, Any], use_chinese: bool) -> List[str]:
    extension = lesson.get("parent_extension")
    if isinstance(extension, list):
        return [str(item) for item in extension]
    if isinstance(extension, str) and extension:
        return [extension]

    if use_chinese:
        return [
            "请和家长一起复述课堂故事，并用新学的句子描述一个生活场景。",
            "和家人一起玩“找找看”游戏：找到家里 3 个物品并用英文介绍。",
        ]
    return [
        "Retell the class story to your family and use the new sentence pattern.",
        "Play a 'treasure hunt' at home: find 3 objects and describe them in English.",
    ]
