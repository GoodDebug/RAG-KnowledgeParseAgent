// 小说解构流水线 API 封装（前端 P0）—— 全部 Bearer，消费后端 /api/novel/*
// 复用 api/index.js 的 authHeaders/BASE 范式；SSE 照 chatStream 用 fetch+ReadableStream（可带 Bearer）。
import { BASE, authHeaders } from './index'

// 统一 JSON 解析：非 2xx 时优先取后端 detail（404 无章节 / 409 running 的提示文案），否则回退 HTTP 码。
async function _json(res) {
  if (!res.ok) {
    let detail = ''
    try { detail = (await res.json()).detail || '' } catch { /* 非 JSON 错误体 */ }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ---------------- 解构任务 ----------------

// 任务列表（最新在前）：[{job_id, trigger_type, status, total_chapters, done_chapters, failed_chapters, started_at, finished_at}]
export async function listJobs(bookId) {
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/jobs`, { headers: authHeaders() }))
}

// 任务详情（含 chapters[]：chapter_id/chapter_index/chapter_title/status/retry_count/shrink_level/error_msg）
export async function getJob(jobId) {
  return _json(await fetch(`${BASE}/api/novel/jobs/${jobId}`, { headers: authHeaders() }))
}

// 一键解构（重新解构已有 novel_chapter 的书）：202 {job_id,...}；404 无章节 / 409 running
export async function deconstruct(bookId) {
  const res = await fetch(`${BASE}/api/novel/books/${bookId}/deconstruct`, {
    method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
  })
  return _json(res)
}

// 重试失败章：{chapter_ids?} → 202 {retry_chapters:n}
export async function retry(jobId, chapterIds = null) {
  const res = await fetch(`${BASE}/api/novel/jobs/${jobId}/retry`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ chapter_ids: chapterIds }),
  })
  return _json(res)
}

// 复核待办：{pending:[...], summary:{pending_total, by_type_severity}}
export async function listValidation(bookId) {
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/validation`, { headers: authHeaders() }))
}

// ---------------- 人工复核（P1：confirm/ignore/fix/repersist） ----------------

// 裁决通过：仅 pending → confirmed；200 {issue_id, status:"confirmed"}；409 非法迁移
export async function confirmIssue(issueId) {
  return _json(await fetch(`${BASE}/api/novel/validation/${issueId}/confirm`, { method: 'POST', headers: authHeaders() }))
}

// 裁决忽略：pending/confirmed → ignored；200 {issue_id, status:"ignored"}；409
export async function ignoreIssue(issueId) {
  return _json(await fetch(`${BASE}/api/novel/validation/${issueId}/ignore`, { method: 'POST', headers: authHeaders() }))
}

// 修正重写：body {corrected_value}(JSON 字符串，可 null→用 suggested_value) → 200 {issue_id, status:"fixed"}；409
export async function fixIssue(issueId, correctedValue = null) {
  const res = await fetch(`${BASE}/api/novel/validation/${issueId}/fix`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ corrected_value: correctedValue }),
  })
  return _json(res)
}

// 批量 re-persist：{issue_ids:[...]} → 200 {total, succeeded}（逐条静默计数）
export async function repersistBook(bookId, issueIds = []) {
  const res = await fetch(`${BASE}/api/novel/books/${bookId}/validation/repersist`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ issue_ids: issueIds }),
  })
  return _json(res)
}

// 疑点原文证据（P2-1 复核分屏）：GET → {chapter_index, chapter_title, text, char_start, char_end, matched_terms[]}
// 命中返回 evidence 对象；无证据 → {evidence:null}
export async function getIssueEvidence(bookId, issueId) {
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/validation/${issueId}/evidence`, { headers: authHeaders() }))
}

// 批量确认（P2-1 一键确认低风险）：{issue_ids:[...]} → 200 {total, succeeded, failed[]}（逐条 confirm + 写回，单事务）
export async function confirmIssues(bookId, issueIds = []) {
  const res = await fetch(`${BASE}/api/novel/books/${bookId}/validation/confirm`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ issue_ids: issueIds }),
  })
  return _json(res)
}

// 结构化查询（图谱/百科）：params = {entity, chapter, chapter_start, chapter_end, events}
// → {snapshots:[...], relations:[...](含 source_name/target_name), events:[...]}（按参数返回）
export async function queryBook(bookId, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString()
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/query${qs ? `?${qs}` : ''}`, { headers: authHeaders() }))
}

