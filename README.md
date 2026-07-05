# Personal AI Voice Diary

Self-hosted, single-user, 24/7 voice diary. Speak → transcribed in browser → stored locally → multi-turn chat with an LLM → RAG-augmented recall.

> See `PLAN.md` for the full design (tech stack, phases, risks, data model).

## Status

**Phase 3.6 — Edit / Delete entries + reverse-geocoded address.** Working locally with DeepSeek + FTS5.

- ✅ Phase 0: env, skeleton, `/health`
- ✅ Phase 1: SQLite, `POST/GET /api/diary`, PWA mic + transcript + history
- ✅ Phase 2: chat router (SSE), LLM client, extraction service, PWA chat mode
- ✅ Phase 3: SQLite FTS5 RAG + memory-mode toggle, Diary mode (journal layout, date-grouped, search), language selector (zh-CN/en-US)
- ✅ Phase 3.5: auto-grow textareas, 📥 markdown export, 📤 markdown import, weather capture (geolocation + Open-Meteo)
- ✅ Phase 3.6: ✏️ Edit / 🗑️ Delete per entry (inline editor, Save / Discard), reverse-geocoded address in weather chip (BigDataCloud)

## Prerequisites

- macOS / Linux
- Python 3.11+ (Homebrew: `brew install python@3.11`)
- A Chromium-based browser (Chrome / Edge / Arc / Brave) for Web Speech API. Safari/Firefox fall back to manual typing.
- A [DeepSeek](https://platform.deepseek.com/) API key for chat + extraction (Phase 2/3)
- (Phase 5) Docker for Synology deployment

## Setup

```bash
# 1. Create venv
/opt/homebrew/bin/python3.11 -m venv .venv

# 2. Activate
source .venv/bin/activate

# 3. Install deps
pip install -r requirements.txt

# 4. Configure env
cp .env.example .env
# edit .env and fill in DEEPSEEK_API_KEY
```

## Run (development)

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:

- http://127.0.0.1:8000/ — **the PWA** (3 modes: 📝 Quick | 💬 Chat | 📖 Diary)
- http://127.0.0.1:8000/health — liveness probe
- http://127.0.0.1:8000/docs — interactive API docs

## Phase 1 acceptance test (Quick mode)

1. Open http://127.0.0.1:8000/ in Chrome/Edge. Default tab is **📝 Quick**.
2. Select 🌐 language (中文 / English) in the header.
3. Click the mic button. Browser asks for microphone permission — allow it.
4. Speak. The transcript appears live in the textarea.
5. Click **💾 Save entry**. Status shows `Saved (id=N)`. Entry appears in **History** (grouped by day).
6. Reload the page — entry persists.

## Phase 2 acceptance test (Chat mode)

1. Click **💬 Chat** in the mode toggle.
2. Click **🆕 New**. A welcome bubble appears.
3. Type a message and press **➤** (or Enter). The assistant bubble streams in token-by-token.
4. Send more messages — multi-turn works, history preserved.
5. Click **💾 End & save** — server runs LLM extraction; green "Extracted" panel shows result. New diary entry appears in Quick mode's History.

## Phase 3 acceptance test (Diary mode + RAG)

1. Click **📖 Diary** tab. Journal layout with date headers (今天 / 昨天 / 具体日期).
2. Each entry shows time, summary, text, and tags (mood, people, events, follow-ups).
3. Use the **search box** to filter by keyword (matches text, summary, people).
4. Click **💬 Chat** and start a new conversation.
5. **Toggle 🧠 Memory** ON (top of chat panel).
6. Ask "上周跟谁吃饭了?" — the assistant should reference the relevant past entry. With memory OFF, it asks back (no context).
7. Try "我最近为什么焦虑?" with memory ON — assistant should surface the anxiety entry from past diaries.
8. Click **💾 End & save** to convert this conversation into a new diary entry (which will be indexed and available for future RAG).

## RAG design notes

- **Index**: SQLite FTS5 virtual table (`diary_fts`) with `unicode61` tokenization. Chinese text is pre-segmented with spaces between adjacent CJK characters so per-character keyword queries work.
- **Recall strategy**: FTS top-3 keyword matches + 5 most recent entries, deduplicated. The LLM gets a "diary scroll" of recent context plus keyword-targeted matches.
- **Why FTS5 not ChromaDB**: ChromaDB's default ONNX embedding model (all-MiniLM-L6-v2) is ~80MB and downloads from PyPI, which was prohibitively slow on this network. FTS5 is built into Python's stdlib sqlite3, no model, no download. For the single-user diary use case (likely < 1000 entries), FTS5 + recent-context is more than adequate. If semantic search is needed later, swap `app/services/vector.py` for a sentence-transformers client — the chat router and recall formatter wouldn't need to change.
- **When does RAG fire**: only when the conversation's `memory_mode` is ON (toggled in the chat header). Default is OFF so a normal recording session isn't polluted with old entries.
- **When are entries indexed**: on every new entry — `POST /api/diary` and the `POST /api/chat/{id}/end` both call `vector.add_entry`.

## API reference

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health`                            | Liveness probe |
| `POST` | `/api/diary`                          | Create entry — `{"text", "conversation", "weather", "created_at"}` |
| `GET`  | `/api/diary`                          | List entries (newest first), `?limit=200&offset=0` |
| `GET`  | `/api/diary/{id}`                     | Single entry |
| `PATCH`| `/api/diary/{id}`                     | Update fields — `{"raw_text", "mood", "summary", "events", "people", "follow_ups", "weather"}` (any subset) |
| `DELETE`| `/api/diary/{id}`                    | Delete entry (and remove from FTS index) |
| `POST` | `/api/diary/import`                   | Bulk insert entries (used by 📤 markdown import) — `{"entries": [...]}` |
| `POST` | `/api/chat`                           | Start new conversation → `{conversation_id, welcome_message, memory_mode}` |
| `GET`  | `/api/chat/{id}`                      | Fetch full conversation (memory_mode, status, messages) |
| `PATCH`| `/api/chat/{id}`                      | Toggle `memory_mode` → `{"memory_mode": bool}` |
| `POST` | `/api/chat/{id}/message`              | Send user msg → SSE stream of assistant reply |
| `POST` | `/api/chat/{id}/end`                  | End conversation, extract, save diary entry |

All responses are wrapped: `{"data": <T>, "error": null}` or `{"data": null, "error": {"message", "code"}}`.

### SSE format (POST /api/chat/{id}/message)

```
data: {"delta": "<text chunk>"}    ← repeated for each token
event: done
data: {"length": N}                ← sent once at the end
event: error
data: {"error": "..."}             ← on failure
```

## LLM provider

- **Provider**: [DeepSeek](https://platform.deepseek.com/) (OpenAI-compatible)
- **Default model**: `deepseek-chat` (V3) or `deepseek-reasoner` (R1) — set `DEEPSEEK_MODEL` in `.env`
- **Note**: the `deepseek-v4-flash` model name sometimes appearing in `.env.example` is not a real DeepSeek model and will return 400

## DeepSeek API verification

```bash
source .venv/bin/activate
curl -X POST "$DEEPSEEK_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$DEEPSEEK_MODEL"'",
    "messages": [{"role":"user","content":"ping"}],
    "max_tokens": 8
  }'
