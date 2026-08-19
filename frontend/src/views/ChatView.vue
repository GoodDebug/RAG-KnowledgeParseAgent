<template>
  <div class="chat-layout">
    <!-- Spec-E：左列会话列表（历史会话 / 新建会话） -->
    <div class="session-side">
      <SessionList :sessions="sessions" :active-key="currentKey" @select="selectSession" @create="newSession" />
    </div>
    <!-- 右列对话区 -->
    <div class="chat-main">
      <div class="chat-header">
        <span class="chat-header-title">{{ currentTitle || '新会话' }}</span>
      </div>
      <MessageList
        :messages="messages"
        :loading="loading"
        :status-text="statusText"
        :elapsed="elapsed"
        @feedback="handleFeedback"
        @followup="handleFollowup"
      />
      <ChatInput @send="handleSend" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import SessionList from '../components/SessionList.vue'
import MessageList from '../components/MessageList.vue'
import ChatInput from '../components/ChatInput.vue'
import { chatStream, listSessions, getSessionMessages, postFeedback } from '../api'

const router = useRouter()
const sessions = ref([])          // 会话列表 [{id,title,created_at,key}]
const currentKey = ref('')        // 当前会话 key（前端透传 session_id）
const activeSessionId = ref(null) // 当前会话 DB id（null=尚未落库的新会话）
const currentTitle = ref('')      // 当前会话标题
const messages = ref([])
const loading = ref(false)
const statusText = ref('')  // 流水线进度文字（status 事件）
const elapsed = ref(0)      // 已等待秒数
const useRag = ref(true)    // 知识库开关（跟随 ChatInput 最新一次发送）
let statusTimer = null
let evtSource = null

function startStatusTimer() {
  elapsed.value = 0
  if (statusTimer) clearInterval(statusTimer)
  statusTimer = setInterval(() => { elapsed.value++ }, 1000)
}
function stopStatusTimer() {
  if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
  elapsed.value = 0
  statusText.value = ''
}

async function refreshSessions() {
  try {
    sessions.value = await listSessions()
  } catch (e) {
    console.warn('[ChatView] ⚠️ 会话列表加载失败', e)
  }
}

function newSessionKey() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return 's_' + Date.now() + '_' + Math.random().toString(36).slice(2)
}

function newSession() {
  currentKey.value = newSessionKey()
  activeSessionId.value = null
  currentTitle.value = '新会话'
  messages.value = []
  console.log('[ChatView] ✨ 新建会话 | key=%s', currentKey.value)
}

async function selectSession(key) {
  const row = sessions.value.find(s => s.key === key)
  if (!row) return
  currentKey.value = key
  activeSessionId.value = row.id
  currentTitle.value = row.title || '新会话'
  messages.value = []
  try {
    const history = await getSessionMessages(row.id)
    console.log('[ChatView] 📋 会话详情加载完成 | id=%d count=%d', row.id, history.length)
    for (const msg of history) {
      messages.value.push({
        role: msg.role,
        type: 'text',
        content: msg.content,
        id: msg.id,
        source_refs: msg.source_refs || [],
        feedback: msg.feedback || null,
        feedback_text: msg.feedback_text || null,
        intent: msg.intent || null,  // Spec-E：意图标注（历史重现）
      })
    }
  } catch (e) {
    console.warn('[ChatView] ⚠️ 会话详情加载失败', e)
  }
}

// done 后：新会话（懒创建）已被列表捕获 → 绑定其 id/title
async function bindNewSessionIfNeeded() {
  await refreshSessions()
  if (activeSessionId.value == null && currentKey.value) {
    const row = sessions.value.find(s => s.key === currentKey.value)
    if (row) {
      activeSessionId.value = row.id
      currentTitle.value = row.title || '新会话'
    }
  }
}

onMounted(async () => {
  // Spec-D/E：无 token 跳登录
  if (!localStorage.getItem('token')) {
    router.push('/login')
    return
  }
  await refreshSessions()
  if (sessions.value.length) {
    await selectSession(sessions.value[0].key)
  } else {
    newSession()
  }
})

