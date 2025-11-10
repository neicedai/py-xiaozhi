"""Data loading utilities for New Concept English lessons."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.utils.logging_config import get_logger
from src.utils.resource_finder import resource_finder

logger = get_logger(__name__)


@dataclass
class LessonRecord:
    """Represents a single lesson entry loaded from disk."""

    book: str
    lesson_number: Optional[int]
    lesson_id: Optional[str]
    title: str
    raw: Dict[str, Any]
    normalized_book: str
    normalized_lesson: str

    def short_dict(self) -> Dict[str, Any]:
        """Return a lightweight dictionary for listings."""

        return {
            "book": self.book,
            "lesson_number": self.lesson_number,
            "lesson_id": self.lesson_id,
            "title": self.title,
            "summary": self.raw.get("summary", ""),
        }


class LessonRepository:
    """Repository responsible for loading and indexing lesson data."""

    def __init__(self) -> None:
        self._lessons: List[LessonRecord] = []
        self._index: Dict[Tuple[str, str], LessonRecord] = {}
        self._loaded = False

    # ---------------------------- public API ----------------------------
    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.reload()

    def reload(self) -> None:
        """Reload lesson data from disk."""

        logger.info("[NewConcept] Loading lesson data ...")
        self._lessons.clear()
        self._index.clear()

        entries = _load_raw_entries()
        for entry in entries:
            record = _build_record(entry)
            if not record:
                continue
            key = (record.normalized_book, record.normalized_lesson)
            if key in self._index:
                logger.warning(
                    "[NewConcept] Duplicate lesson detected for %s #%s, replacing previous entry",
                    record.book,
                    record.lesson_number or record.lesson_id,
                )
            self._index[key] = record

        self._lessons = sorted(
            self._index.values(),
            key=lambda rec: (
                rec.normalized_book,
                rec.lesson_number if rec.lesson_number is not None else 999,
                rec.title,
            ),
        )
        self._loaded = True
        logger.info(
            "[NewConcept] Loaded %d lesson entries from disk", len(self._lessons)
        )

    def list_lessons(self, book: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return metadata for available lessons, optionally filtered by book."""

        self.ensure_loaded()
        target = _normalize_book(book) if book else None
        result = []
        for record in self._lessons:
            if target and record.normalized_book != target:
                continue
            result.append(record.short_dict())
        return result

    def find_lesson(self, book: str, lesson: str) -> Optional[LessonRecord]:
        """Find a specific lesson by book identifier and lesson number/id."""

        self.ensure_loaded()
        book_key = _normalize_book(book)
        lesson_key = _normalize_lesson_identifier(lesson)

        if not book_key:
            logger.error("[NewConcept] Book identifier is empty: %s", book)
            return None
        if not lesson_key:
            logger.error("[NewConcept] Lesson identifier is empty: %s", lesson)
            return None

        record = self._index.get((book_key, lesson_key))
        if record:
            return record

        # Try fallback: iterate all lessons in the same book and match by lesson number or id heuristics
        for candidate in self._lessons:
            if candidate.normalized_book != book_key:
                continue
            if candidate.lesson_id and _normalize_lesson_identifier(candidate.lesson_id) == lesson_key:
                return candidate
            if candidate.lesson_number is not None and lesson_key.isdigit():
                try:
                    if int(lesson_key) == candidate.lesson_number:
                        return candidate
                except ValueError:
                    pass
        return None

    def books(self) -> List[str]:
        """Return sorted list of distinct book identifiers."""

        self.ensure_loaded()
        books = {record.book for record in self._lessons}
        return sorted(books)


# ---------------------------- helper functions ----------------------------

def _load_raw_entries() -> List[Dict[str, Any]]:
    project_root = resource_finder.get_project_root()
    candidate_dirs = [
        project_root / "documents" / "new_concept",
        project_root / "documents" / "docs" / "new_concept",
        project_root / "assets" / "new_concept",
    ]

    entries: List[Dict[str, Any]] = []
    for directory in candidate_dirs:
        if not directory.exists():
            continue

        logger.info("[NewConcept] Scanning data directory: %s", directory)
        files: List[Path] = []

        preferred = directory / "lessons.json"
        fallback = directory / "lessons_sample.json"

        if preferred.is_file():
            files.append(preferred)
        elif fallback.is_file():
            files.append(fallback)
        else:
            files.extend(sorted(directory.glob("*.json")))
            lessons_dir = directory / "lessons"
            if lessons_dir.is_dir():
                files.extend(sorted(lessons_dir.glob("*.json")))

        loaded_any = False
        for file_path in files:
            try:
                entries.extend(_load_json_file(file_path))
                loaded_any = True
            except Exception as exc:  # pragma: no cover - log then continue
                logger.error(
                    "[NewConcept] Failed to load %s: %s", file_path, exc, exc_info=True
                )
        if loaded_any:
            break

    return entries


