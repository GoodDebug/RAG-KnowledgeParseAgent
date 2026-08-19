export const BASE = ''

// 统一 Bearer 头：token 只存 localStorage（Spec-D 决策 7，禁止 URL/代码/日志暴露）
export function authHeaders() {
  const token = localStorage.getItem('token') || ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// ======================= 认证（Spec-D 最小登录表单；正式登录页留 Spec-E） =======================

export async function login(account, password) {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account, password }),
  })
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || `登录失败 HTTP ${res.status}`)
  return res.json()
}

export async function register(payload) {
  const res = await fetch(`${BASE}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || `注册失败 HTTP ${res.status}`)
  return res.json()
}

// ======================= 聊天接口 =======================

// fetch + ReadableStream 版 SSE：原生 EventSource 无法带 Authorization 头，
// 返回 {onmessage, onerror, close} 兼容对象（事件 data 与 EventSource 一致）。
export function chatStream(message, sessionId = 'default', useRag = true) {
  const url = `${BASE}/api/chat/stream?message=${encodeURIComponent(message)}&session_id=${sessionId}&use_rag=${useRag}`
  const controller = new AbortController()
  const stream = {
    onmessage: null,
    onerror: null,
    close() { controller.abort() },
  }
  ;(async () => {
    try {
      const res = await fetch(url, { headers: authHeaders(), signal: controller.signal })
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
            if (line.startsWith('data: ')) {
              if (stream.onmessage) {
                stream.onmessage({ data: line.slice(6) })
                // 关键修复：每帧让出 macrotask，浏览器在帧间有机会绘制、Vue 逐帧 flush，
                // 恢复逐帧流式（fetch-SSE microtask 续延会把同批帧合并成一次渲染）
                await new Promise(r => setTimeout(r, 0))
              }
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError' && stream.onerror) stream.onerror(err)
    }
  })()
  return stream
}

// 历史记录接口（Spec-D 扩展：返回 id/source_refs/feedback/feedback_text）

export async function chatHistory(sessionId = 'default') {
  const res = await fetch(`${BASE}/api/chat/history?session_id=${sessionId}`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// 反馈提交接口（Spec-D）

export async function postFeedback(messageId, feedback, feedbackText) {
  const res = await fetch(`${BASE}/api/messages/${messageId}/feedback`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback, feedback_text: feedbackText || null }),
  })
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || `HTTP ${res.status}`)
  return res.json()
}

// 会话模块（Spec-E）：历史会话列表 + 会话详情（含完整对话记录）

export async function listSessions() {
  const res = await fetch(`${BASE}/api/sessions`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function getSessionMessages(id) {
  const res = await fetch(`${BASE}/api/sessions/${id}/messages`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ======================= 上传接口 =======================

// 知识库管理（Spec-C /api/documents/*，Bearer）：正确路径（legacy /api/ingest/upload 已废弃）
// deconstruct（可选，默认 false）：勾选时 form 带 deconstruct=1，上传同时启动小说解构流水线
// （后端 _resolve_deconstruct 解析；向后兼容，既有调用不受影响）。
export async function uploadDocuments(bookName, files, deconstruct = false) {
  const form = new FormData()
  form.append('book_name', bookName)
  if (deconstruct) form.append('deconstruct', '1')
  for (const f of files) form.append('files', f, f.name)
  // 不手动设 Content-Type（让浏览器带 boundary）；headers 仅加 Bearer
  const res = await fetch(`${BASE}/api/documents/upload`, { method: 'POST', headers: authHeaders(), body: form })
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || `HTTP ${res.status}`)
  const data = await res.json()
  // 前端 P0：上传响应含 book_id → 缓存 bookIdMap[bookName]（novel 端点需它；与 books 接口 book_id 双保险）
  if (data && data.book_id) {
    const map = JSON.parse(localStorage.getItem('bookIdMap') || '{}')
    map[bookName] = data.book_id
    localStorage.setItem('bookIdMap', JSON.stringify(map))
  }
  return data
}

export async function listDocuments(bookName) {
  const qs = bookName ? `?book_name=${encodeURIComponent(bookName)}` : ''
  const res = await fetch(`${BASE}/api/documents${qs}`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// 书分组（左列套餐菜单）与按书删除（顶层计划外）
export async function listBookNames() {
  const res = await fetch(`${BASE}/api/documents/books`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function deleteBook(bookName) {
  const res = await fetch(`${BASE}/api/documents/books/${encodeURIComponent(bookName)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`)
  return res
}

// 单文件删除（DELETE /api/documents/{id} = 删该 document，顶层计划外）
export async function deleteDocument(id) {
  const res = await fetch(`${BASE}/api/documents/${id}`, { method: 'DELETE', headers: authHeaders() })
  if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`)
  return res
}

// legacy /api/ingest/upload 与 /api/ingest/books（旧前端兼容，Spec-E 迁移后不再使用）
export async function uploadFiles(bookName, files) {
  const form = new FormData()
  form.append('book_name', bookName)
  for (const f of files) form.append('files', f, f.name)
  const res = await fetch(`${BASE}/api/ingest/upload`, { method: 'POST', body: form })
  return res.json()
}

// 列表接口（legacy 兼容）

export async function listBooks() {
  const res = await fetch(`${BASE}/api/ingest/books`)
  return res.json()
}

// 小说爬虫接口

export async function novelChapters(novelUrl) {
  const res = await fetch(`${BASE}/api/crawler/novel/chapters`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ novel_url: novelUrl }),
  })
  return res.json()
}

export async function novelCrawl(chapters, baseUrl = '') {
  const res = await fetch(`${BASE}/api/crawler/novel/crawl`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chapters, base_url: baseUrl }),
  })
  return res.json()
}

// 爬虫接口

export async function crawlSingle(url, mode = 'dynamic') {
  const res = await fetch(`${BASE}/api/crawler/fetch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, mode }),
  })
  return res.json()
}

export async function crawlBatch(urls, mode = 'dynamic') {
  const res = await fetch(`${BASE}/api/crawler/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls, mode }),
  })
  return res.json()
}