function handleSend(text, rag) {
  if (loading.value) {
    console.warn('[ChatView] ⚠️ 发送被阻止：loading 中')
    return
  }
  useRag.value = rag
  if (!currentKey.value) newSession()
  console.log('[ChatView] 🚀 用户发送消息 | session=%s | text=%.80s | useRag=%s', currentKey.value, text, rag)
  messages.value.push({ role: 'user', type: 'text', content: text })
  loading.value = true
  statusText.value = '正在连接服务...'
  startStatusTimer()

  evtSource = chatStream(text, currentKey.value, rag)
  let eventCount = 0

  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      eventCount++
      if (data.type === 'status') {
        statusText.value = data.content
      } else if (data.type === 'done') {
        const last = messages.value[messages.value.length - 1]
        if (last?.role === 'ai' && last.type === 'text' && data.message_id != null) {
          last.id = data.message_id
        }
        console.log('[ChatView] 🏁 SSE done | 总事件数=%d', eventCount)
        closeStream()
        bindNewSessionIfNeeded()  // 异步刷新会话列表（fire-and-forget）
        return
      }
      if (data.type === 'thinking') {
        const last = messages.value[messages.value.length - 1]
        const canAppend = last?.role === 'ai' && last.type === 'thinking'
        if (!canAppend) {
          messages.value.push({ role: 'ai', type: 'thinking', content: '' })
        }
        messages.value[messages.value.length - 1].content += data.content
      } else if (data.type === 'separator') {
        messages.value.push({ role: 'ai', type: 'separator' })
      } else if (data.type === 'tool') {
        messages.value.push({ role: 'ai', type: 'tool', content: data.content })
      } else if (data.type === 'answer') {
        if (statusText.value) statusText.value = ''
        const last = messages.value[messages.value.length - 1]
        const canAppend = last?.role === 'ai' && (last.type === 'text' || last.type === 'answer')
        if (!canAppend) {
          messages.value.push({
            role: 'ai', type: 'text', content: '',
            source_refs: data.source_refs || [],
            knowledgeMode: data.knowledge_mode || null,
          })
        } else {
          if (last.source_refs === undefined && data.source_refs) last.source_refs = data.source_refs
          if (last.knowledgeMode === undefined && data.knowledge_mode) last.knowledgeMode = data.knowledge_mode
        }
        messages.value[messages.value.length - 1].content += data.content
      } else if (data.type === 'intent') {
        // Spec-E：意图标注映射到刚发送的用户消息（优先按 message_id 精确匹配，否则取最近一条 user）
        const target = messages.value.find(m => m.role === 'user' && m.id != null && m.id === data.message_id)
          || [...messages.value].reverse().find(m => m.role === 'user')
        if (target) {
          target.intent = data.intent
          if (data.message_id != null) target.id = data.message_id
        }
      } else if (data.type === 'followup') {
        // Spec-E：追问建议存到最后一条 AI 文本消息
        const last = messages.value[messages.value.length - 1]
        if (last?.role === 'ai' && last.type === 'text') last.suggestions = data.suggestions || []
      }
    } catch (parseErr) {
      console.warn('[ChatView] ⚠️ SSE 事件解析失败', parseErr, e.data?.slice(0, 100))
    }
  }

  evtSource.onerror = (err) => {
    console.error('[ChatView] ❌ SSE 连接异常', err)
    closeStream()
  }
}

function handleFollowup(suggestion) {
  console.log('[ChatView] 💬 点击追问建议 | %s', suggestion)
  handleSend(suggestion, useRag.value)
}

function closeStream() {
  if (evtSource) {
    evtSource.close(); evtSource = null
  }
  loading.value = false
  stopStatusTimer()
}

// Spec-D：赞/踩提交 → postFeedback → 高亮当前选择
async function handleFeedback(msg, value, text) {
  if (!msg.id) return
  try {
    await postFeedback(msg.id, value, text)
    msg.feedback = value
    msg.feedback_text = text || null
    console.log('[ChatView] 👍 反馈提交成功 | msg_id=%s feedback=%s', msg.id, value)
  } catch (e) {
    console.warn('[ChatView] ⚠️ 反馈提交失败', e)
  }
}
</script>

<style scoped>
.chat-layout { display: flex; height: 100%; }
.session-side { width: 220px; flex-shrink: 0; border-right: 1px solid #e8e8e8; background: #f8faff; overflow-y: auto; }
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-header { padding: 10px 16px; border-bottom: 1px solid #e8e8e8; background: #fff; }
.chat-header-title { font-size: 14px; font-weight: 600; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