def _load_json_file(path: Path) -> Iterable[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)

    if isinstance(data, dict):
        # Accept {"lessons": [...]} or a single lesson dictionary
        if "lessons" in data and isinstance(data["lessons"], list):
            data_list = data["lessons"]
        else:
            data_list = [data]
    elif isinstance(data, list):
        data_list = data
    else:
        raise ValueError(f"Unsupported data format in {path}")

    normalized_entries: List[Dict[str, Any]] = []
    for index, entry in enumerate(data_list):
        if not isinstance(entry, dict):
            logger.warning(
                "[NewConcept] Skip non-dict entry at %s index %d", path, index
            )
            continue
        normalized_entries.append(entry)

    logger.info(
        "[NewConcept] Loaded %d entries from %s", len(normalized_entries), path
    )
    return normalized_entries


def _build_record(entry: Dict[str, Any]) -> Optional[LessonRecord]:
    book = entry.get("book") or entry.get("level") or entry.get("volume")
    lesson_id = entry.get("lesson_id") or entry.get("id")
    lesson_number = _extract_lesson_number(entry)
    title = entry.get("title") or entry.get("name")

    if not book:
        logger.warning("[NewConcept] Skip entry without book: %s", entry)
        return None

    if lesson_number is None and not lesson_id:
        logger.warning(
            "[NewConcept] Skip entry without lesson identifier: %s", entry
        )
        return None

    normalized_book = _normalize_book(book)
    normalized_lesson = (
        _normalize_lesson_identifier(lesson_number)
        if lesson_number is not None
        else _normalize_lesson_identifier(lesson_id)
    )

    if not normalized_lesson:
        logger.warning(
            "[NewConcept] Skip entry with invalid lesson identifier: %s", entry
        )
        return None

    if not title:
        # Fallback to auto-generated title
        if lesson_number is not None:
            title = f"Lesson {lesson_number}"
        elif lesson_id:
            title = str(lesson_id)
        else:
            title = "Untitled Lesson"

    return LessonRecord(
        book=str(book),
        lesson_number=lesson_number,
        lesson_id=str(lesson_id) if lesson_id else None,
        title=str(title),
        raw=entry,
        normalized_book=normalized_book,
        normalized_lesson=normalized_lesson,
    )


def _extract_lesson_number(entry: Dict[str, Any]) -> Optional[int]:
    keys = (
        "lesson_number",
        "lessonNo",
        "lesson",
        "number",
        "lessonIndex",
        "lesson_no",
    )
    for key in keys:
        if key not in entry:
            continue
        value = entry[key]
        number = _parse_int(value)
        if number is not None:
            return number
    return None


def _parse_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        matches = re.findall(r"\d+", value)
        if matches:
            try:
                return int(matches[0])
            except ValueError:
                return None
    return None


def _normalize_book(value: Optional[str]) -> str:
    if not value:
        return ""
    value = str(value).lower()
    value = value.replace("new concept english", "")
    value = value.replace("新概念英语", "")
    normalized = re.sub(r"[^a-z0-9]+", "", value)
    return normalized


def _normalize_lesson_identifier(value: Optional[Any]) -> str:
    if value is None:
        return ""
    if isinstance(value, LessonRecord):
        value = value.lesson_number if value.lesson_number is not None else value.lesson_id
    if isinstance(value, int):
        return f"{value:03d}"
    if isinstance(value, float):
        return f"{int(value):03d}"
    text = str(value).strip().lower()
    if not text:
        return ""
    digits = re.findall(r"\d+", text)
    if digits:
        return digits[-1].zfill(3)
    return re.sub(r"[^a-z0-9]+", "", text)


_repository: Optional[LessonRepository] = None


def get_repository() -> LessonRepository:
    global _repository
    if _repository is None:
        _repository = LessonRepository()
    return _repository