// 知识库浏览（P1 补强）：type ∈ entity|entity_snapshot|relation|timeline_event|location|
// foreshadowing|conflict|rule|alias|validation；params = 各类型过滤字段 + limit + offset
// → {total, items}；relation 含 source_name/target_name，validation 含 chapter_title
export async function browseBook(bookId, type, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString()
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/browse/${type}${qs ? `?${qs}` : ''}`, { headers: authHeaders() }))
}

// ======================= 大修002 · Knowledge API（共享查询层，对齐后端 /knowledge/*） =======================

// 构建 knowledge 查询串：camelCase 入参 → 后端 snake_case query（空值剔除）
function _kq(params = {}) {
  const map = { entityId: 'entity_id', chapter: 'chapter', chapterStart: 'chapter_start', chapterEnd: 'chapter_end' }
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') qs.append(map[k] || k, v)
  }
  return qs.toString()
}

export async function listBookChapters(bookId) {
  // 章节列表（章节滑块 max/标题用；后端 novel.py:82）
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/chapters`, { headers: authHeaders() }))
}

export async function getKnowledgeGraph(bookId, { entityId, chapter } = {}) {
  // 图谱 1-hop + 时态：{center, nodes, edges}（relation as-of chapter）
  const qs = _kq({ entityId, chapter })
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/knowledge/graph${qs ? `?${qs}` : ''}`, { headers: authHeaders() }))
}

export async function getEntityCard(bookId, entityId, { chapter } = {}) {
  // 实体卡（时态 as-of chapter）：旧键 name/type/aliases/status/relations/events/evidence/confidence/review_status
  // + 二阶段 04 L0-L4 五键：L0_identity（身份锚点）/L1_baseline（基线）/L2_snapshot（当前状态+回填）/
  //   L3_arc（弧光：snapshots/events/relation_evolution/foreshadowing_line）/L4_narrative（伏笔·规则·明暗·叙事类型）
  const qs = _kq({ chapter })
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/knowledge/entities/${entityId}${qs ? `?${qs}` : ''}`, { headers: authHeaders() }))
}

export async function getTimeline(bookId, { chapterStart, chapterEnd } = {}) {
  // 时间线事件（章节区间，含 involved_entities）
  const qs = _kq({ chapterStart, chapterEnd })
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/knowledge/timeline${qs ? `?${qs}` : ''}`, { headers: authHeaders() }))
}

export async function getEvidence(bookId, entityId, { chapter } = {}) {
  // 实体原文证据（指定章含实体名/别名窗口片段）
  const qs = _kq({ chapter })
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/knowledge/entities/${entityId}/evidence${qs ? `?${qs}` : ''}`, { headers: authHeaders() }))
}

export async function getSnapshots(bookId, entityId, { chapterStart, chapterEnd } = {}) {
  // 实体快照演化（章节区间）
  const qs = _kq({ chapterStart, chapterEnd })
  return _json(await fetch(`${BASE}/api/novel/books/${bookId}/knowledge/entities/${entityId}/snapshots${qs ? `?${qs}` : ''}`, { headers: authHeaders() }))
}

// ---------------- SSE 进度流 ----------------

// 订阅任务 SSE：fetch+ReadableStream（原生 EventSource 无法带 Authorization）。
// onEvent(parsedEvent)：job_started/chapter_*/agent_*/progress/job_done/job_failed；无 type 裸事件忽略。
// 返回 {close()} 用于 AbortController 取消。
export function novelStream(jobId, onEvent) {
  const controller = new AbortController()
  const handle = { close: () => controller.abort() }
  ;(async () => {
    try {
      const res = await fetch(`${BASE}/api/novel/jobs/${jobId}/stream`, {
        headers: authHeaders(), signal: controller.signal,
      })
      if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let idx
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data: ')) continue
            try {
              const ev = JSON.parse(line.slice(6))
              if (ev && ev.type) onEvent(ev)   // 无 type 裸事件忽略（后端会发 chapter_results 裸事件）
            } catch { /* 脏帧忽略 */ }
          }
          await new Promise(r => setTimeout(r, 0))   // 让出 macrotask，逐帧渲染（chatStream 同款修复）
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') onEvent({ type: 'stream_error', error: String(err) })
    }
  })()
  return handle
}
