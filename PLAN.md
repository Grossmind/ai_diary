# 个人 AI 语音日记 — 项目计划

> 状态：v0.3 草案
> 最后更新：2026-06-03
>
> **修订记录**
> - v0.3 (2026-06-03): LLM 从 MiniMax 切换到 DeepSeek（MiniMax 计划不包含对话模型；DeepSeek OpenAI 兼容，免费额度更友好）
> - v0.2 (2026-06-01): 初稿

---

## 1. 背景 & 目标

### 背景
- 用户希望做一个**自用的语音日记工具**
- 核心交互：用户对工具说话 → 工具记录 → 形成可检索的日记
- 后期能力：基于日记帮用户回忆 + 主动提示

### 目标（MVP 范围）
- ✅ 24/7 可用的语音录入（手机 + 电脑）
- ✅ 多轮对话式记录（不只是单次录音转写）
- ✅ 自动结构化提取（事件 / 人物 / 情绪 / 任务）
- ✅ 基于 RAG 的回忆 / 问答
- ✅ 主动提示（去年今天、长期未跟进事项等）

### 非目标（先不做）
- ❌ 多用户 / 权限
- ❌ 原生 App（先用 PWA）
- ❌ 公开分享 / 社交
- ❌ 图片 / 视频日记（先文字 + 音频）

---

## 2. 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 前端 | HTML + 原生 JS + PWA | 轻量、跨端、可装到手机 |
| 后端 | Python 3.11 + FastAPI | AI 生态最好、流式响应方便 |
| 数据库 | SQLite | 单用户、零配置、文件级 |
| 向量库 | ChromaDB | 轻量、Python 集成好、文件持久化 |
| STT | 浏览器 Web Speech API | 零成本、Chrome/Edge 原生 |
| TTS | 浏览器 SpeechSynthesis API | 可选，给 AI 配个声 |
| LLM | DeepSeek API | OpenAI 兼容，便宜/稳定，用户已定（v0.3 从 MiniMax 切换） |
| 容器化 | Docker | 部署可移植 |
| 部署目标 | 黑群晖 VM（Docker） | 24/7、群晖自带的 Container Manager |
| 内网穿透 | Cloudflare Tunnel | 免费、稳定、自动 HTTPS |
| 唤醒保持 | Windows 任务计划 | 防止 Windows 休眠影响底层 |

---

## 3. 架构总览

```
┌──────────────────────────────────────────┐
│   浏览器（PWA, 手机 / 电脑）              │
│   - MediaRecorder + Web Speech API       │
│   - SSE 接收 LLM 流式回复                 │
│   - Web Push 接收主动提示                  │
└────────────────┬─────────────────────────┘
                 │ HTTPS (Cloudflare Tunnel)
┌────────────────▼─────────────────────────┐
│   FastAPI (Docker, 黑群晖 VM)             │
│   - POST /api/chat      (SSE 流式对话)    │
│   - POST /api/diary     (写入日记)        │
│   - GET  /api/diary     (列表 / 详情)     │
│   - POST /api/recall    (RAG 回忆)        │
│   - POST /api/extract   (结构化提取)      │
│   - Cron jobs (主动提示)                  │
└────┬──────────────────┬──────────────┬────┘
     │                  │              │
┌────▼─────┐  ┌────────▼────────┐  ┌──▼──────────┐
│  SQLite  │  │   ChromaDB      │  │  DeepSeek API│
│  日记库  │  │   向量索引       │  │             │
└──────────┘  └─────────────────┘  └─────────────┘
                       │
                       ▼
                ┌──────────────┐
                │  群晖共享卷   │  ← 快照 / 备份
                └──────────────┘
```

---

## 4. 部署方案

### 推荐：黑群晖 VM（Docker）

**为什么不上 Windows 直装：**
- Windows 会休眠 → 服务停
- Windows 系统升级 / 重启会打断服务
- 群晖本身就是 7x24 定位，自带 Container Manager（Docker 套件）

**部署步骤：**
1. 群晖系统 → 打开 Container Manager（Docker）
2. 创建项目文件夹 `/volume1/docker/diary/`
3. 在该目录下放：
   - `docker-compose.yml`
   - `app/`（FastAPI 代码）
   - 挂载 `data/`（SQLite + ChromaDB 持久化）
4. Container Manager 启动 `diary-app` 容器
5. 同台机器或路由器上跑 `cloudflared` 容器，建 tunnel
6. 域名托管在 Cloudflare（free tier 够用）
7. Windows 任务计划：禁止休眠（防止虚机挂掉）

### 网络 & 访问

- **内网**：直接通过 `http://<群晖IP>:8000` 访问
- **外网**：通过 Cloudflare Tunnel，`https://diary.yourdomain.com`
- 电信 NAT 没公网 IP → Tunnel 是唯一稳的方案

---

## 5. 里程碑 / 阶段

### Phase 0 — 环境准备（0.5 天）
- [ ] 装 Python 3.11、Node 20（开发机）
- [ ] 申请 MiniMax API key，验证连通性
- [ ] 群晖开 SSH、装 Container Manager
- [ ] 申请 / 托管一个域名到 Cloudflare
- [ ] 在群晖建好 `/volume1/docker/diary/` 目录

### Phase 1 — MVP：录 → 转 → 存（1.5 天）
- [ ] FastAPI 项目骨架
- [ ] 端点：`POST /api/diary { text }`
- [ ] 前端：mic 按钮 + Web Speech API + 文字确认
- [ ] SQLite 建表 `diary_entries`
- [ ] 历史列表页（最简）
- [ ] 本地能跑通完整链路

