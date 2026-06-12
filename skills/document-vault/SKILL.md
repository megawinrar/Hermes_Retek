# Document Vault + Token Tracker

## Описание

Система хранения, индексации документов и учёта токенов. Любой файл из чата автоматически сохраняется, текст извлекается и индексируется для полнотекстового поиска. Все запросы к модели учитываются с расчётом стоимости в рублях.

## Обязательные правила

### При получении файла от пользователя:

1. Сохрани файл через `vault.save_document(file_path, original_name, source_chat, source_user)`
2. Извлеки текст из файла:
   - PDF: используй `pdftotext` или `PyPDF2`
   - DOCX: используй `python-docx`
   - XLSX: используй `openpyxl`, извлеки все листы
   - Изображения: опиши содержимое (OCR если доступен)
   - Текстовые файлы: читай напрямую
3. Индексируй текст: `vault.index_content(document_id, extracted_text)`
4. Подтверди пользователю: "📁 Сохранено: {filename} ({size_kb} KB). Проиндексировано."
5. Если файл содержит структурированные данные (инвойс, таблица) — извлеки ключевые поля и сохрани как `extracted_data`

### При поиске документов:

1. Используй `vault.search(query)` для полнотекстового поиска
2. Показывай: имя файла, дату, фрагмент с совпадением
3. Если пользователь просит файл — отправь из `file_path`

### Учёт токенов:

1. После каждого ответа записывай использование: `token_tracker.log_usage(input_tokens, output_tokens, chat_id=chat_id)`
2. По команде "статистика" или "сколько потрачено" — вызови `token_tracker.today_stats()` или `token_tracker.period_stats(7)`
3. Формат ответа: токены + стоимость в рублях

### Ежедневный отчёт (cron 21:00):

Отправляй владельцу:
```
📊 Статистика за сегодня
📥 Input: X токенов
📤 Output: Y токенов
💰 Стоимость: Z.ZZ ₽
🔄 Запросов: N
```

## Команды пользователя (естественный язык)

| Что скажет пользователь | Что делать |
|---|---|
| "Сохрани это" / кидает файл | save_document + index_content |
| "Найди документ про X" | search(X) |
| "Что у нас есть по теме X" | search(X) |
| "Покажи все документы" | list_documents() |
| "Покажи инвойсы" | list_documents(mime_filter="pdf") |
| "Сколько потрачено сегодня" | today_stats() |
| "Статистика за неделю" | period_stats(7) |
| "Статистика за месяц" | monthly_report() |
| "Сколько токенов" | today_stats() |

## Файлы скилла

- `vault.py` — основной модуль хранения и индексации
- `token_tracker.py` — учёт токенов и стоимости

## Тарифы YandexGPT (актуальные)

| Модель | Input (₽/1000 ток.) | Output (₽/1000 ток.) |
|---|---|---|
| yandexgpt/latest | 0.80 | 1.60 |
| yandexgpt-lite/latest | 0.20 | 0.40 |

## Использование в коде

```python
import sys
sys.path.insert(0, "/opt/data/skills/document-vault")
from vault import save_document, index_content, search, list_documents, stats
from token_tracker import log_usage, today_stats, period_stats, monthly_report, format_report
```