```

## Resetting the local database

```bash
rm -f data/diary.db data/diary.db-shm data/diary.db-wal
# next uvicorn startup will recreate the schema (diary_entries, conversations, diary_fts)
```

## Project layout

```
diary/
├── PLAN.md                ← full design doc
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── main.py             ← FastAPI entry + static mount
│   ├── config.py           ← env loading
│   ├── database.py         ← SQLite + FTS5 schema init (WAL + busy_timeout)
│   ├── models.py           ← Pydantic schemas
│   ├── responses.py        ← success/error envelope helpers
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── diary.py        ← POST/GET /api/diary (vectorizes on insert)
│   │   └── chat.py         ← /api/chat/* with SSE + RAG (vectorizes on end)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── llm.py          ← DeepSeek (OpenAI-compatible) client
│   │   ├── extract.py      ← LLM-based structured extraction
│   │   ├── vector.py       ← FTS5 RAG (search, recent, add_entry)
│   │   └── recall.py       ← build_recall_context (FTS + recent)
│   └── static/
│       ├── index.html      ← PWA shell (3 modes + lang selector)
│       ├── app.js          ← Web Speech + fetch + SSE + memory toggle
│       └── style.css
└── data/                   ← gitignored: diary.db (WAL mode)
```

## Next phase

Phase 4 — proactive prompts (cron: "on this day last year", overdue follow-ups) + Web Push notifications. See `PLAN.md` §5.

## Phase 3.5 details

### Auto-grow textareas
Both `#transcript` (Quick) and `#chat-input` (Chat) auto-grow with content. Capped at 18rem / 10rem respectively, then scroll. Reset on Clear / after Send.

### Weather capture
- Triggered on every Quick save and Chat end.
- Browser asks for **geolocation permission** the first time. Grant = weather captured; deny = no weather (no error).
- Lat/lon → **Open-Meteo** (`https://api.open-meteo.com/v1/forecast`, free, no API key) → `{temp_c, weather_code, condition, emoji, location, source, captured_at}`.
- 3-second timeout; cached in browser for 10 min.
- WMO weather codes mapped to Chinese descriptions (晴/多云/小雨/etc) + emoji.
- Stored in `diary_entries.weather` JSON column. Displayed as a small `☀️ 晴 28°C · 30.27°N 120.16°E` chip in Diary view and History list.

### Markdown export
- Click `📥 Export` in the Diary tab → downloads `voice-diary-YYYY-MM-DD.md`.
- Grouped by day (`## 2026-06-08 (周三)`), each entry as `### HH:MM · #id · mood` with summary as a blockquote, mood/people/events/follow-ups as bullet-style lines, body as plain text, and a `---` separator.
- 100% round-trippable via the Import button.

### Markdown import
- Click `📤 Import` → file picker → parse → POST to `/api/diary/import`.
- Parser is tolerant: extracts `## YYYY-MM-DD` day groups, `### HH:MM · #N` entry headers, optional summary quote, optional `**人物/事件/后续**: ...` lines, body until `---`.
- Full original entry dict is stored in `raw_metadata` for re-export fidelity.
- Re-indexes each new entry into FTS so it's immediately available for RAG.

### Schema (Phase 3.5 additions)
```sql
ALTER TABLE diary_entries ADD COLUMN weather      JSON;   -- {temp_c, condition, emoji, location, address, address_parts, source, captured_at}
ALTER TABLE diary_entries ADD COLUMN raw_metadata JSON;   -- full original payload, for export round-trip
```
Both added via idempotent `PRAGMA table_info` check in `init_db()`, so re-running is safe.

## Phase 3.6 details

### Edit / Delete entries
- Every Diary entry has **✏️ Edit** and **🗑️ Delete** buttons in a small action row.
- **Edit**: turns the entry text into an auto-sized `<textarea>`, shows **💾 Save** + **↶ Discard** buttons. Save → `PATCH /api/diary/{id}` with `raw_text`; FTS index is re-built. Discard → reverts to display mode. No-op save (text unchanged) is also a discard.
- **Delete**: confirms via `window.confirm`, then `DELETE /api/diary/{id}`. Removes the entry from both the source table and the FTS index. Also clears any conversation that was linked to it.
- Backend whitelist: only the 7 editable columns are accepted; everything else is ignored.

### Reverse-geocoded address
- Captured alongside weather on every Quick save and Chat end.
- Lat/lon → **BigDataCloud** `/data/reverse-geocode-client` (free, no key, low-volume intended). Returns country · state · city · district · postcode.
- Stored on the weather JSON as `address` (formatted `中国 · 浙江省 · 杭州市 · 拱墅區`) and `address_parts` (the raw fields).
- Displayed in the weather chip instead of the raw lat/lon when available. Falls back to lat/lon if Nominatim/BDC is unreachable.
