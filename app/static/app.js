// Personal AI Voice Diary — Phase 3.5 frontend.
// Pure browser JS. Web Speech API for STT, fetch + SSE for chat, RAG via memory toggle,
// geolocation + Open-Meteo for weather, markdown export/import.

(() => {
  'use strict';

  // ---- DOM helpers ---------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const setText = (el, txt) => { el.textContent = txt; };

  // ---- Auto-grow textarea (Phase 3.5) -------------------------------------
  function autogrow(textarea) {
    if (!textarea) return;
    textarea.style.height = 'auto';
    const maxH = parseFloat(getComputedStyle(textarea).maxHeight) || Infinity;
    textarea.style.height = Math.min(textarea.scrollHeight, maxH) + 'px';
  }

  // ---- Settings (localStorage) --------------------------------------------
  const LS_LANG = 'diary.lang';
  const langSelect = $('lang-select');
  const currentLang = () => langSelect.value;
  langSelect.value = localStorage.getItem(LS_LANG) || 'zh-CN';
  langSelect.addEventListener('change', () => {
    localStorage.setItem(LS_LANG, langSelect.value);
    setupQuickRecognition();
    setupChatRecognition();
    updateLangIndicators();
  });

  // ---- Mode toggle ---------------------------------------------------------
  const modeQuickBtn = $('mode-quick');
  const modeChatBtn = $('mode-chat');
  const modeDiaryBtn = $('mode-diary');
  const quickPanel = $('quick-panel');
  const chatPanel = $('chat-panel');
  const diaryPanel = $('diary-panel');

  function setMode(mode) {
    const map = {
      quick: [modeQuickBtn, quickPanel],
      chat:  [modeChatBtn, chatPanel],
      diary: [modeDiaryBtn, diaryPanel],
    };
    Object.entries(map).forEach(([k, [btn, panel]]) => {
      const isActive = k === mode;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
      panel.hidden = !isActive;
    });
    if (mode === 'diary') loadDiary();
  }
  modeQuickBtn.addEventListener('click', () => setMode('quick'));
  modeChatBtn.addEventListener('click', () => setMode('chat'));
  modeDiaryBtn.addEventListener('click', () => setMode('diary'));

  // ==========================================================================
  // Quick mode (Phase 1) — with date-grouped history + weather
  // ==========================================================================
  const micBtn = $('mic-btn');
  const micStatus = $('mic-status');
  const transcript = $('transcript');
  const saveBtn = $('save-btn');
  const clearBtn = $('clear-btn');
  const historyList = $('history-list');
  const historyEmpty = $('history-empty');

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let isRecording = false;

  const sttLangValue = $('stt-lang-value');
  const chatSttLangValue = $('chat-stt-lang-value');

  function updateLangIndicators() {
    const lang = currentLang();
    const label = lang === 'zh-CN' ? '中文' : lang === 'en-US' ? 'English' : lang;
    if (sttLangValue) setText(sttLangValue, label);
    if (chatSttLangValue) setText(chatSttLangValue, label);
  }
  updateLangIndicators();

  function setupQuickRecognition() {
    if (!SpeechRecognition) {
      micBtn.disabled = true;
      setText(micStatus, 'Web Speech API not supported in this browser. Use Chrome/Edge or type directly.');
      if (sttLangValue) setText(sttLangValue, '不支持');
      return;
    }
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = currentLang();

    recognition.onstart = () => {
      isRecording = true;
      micBtn.classList.add('recording');
      setText(micStatus, '🔴 Listening... speak now');
    };
    recognition.onresult = (event) => {
      let finalText = '';
      let interimText = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += t;
        else interimText += t;
      }
      if (finalText) {
        transcript.value = (transcript.value.trim() + ' ' + finalText.trim()).trim();
        autogrow(transcript);
      }
      if (interimText) setText(micStatus, `... ${interimText}`);
    };
    recognition.onerror = (event) => {
      setText(micStatus, `Speech error: ${event.error || 'unknown'}. You can still type and save.`);
      stopQuickRecording();
    };
    recognition.onend = () => {
      isRecording = false;
      micBtn.classList.remove('recording');
      if (micStatus.textContent.startsWith('🔴') || micStatus.textContent.startsWith('...')) {
        setText(micStatus, 'Stopped. Edit transcript if needed, then save.');
      }
    };
    micBtn.disabled = false;
  }
  setupQuickRecognition();

  function startQuickRecording() {
    if (!recognition) return;
    try { recognition.start(); } catch (_) {}
  }
  function stopQuickRecording() {
    if (!recognition) return;
    try { recognition.stop(); } catch (_) {}
  }
  micBtn.addEventListener('click', () => (isRecording ? stopQuickRecording() : startQuickRecording()));

  clearBtn.addEventListener('click', () => {
    transcript.value = '';
    autogrow(transcript);
    setText(micStatus, 'Cleared.');
  });

  // Auto-grow as user types
  transcript.addEventListener('input', () => autogrow(transcript));
  autogrow(transcript);  // initial size

  saveBtn.addEventListener('click', async () => {
    const text = transcript.value.trim();
    if (!text) { setText(micStatus, 'Nothing to save. Record or type something first.'); return; }
    saveBtn.disabled = true;
    setText(micStatus, 'Saving…');
    const weather = await getWeather();
    try {
      const res = await fetch('/api/diary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, weather }),
      });
      const body = await res.json();
      if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
      setText(micStatus, `Saved (id=${body.data.id})${weather ? ' · with weather' : ''}.`);
      transcript.value = '';
      autogrow(transcript);
      await loadHistory();
    } catch (e) {
      setText(micStatus, `Save failed: ${e.message}`);
    } finally {
      saveBtn.disabled = false;
    }
  });

  // ---- Date / weather helpers ---------------------------------------------
  function parseDate(s) {
    if (!s) return null;
    const iso = s.includes('T') ? s : s.replace(' ', 'T') + 'Z';
    const d = new Date(iso);
    return isNaN(d) ? null : d;
  }
  function dayKey(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  function isSameDay(a, b) { return a && b && dayKey(a) === dayKey(b); }
  function relativeDayLabel(d) {
    const today = new Date();
    const yest = new Date(today); yest.setDate(today.getDate() - 1);
    if (isSameDay(d, today)) return '今天';
    if (isSameDay(d, yest)) return '昨天';
    const diff = Math.floor((today - d) / 86400000);
    if (diff > 0 && diff < 7) return `${diff} 天前`;
    return null;
  }
  function formatDayHeader(d) {
    const rel = relativeDayLabel(d);
    if (rel) return rel;
    const y = d.getFullYear();
    const m = d.getMonth() + 1;
    const day = d.getDate();
    const weekday = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
    return `${y} 年 ${m} 月 ${day} 日 · 周${weekday}`;
  }
  function formatTime(d) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  function weatherChip(w) {
    if (!w) return '';
    const emoji = w.emoji || '🌡️';
    const cond = w.condition || '';
    const temp = (w.temp_c != null) ? `${Math.round(w.temp_c)}°C` : '';
    // Prefer the reverse-geocoded address (city · district · ...) when
    // available; fall back to the raw lat/lon label.
    const place = w.address || w.location || '';
    return `<span class="weather-chip" title="${escapeHtml(JSON.stringify(w))}">${emoji} ${escapeHtml(cond)} ${escapeHtml(temp)}${place ? ' · ' + escapeHtml(place) : ''}</span>`;
  }

  // ---- Weather capture (Phase 3.5) ----------------------------------------
  // Try browser geolocation; on success, hit Open-Meteo (no key needed).
  // Best-effort: returns null on any failure, never blocks the save.
  let weatherCache = { value: null, ts: 0 };
  const WEATHER_TTL_MS = 10 * 60 * 1000;  // 10 min
  const WEATHER_TIMEOUT_MS = 3000;

  function getPosition() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) return reject(new Error('no geolocation'));
      navigator.geolocation.getCurrentPosition(
        pos => resolve(pos.coords),
        err => reject(err),
        { timeout: WEATHER_TIMEOUT_MS, maximumAge: 5 * 60 * 1000 }
      );
    });
  }

  async function fetchOpenMeteo(lat, lon) {
    const url = new URL('https://api.open-meteo.com/v1/forecast');
    url.searchParams.set('latitude', lat.toFixed(4));
    url.searchParams.set('longitude', lon.toFixed(4));
    url.searchParams.set('current', 'temperature_2m,weather_code');
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), WEATHER_TIMEOUT_MS);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      if (!res.ok) return null;
      const data = await res.json();
      const cur = data.current || {};
      if (cur.temperature_2m == null || cur.weather_code == null) return null;
      return {
        temp_c: Number(cur.temperature_2m),
        weather_code: Number(cur.weather_code),
        condition: wmoCondition(cur.weather_code),
        emoji: wmoEmoji(cur.weather_code),
        location: `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? 'E' : 'W'}`,
        source: 'open-meteo',
        captured_at: cur.time || new Date().toISOString(),
      };
    } catch (_) {
      return null;
    } finally {
      clearTimeout(t);
    }
  }

  function wmoCondition(code) {
    const map = {
      0:'晴',1:'少云',2:'多云',3:'阴',45:'雾',48:'冻雾',
      51:'小毛毛雨',53:'毛毛雨',55:'大毛毛雨',
      56:'冻毛毛雨',57:'强冻毛毛雨',
      61:'小雨',63:'中雨',65:'大雨',66:'冻雨',67:'强冻雨',
      71:'小雪',73:'中雪',75:'大雪',77:'雪粒',
      80:'小阵雨',81:'中阵雨',82:'大阵雨',
      85:'小阵雪',86:'大阵雪',
      95:'雷暴',96:'雷暴伴小冰雹',99:'雷暴伴大冰雹',
    };
    return map[code] || `代码 ${code}`;
  }
  function wmoEmoji(code) {
    if (code === 0) return '☀️';
    if (code <= 2) return '🌤️';
    if (code === 3) return '☁️';
    if (code <= 48) return '🌫️';
    if (code <= 67) return '🌧️';
    if (code <= 77) return '🌨️';
    if (code <= 82) return '🌦️';
    if (code <= 86) return '🌨️';
    return '⛈️';
  }

  async function getWeather() {
    const now = Date.now();
    if (weatherCache.value && now - weatherCache.ts < WEATHER_TTL_MS) {
      return weatherCache.value;
    }
    try {
      const coords = await getPosition();
      const w = await fetchOpenMeteo(coords.latitude, coords.longitude);
      if (w) {
        weatherCache = { value: w, ts: now };
        return w;
      }
    } catch (_) { /* fall through */ }
    return null;
  }

  // ---- History (date-grouped, with weather) -------------------------------
  async function loadHistory() {
    try {
      const res = await fetch('/api/diary?limit=200');
      const body = await res.json();
      if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
      renderHistory(body.data.items || []);
    } catch (e) {
      historyList.innerHTML = `<p class="error">Failed to load: ${e.message}</p>`;
    }
  }

  function renderHistory(items) {
    if (!items.length) {
      historyList.innerHTML = '';
      historyEmpty.style.display = 'block';
      return;
    }
    historyEmpty.style.display = 'none';
    const groups = new Map();
    for (const item of items) {
      const d = parseDate(item.created_at);
      if (!d) continue;
      const k = dayKey(d);
      if (!groups.has(k)) groups.set(k, { date: d, items: [] });
      groups.get(k).items.push({ item, date: d });
    }
    const sortedDays = [...groups.values()].sort((a, b) => b.date - a.date);

    historyList.innerHTML = sortedDays.map(g => `
      <div class="day-group">
        <h3 class="day-header">${formatDayHeader(g.date)}</h3>
        <ul class="day-entries">
          ${g.items.map(({ item, date }) => `
            <li class="entry" data-id="${item.id}">
              <div class="entry-meta">
                <span class="entry-id">#${item.id}</span>
                <span class="entry-time">${formatTime(date)}</span>
                ${item.mood ? `<span class="entry-mood">· ${escapeHtml(item.mood)}</span>` : ''}
                ${weatherChip(item.weather)}
              </div>
              ${item.summary ? `<div class="entry-summary">📝 ${escapeHtml(item.summary)}</div>` : ''}
              <div class="entry-text">${escapeHtml(item.raw_text)}</div>
            </li>
          `).join('')}
        </ul>
      </div>
    `).join('');
  }

  // ---- Diary mode (Phase 3) — full journal layout ------------------------
  const diaryList = $('diary-list');
  const diaryEmpty = $('diary-empty');
  const diarySearch = $('diary-search');
  const diaryRefresh = $('diary-refresh');
  const diaryExport = $('diary-export');
  const diaryImportBtn = $('diary-import');
  const diaryImportFile = $('diary-import-file');
  let diaryCache = [];

  async function loadDiary() {
    diaryList.innerHTML = '<p class="empty">Loading…</p>';
    try {
      const res = await fetch('/api/diary?limit=200');
      const body = await res.json();
      if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
      diaryCache = body.data.items || [];
      renderDiary(diaryCache);
    } catch (e) {
      diaryList.innerHTML = `<p class="error">Failed to load: ${e.message}</p>`;
    }
  }

  function renderDiary(items) {
    const q = (diarySearch.value || '').trim().toLowerCase();
    const filtered = q
      ? items.filter(i =>
          (i.raw_text || '').toLowerCase().includes(q) ||
          (i.summary || '').toLowerCase().includes(q) ||
          ((i.people || []).join(',')).toLowerCase().includes(q) ||
          (i.mood || '').toLowerCase().includes(q))
      : items;
    if (!filtered.length) {
      diaryList.innerHTML = '';
      diaryEmpty.style.display = 'block';
      diaryEmpty.textContent = q ? '没有匹配的日记。' : 'No entries yet.';
      return;
    }
    diaryEmpty.style.display = 'none';

    const groups = new Map();
    for (const item of filtered) {
      const d = parseDate(item.created_at);
      if (!d) continue;
      const k = dayKey(d);
      if (!groups.has(k)) groups.set(k, { date: d, items: [] });
      groups.get(k).items.push({ item, date: d });
    }
    const sortedDays = [...groups.values()].sort((a, b) => b.date - a.date);

    diaryList.innerHTML = sortedDays.map(g => `
      <article class="diary-day">
        <header class="diary-day-header">
          <h3>${formatDayHeader(g.date)}</h3>
          <span class="diary-day-count">${g.items.length} 篇</span>
        </header>
        ${g.items.map(({ item, date }) => renderDiaryEntry(item, date)).join('')}
      </article>
    `).join('');
  }

  function renderDiaryEntry(item, date) {
    const people = (item.people || []).join('、');
    const events = (item.events || []).map(e => `${e.description}${e.time_anchor ? ' (' + e.time_anchor + ')' : ''}`).join('；');
    const followUps = (item.follow_ups || []).map(f => `${f.description}${f.due ? ' (' + f.due + ')' : ''}`).join('；');
    return `
      <div class="diary-entry" data-id="${item.id}">
        <div class="diary-entry-time">${formatTime(date)}</div>
        <div class="diary-entry-body">
          ${item.summary ? `<p class="diary-entry-summary">${escapeHtml(item.summary)}</p>` : ''}
          <p class="diary-entry-text">${escapeHtml(item.raw_text)}</p>
          <div class="diary-entry-meta">
            ${item.mood ? `<span class="tag mood">😶 ${escapeHtml(item.mood)}</span>` : ''}
            ${people ? `<span class="tag people">👥 ${escapeHtml(people)}</span>` : ''}
            ${events ? `<span class="tag events">📌 ${escapeHtml(events)}</span>` : ''}
            ${followUps ? `<span class="tag followups">⏰ ${escapeHtml(followUps)}</span>` : ''}
            ${weatherChip(item.weather)}
          </div>
          <div class="entry-actions">
            <button class="edit-btn" data-id="${item.id}">✏️ Edit</button>
            <button class="delete-btn danger" data-id="${item.id}">🗑️ Delete</button>
          </div>
        </div>
      </div>
    `;
  }

  // ---- Edit / Delete handlers (Phase 3.6) ---------------------------------
  // Delegated click handler (the entry list is re-rendered on every change).
  diaryList.addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (!id) return;
    if (btn.classList.contains('edit-btn')) {
      enterEditMode(id, btn);
    } else if (btn.classList.contains('delete-btn')) {
      await deleteDiaryEntry(id);
    }
  });

  function enterEditMode(id, btn) {
    const entryEl = btn.closest('.diary-entry');
    if (!entryEl) return;
    const textEl = entryEl.querySelector('.diary-entry-text');
    const actionsEl = entryEl.querySelector('.entry-actions');
    if (!textEl || !actionsEl || entryEl.querySelector('.entry-edit-area')) return;

    const original = textEl.textContent;
    const ta = document.createElement('textarea');
    ta.className = 'entry-edit-area';
    ta.value = original;
    ta.rows = Math.max(3, Math.min(15, original.split('\n').length + 1));
    textEl.replaceWith(ta);

    const editActions = document.createElement('div');
    editActions.className = 'entry-edit-actions';
    const saveBtn = document.createElement('button');
    saveBtn.className = 'save';
    saveBtn.textContent = '💾 Save';
    const discardBtn = document.createElement('button');
    discardBtn.className = 'discard';
    discardBtn.textContent = '↶ Discard';
    editActions.append(saveBtn, discardBtn);
    actionsEl.replaceWith(editActions);

    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    saveBtn.addEventListener('click', async () => {
      const newText = ta.value;
      if (newText === original) { exitEditMode(entryEl, original, false); return; }
      saveBtn.disabled = true; saveBtn.textContent = '⏳ Saving...';
      try {
        const res = await fetch(`/api/diary/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ raw_text: newText }),
        });
        const body = await res.json();
        if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
        // Update the cached item so the next render shows the new text.
        const idx = diaryCache.findIndex(i => i.id === id);
        if (idx >= 0) diaryCache[idx] = body.data;
        exitEditMode(entryEl, newText, false);
      } catch (err) {
        saveBtn.disabled = false; saveBtn.textContent = '💾 Save';
        alert(`Save failed: ${err.message}`);
      }
    });
    discardBtn.addEventListener('click', () => exitEditMode(entryEl, original, true));
  }

  function exitEditMode(entryEl, text, _discarded) {
    // Restore the .diary-entry-text paragraph
    const ta = entryEl.querySelector('.entry-edit-area');
    const editActions = entryEl.querySelector('.entry-edit-actions');
    if (ta) {
      const p = document.createElement('p');
      p.className = 'diary-entry-text';
      p.textContent = text;
      ta.replaceWith(p);
    }
    if (editActions) {
      const actions = document.createElement('div');
      actions.className = 'entry-actions';
      const id = entryEl.dataset.id;
      const editBtn = document.createElement('button');
      editBtn.className = 'edit-btn';
      editBtn.dataset.id = id;
      editBtn.textContent = '✏️ Edit';
      const delBtn = document.createElement('button');
      delBtn.className = 'delete-btn danger';
      delBtn.dataset.id = id;
      delBtn.textContent = '🗑️ Delete';
      actions.append(editBtn, delBtn);
      editActions.replaceWith(actions);
    }
  }

  async function deleteDiaryEntry(id) {
    if (!confirm(`Delete entry #${id}? This cannot be undone.`)) return;
    try {
      const res = await fetch(`/api/diary/${id}`, { method: 'DELETE' });
      const body = await res.json();
      if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
      diaryCache = diaryCache.filter(i => i.id !== id);
      renderDiary(diaryCache);
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
  }

  diaryRefresh.addEventListener('click', loadDiary);
  diarySearch.addEventListener('input', () => renderDiary(diaryCache));

  // ---- Export to Markdown (Phase 3.5) -------------------------------------
  function pad2(n) { return String(n).padStart(2, '0'); }
  function mdDate(d) { return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}`; }
  function mdDateTime(s) { return s ? s.replace(' ', 'T') : ''; }
  function mdEscape(s) {
    return String(s || '').replace(/[\\`*_{}\[\]()#+\-.!]/g, c => '\\' + c);
  }

  function buildMarkdown(items) {
    const groups = new Map();
    for (const item of items) {
      const d = parseDate(item.created_at);
      if (!d) continue;
      const k = dayKey(d);
      if (!groups.has(k)) groups.set(k, { date: d, items: [] });
      groups.get(k).items.push({ item, date: d });
    }
    const sortedDays = [...groups.values()].sort((a, b) => b.date - a.date);
    const total = items.length;
    const now = new Date();

    const lines = [];
    lines.push('# Voice Diary Export');
    lines.push('');
    lines.push(`> Exported ${mdDate(now)} · ${total} entries`);
    lines.push('');

    for (const g of sortedDays) {
      const weekday = ['日', '一', '二', '三', '四', '五', '六'][g.date.getDay()];
      lines.push(`## ${mdDate(g.date)} (周${weekday})`);
      lines.push('');

      for (const { item, date } of g.items) {
        const time = formatTime(date);
        const mood = item.mood ? ` · ${item.mood}` : '';
        const w = item.weather;
        const weather = w ? ` · ${w.emoji || ''} ${w.condition || ''} ${w.temp_c != null ? Math.round(w.temp_c) + '°C' : ''}` : '';
        lines.push(`### ${time} · #${item.id}${mood}${weather}`);
        lines.push('');
        if (item.summary) {
          lines.push(`> ${item.summary}`);
          lines.push('');
        }
        const meta = [];
        if ((item.people || []).length) meta.push(`**人物**: ${item.people.join('、')}`);
        if ((item.events || []).length) meta.push(`**事件**: ${item.events.map(e => `${e.description}${e.time_anchor ? ' (' + e.time_anchor + ')' : ''}`).join('；')}`);
        if ((item.follow_ups || []).length) meta.push(`**后续**: ${item.follow_ups.map(f => `${f.description}${f.due ? ' (' + f.due + ')' : ''}`).join('；')}`);
        if (meta.length) {
          for (const m of meta) lines.push(m);
          lines.push('');
        }
        lines.push(item.raw_text);
        lines.push('');
        lines.push('---');
        lines.push('');
      }
    }
    return lines.join('\n');
  }

  diaryExport.addEventListener('click', () => {
    if (!diaryCache.length) {
      alert('No entries to export.');
      return;
    }
    const md = buildMarkdown(diaryCache);
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `voice-diary-${mdDate(new Date())}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  // ---- Import from Markdown (Phase 3.5) -----------------------------------
  // Expected format: a `voice-diary-*.md` file produced by Export.
  // Tolerant parser: extracts `## YYYY-MM-DD` day groups and `### HH:MM · #N`
  // entry headers, then the body until the next `---` separator.
  function parseMarkdown(md) {
    const entries = [];
    const lines = md.split(/\r?\n/);
    let i = 0;
    let currentDay = null;
    let currentEntry = null;

    function commit() {
      if (currentEntry && currentEntry.raw_text) {
        entries.push(currentEntry);
      }
      currentEntry = null;
    }

    while (i < lines.length) {
      const line = lines[i];

      // Day header
      const dayMatch = line.match(/^##\s+(\d{4}-\d{2}-\d{2})/);
      if (dayMatch) {
        commit();
        currentDay = dayMatch[1];
        i++; continue;
      }
      // Entry header
      const entryMatch = line.match(/^###\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+·\s+#?(\d+)?\s*(?:·\s*(.+))?$/);
      if (entryMatch) {
        commit();
        const time = entryMatch[1];
        const id = entryMatch[2] ? Number(entryMatch[2]) : undefined;
        const meta = entryMatch[3] || '';
        // Try to extract mood and weather from the rest
        const moodMatch = meta.match(/^([^·]+?)(?:\s+·\s+(☀|⛅|☁|🌧|🌨|⛈|🌫|🌦|🌤).*)?$/);
        currentEntry = {
          created_at: currentDay && time ? `${currentDay} ${time.length === 5 ? time + ':00' : time}` : undefined,
          _id: id,
          raw_text: '',
          mood: null,
          summary: null,
          events: [],
          people: [],
          follow_ups: [],
        };
        // Very loose mood extraction
        const m = meta.match(/([一-鿿]{2,4})/);
        if (m && !['人物','事件','后续'].includes(m[1])) currentEntry.mood = m[1];
        i++; continue;
      }
      // Summary quote
      if (line.startsWith('> ') && currentEntry) {
        if (!currentEntry.summary) {
          currentEntry.summary = line.slice(2).trim();
        } else {
          // continuation of the body
          currentEntry.raw_text = (currentEntry.raw_text + '\n' + line).trim();
        }
        i++; continue;
      }
      // Meta lines like **人物**: 老王
      const metaMatch = line.match(/^\*\*([^*]+)\*\*:\s*(.+)$/);
      if (metaMatch && currentEntry) {
        const key = metaMatch[1].trim();
        const val = metaMatch[2].trim();
        if (key === '人物') currentEntry.people = val.split(/[、,，]/).map(s => s.trim()).filter(Boolean);
        else if (key === '事件') currentEntry.events = [{ description: val }];
        else if (key === '后续') currentEntry.follow_ups = [{ description: val }];
        i++; continue;
      }
      // Separator
      if (/^---+$/.test(line)) {
        i++; continue;
      }
      // Body text
      if (currentEntry) {
        if (line.trim() === '') {
          // blank line: preserve as paragraph break
          if (currentEntry.raw_text) currentEntry.raw_text += '\n';
        } else {
          currentEntry.raw_text = currentEntry.raw_text
            ? currentEntry.raw_text + '\n' + line
            : line;
        }
      }
      i++;
    }
    commit();
    return entries;
  }

  diaryImportBtn.addEventListener('click', () => diaryImportFile.click());
  diaryImportFile.addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = parseMarkdown(text);
      if (!parsed.length) {
        alert('No entries found in the file. Is it a valid export?');
        return;
      }
      const res = await fetch('/api/diary/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entries: parsed }),
      });
      const body = await res.json();
      if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
      alert(`Imported ${body.data.inserted} entries.`);
      await loadDiary();
    } catch (err) {
      alert(`Import failed: ${err.message}`);
    } finally {
      e.target.value = '';  // allow re-importing the same file
    }
  });

  // ==========================================================================
  // Chat mode (Phase 2 + Phase 3 RAG)
  // ==========================================================================
  const chatNew = $('chat-new');
  const chatEnd = $('chat-end');
  const chatStatus = $('chat-status');
  const chatThread = $('chat-thread');
  const chatInput = $('chat-input');
  const chatSend = $('chat-send');
  const chatMic = $('chat-mic');
  const memoryToggle = $('memory-mode');
  const extractedPanel = $('extracted-panel');
  const extractedFields = $('extracted-fields');

  let currentConvId = null;
  let isStreaming = false;
  let chatRecognition = null;
  let chatRecording = false;

  function setupChatRecognition() {
    if (!SpeechRecognition) { chatMic.disabled = true; return; }
    chatRecognition = new SpeechRecognition();
    chatRecognition.continuous = true;
    chatRecognition.interimResults = true;
    chatRecognition.lang = currentLang();
    chatRecognition.onresult = (event) => {
      let finalText = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) finalText += event.results[i][0].transcript;
      }
      if (finalText) {
        chatInput.value = (chatInput.value.trim() + ' ' + finalText.trim()).trim();
        autogrow(chatInput);
      }
    };
    chatRecognition.onerror = () => stopChatRecording();
    chatRecognition.onend = () => { chatRecording = false; chatMic.classList.remove('recording'); };
    chatMic.disabled = false;
  }
  setupChatRecognition();

  function startChatRecording() {
    if (!chatRecognition) return;
    try { chatRecognition.start(); chatRecording = true; chatMic.classList.add('recording'); }
    catch (_) {}
  }
  function stopChatRecording() {
    if (!chatRecognition) return;
    try { chatRecognition.stop(); } catch (_) {}
    chatRecording = false;
    chatMic.classList.remove('recording');
  }
  chatMic.addEventListener('click', () => (chatRecording ? stopChatRecording() : startChatRecording()));

  // Auto-grow chat input
  chatInput.addEventListener('input', () => autogrow(chatInput));
  autogrow(chatInput);

  memoryToggle.addEventListener('change', async () => {
    if (!currentConvId) return;
    try {
      const res = await fetch(`/api/chat/${currentConvId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ memory_mode: memoryToggle.checked }),
      });
      const body = await res.json();
      if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
      setText(chatStatus, memoryToggle.checked ? '🧠 Memory mode ON — assistant can recall past entries' : `Active conversation • ${currentConvId.slice(0, 8)}…`);
    } catch (e) {
      setText(chatStatus, `Toggle failed: ${e.message}`);
      memoryToggle.checked = !memoryToggle.checked;
    }
  });

  function renderThread(messages) {
    chatThread.innerHTML = '';
    if (!messages || !messages.length) {
      const p = document.createElement('p');
      p.className = 'chat-placeholder';
      p.textContent = 'Click 🆕 New to start a conversation.';
      chatThread.appendChild(p);
      return;
    }
    for (const m of messages) appendBubble(m.role, m.content);
    scrollThreadToBottom();
  }

  function appendBubble(role, content) {
    const wrapper = document.createElement('div');
    wrapper.className = `bubble bubble-${role}`;
    const text = document.createElement('div');
    text.className = 'bubble-text';
    text.textContent = content;
    wrapper.appendChild(text);
    chatThread.appendChild(wrapper);
    scrollThreadToBottom();
    return text;
  }

  function scrollThreadToBottom() { chatThread.scrollTop = chatThread.scrollHeight; }

  function setChatState(convId) {
    currentConvId = convId;
    const has = !!convId;
    chatEnd.disabled = !has || isStreaming;
    chatSend.disabled = !has || isStreaming;
    chatInput.disabled = !has || isStreaming;
    chatMic.disabled = !has || !chatRecognition || isStreaming;
    if (has) {
      setText(chatStatus, `Active conversation • ${convId.slice(0, 8)}…`);
    } else {
      setText(chatStatus, 'No active conversation');
      memoryToggle.checked = false;
    }
  }

  chatNew.addEventListener('click', async () => {
    chatNew.disabled = true;
    try {
      const res = await fetch('/api/chat', { method: 'POST' });
      const body = await res.json();
      if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
      setChatState(body.data.conversation_id);
      memoryToggle.checked = !!body.data.memory_mode;
      renderThread([{ role: 'assistant', content: body.data.welcome_message }]);
      extractedPanel.hidden = true;
    } catch (e) {
      setText(chatStatus, `Failed to start: ${e.message}`);
    } finally {
      chatNew.disabled = false;
    }
  });

  chatSend.addEventListener('click', () => sendChatMessage());
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
  });

  async function sendChatMessage() {
    if (isStreaming) return;
    const text = chatInput.value.trim();
    if (!text || !currentConvId) return;
    isStreaming = true;
    setChatState(currentConvId);
    appendBubble('user', text);
    chatInput.value = '';
    autogrow(chatInput);
    const assistantEl = appendBubble('assistant', '');
    assistantEl.classList.add('streaming');

    try {
      const res = await fetch(`/api/chat/${currentConvId}/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text }),
      });
      if (!res.ok || !res.body) {
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.error?.message || `HTTP ${res.status}`);
      }
      await consumeSSE(res.body, {
        onDelta: (delta) => { assistantEl.textContent += delta; scrollThreadToBottom(); },
        onError: (errMsg) => {
          assistantEl.textContent = `[Error: ${errMsg}]`;
          assistantEl.classList.remove('streaming');
          assistantEl.classList.add('error');
        },
        onDone: () => assistantEl.classList.remove('streaming'),
      });
    } catch (e) {
      assistantEl.textContent = `[Error: ${e.message}]`;
      assistantEl.classList.remove('streaming');
      assistantEl.classList.add('error');
    } finally {
      isStreaming = false;
      setChatState(currentConvId);
    }
  }

  async function consumeSSE(body, { onDelta, onError, onDone }) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = 'message';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '');
        if (line.startsWith('event: ')) currentEvent = line.slice(7).trim();
        else if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (currentEvent === 'error') {
            try { onError(JSON.parse(data).error || 'unknown'); } catch (_) { onError(data); }
          } else if (currentEvent === 'done') {
            onDone();
          } else {
            try {
              const parsed = JSON.parse(data);
              if (parsed.delta) onDelta(parsed.delta);
            } catch (_) {}
          }
          currentEvent = 'message';
        }
      }
    }
  }

  chatEnd.addEventListener('click', async () => {
    if (!currentConvId || isStreaming) return;
    chatEnd.disabled = true;
    setText(chatStatus, 'Extracting & saving...');
    try {
      const res = await fetch(`/api/chat/${currentConvId}/end`, { method: 'POST' });
      const body = await res.json();
      if (!res.ok || body.error) throw new Error(body.error?.message || `HTTP ${res.status}`);
      showExtracted(body.data);
      // Refresh weather in background (best-effort) and re-save
      const weather = await getWeather();
      if (weather) {
        try {
          await fetch(`/api/diary/${body.data.diary_entry_id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ weather }),
          });
        } catch (_) { /* PATCH endpoint may not exist; ignore */ }
      }
      setText(chatStatus, `Saved as diary entry #${body.data.diary_entry_id}.`);
      currentConvId = null;
      setChatState(null);
      // refresh history if visible
      loadHistory();
    } catch (e) {
      setText(chatStatus, `Save failed: ${e.message}`);
      chatEnd.disabled = false;
    }
  });

  function showExtracted(d) {
    extractedFields.innerHTML = '';
    const rows = [
      ['Summary', d.summary],
      ['Mood', d.mood],
      ['People', (d.people || []).join(', ')],
      ['Events', (d.events || []).map(e => `${e.description}${e.time_anchor ? ' (' + e.time_anchor + ')' : ''}`).join('; ')],
      ['Follow-ups', (d.follow_ups || []).map(f => `${f.description}${f.due ? ' (' + f.due + ')' : ''}`).join('; ')],
    ];
    for (const [k, v] of rows) {
      if (!v) continue;
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v;
      extractedFields.append(dt, dd);
    }
    extractedPanel.hidden = false;
  }

  // ---- Initial load -------------------------------------------------------
  loadHistory();
  setMode('quick');
})();
