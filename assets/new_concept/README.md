# New Concept English Lesson Data

This folder stores structured lesson resources consumed by the New Concept MCP tools.

## File layout

- `lessons_sample.json` – minimal demo data with two starter lessons.
- `lessons.json` (optional) – drop your full dataset here to override the sample file.
- `lessons/` (optional) – alternatively place individual `*.json` lesson files and they will be merged.

## JSON structure

Each lesson entry is a JSON object. Only `book` and a lesson identifier (`lesson_number` or `lesson_id`) are required. Other fields are optional but improve the generated prompts.

```json
{
  "book": "Book 1",
  "lesson_number": 1,
  "lesson_id": "B1L01",
  "title": "Hello, I'm Sam!",
  "summary": "Introduce greeting phrases...",
  "vocabulary": [
    {"phrase": "hello", "translation": "你好", "usage": "Say hello when you meet someone."}
  ],
  "key_sentences": ["Hello, I'm Sam."],
  "grammar_points": ["Using 'I am' ..."],
  "activities": ["Role-play meeting a new classmate."],
  "story_outline": ["Sam arrives in class and meets everyone."],
  "teaching_tips": "Encourage shy learners to use props.",
  "parent_extension": "Retell the greetings at home."
}
```

When multiple data sources are present, the loader prefers (in order):

1. `lessons.json`
2. `lessons_sample.json`
3. All `*.json` files in this folder
4. Files under `lessons/*.json`

Restart XiaoZhi after updating the data so that the MCP server reloads the lessons.