**验证**：浏览器打开 → 按 mic → 说话 → 文字出现 → 提交 → 重开能看到

### Phase 2 — 对话 + 结构化提取（2 天）
- [ ] 封装 MiniMax API 客户端（带重试、流式）
- [ ] 多轮对话（带 history 的 chat）
- [ ] SSE 流式输出
- [ ] 结束对话时调用 LLM 提取：
  - 事件摘要
  - 涉及人物
  - 情绪标签
  - 时间锚点
  - 后续事项（"下周要..."）
- [ ] DiaryEntry 表结构升级
- [ ] 前端：聊天式 UI（消息气泡 + 录音按钮）

**验证**：完整对话流程跑通，存进去的日记有结构化字段

### Phase 3 — 回忆 / RAG（1.5 天）
- [ ] ChromaDB 集成
- [ ] 每次新日记写入 → 自动向量化 + 入库
- [ ] `POST /api/recall { query }` 端点
- [ ] 召回 top-k → LLM 生成答案 + 引用
- [ ] 前端：搜索框 + 答案卡片

**验证**：问"上周跟谁吃饭了"，能给出准确回答 + 引用原文

### Phase 4 — 主动提示（1 天）
- [ ] 后台 cron 任务（每日 8:00、20:00）
- [ ] 触发逻辑：
  - 去年今日
  - 30 天 / 90 天前
  - 长期未提的人物
  - 日记中"下周要..."的事项 → 到时间没跟进
- [ ] 浏览器 Web Push 通知
- [ ] 通知中心页面（看历史推送）

**验证**：注册 push 一次后，能在第二天早上收到"去年今天你..."推送

### Phase 5 — 部署上线（1 天）
- [ ] 写 Dockerfile + docker-compose.yml
- [ ] 群晖 Container Manager 跑起来
- [ ] 配 Cloudflare Tunnel
- [ ] 域名 + HTTPS 验证
- [ ] PWA manifest + icons
- [ ] Windows 任务计划防休眠
- [ ] 写一个最简 README（启动 / 重启 / 备份命令）

**验证**：手机装 PWA → 在外面 4G 打开 → 能用 → 重启群晖后服务自动恢复

### Phase 6 — 打磨（持续）
- 移动端 UX 优化
- 文本输入兜底
- 导出 / 备份按钮
- 年终回顾 / 数据可视化
- 后期可考虑换 Whisper local 提升 STT 质量

**总时间估算**：7-8 天到 Phase 5 可用版

---

## 6. 关键决策（动手前要敲定）

| # | 决策项 | 我的推荐 | 需要你确认 |
|---|---|---|---|
| 1 | 部署到 Windows 还是群晖 VM？ | 群晖 VM | ☐ |
| 2 | 群晖 Container Manager 是否已装好？ | — | ☐ |
| 3 | 域名用谁的？愿意放 Cloudflare 吗？ | 用 Cloudflare 托管的二级域名 | ☐ |
| 4 | 内网穿透用 Cloudflare Tunnel？ | 是（免费 + 稳 + 自动 HTTPS） | ☐ |
| 5 | LLM 数据走 MiniMax API 是否有合规顾虑？ | 个人用应该 OK | ☐ |
| 6 | 是否需要多设备同步 / 多用户？ | 先单用户 | ☐ |

---

## 7. 风险 & 对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Windows 休眠 → VM 跟着挂 | 服务停 | Windows 任务计划禁休眠 + 群晖本身较稳 |
| 电信 NAT 无公网 | 外网访问不到 | Cloudflare Tunnel |
| Web Speech API 精度差 | 录入体验差 | 后期换 Whisper local；先做兜底可改文字 |
| MiniMax API 限流 / 费用 | 不可用 | 本地缓存 + 兜底静态回复 |
| SQLite 损坏 | 数据丢失 | 群晖快照 + 定期 JSON 导出 |
| 单点故障（机器挂） | 整个停 | 文档化恢复流程 + 数据易迁移 |

---

## 8. 数据模型（草案）

```sql
-- 主体日记表
CREATE TABLE diary_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  conversation JSON,           -- 完整对话历史 [{role, content, ts}]
  summary TEXT,                -- LLM 总结的一句话
  mood TEXT,                   -- 情绪标签
  events JSON,                 -- 提取出的事件列表
  people JSON,                 -- 涉及人物
  follow_ups JSON,             -- 后续事项
  audio_url TEXT,              -- 原始录音（可选）
  raw_text TEXT                -- 原始转写文本
);

-- 人物库（用于"长期未提"提醒）
CREATE TABLE people (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE,
  first_seen TIMESTAMP,
  last_seen TIMESTAMP,
  mention_count INTEGER DEFAULT 0
);

-- 主动提示记录
CREATE TABLE notifications (
  id INTEGER PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  type TEXT,                   -- 'anniversary' / 'overdue' / 'forgotten' ...
  payload JSON,
  delivered BOOLEAN DEFAULT 0
);
```

---

## 9. 验证标准（Acceptance）

### Phase 1（核心链路）
- [ ] 浏览器按 mic → 说话 → 文字出现
- [ ] 提交后 SQLite 能查到
- [ ] 重开能看到历史

### Phase 5（可用版）
- [ ] 完整对话流程跑通
- [ ] 搜索"上周跟谁吃饭"有答案
- [ ] 早晨能收到去年今日推送
- [ ] 手机 PWA 可装可用
- [ ] 重启群晖服务自动恢复
- [ ] HTTPS 证书有效

---

## 10. 下一步

1. 你过一遍这份计划，提意见
2. 回答第 6 节的 6 个决策项
3. 我开始 Phase 0 + Phase 1（环境 + MVP 链路）
