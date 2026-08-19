<template>
  <div class="msg-list" ref="container">
    <div v-for="(msg, i) in messages" :key="i" class="msg-row" :class="msg.role">
      <div class="msg-label"><template v-if="msg.role === 'ai'"><AppIcon name="Bot" :size="13" /> AI</template><template v-else>你</template></div>
      <div class="msg-content">
        <div v-if="msg.type === 'thinking'" class="thinking-text">{{ msg.content }}</div>
        <div v-else-if="msg.type === 'tool'" class="tool-text"><AppIcon name="Wrench" :size="14" /> {{ msg.content }}</div>
        <div v-else-if="msg.type === 'separator'" class="separator"><span>─── 回答 ───</span></div>
        <div v-else class="answer-text" v-html="renderMarkdown(msg.content)" />

        <!-- Spec-E：意图识别标注（用户消息旁，历史/实时均可重现） -->
        <span v-if="msg.role === 'user' && msg.intent" class="intent-badge"><AppIcon name="Target" :size="14" /> {{ msg.intent }}</span>

        <!-- 引用折叠下拉（默认收起，按文件分组；顶层计划外《引用展示-折叠下拉与按文件分组》） -->
        <div v-if="msg.type === 'text' && msg.role === 'ai' && msg.source_refs && msg.source_refs.length" class="refs-dropdown">
          <button class="refs-toggle" @click="toggleRefs(i)">
            <template v-if="refsOpen.has(i)"><AppIcon name="BookOpen" :size="14" /> 收起引用 ▲</template>
            <template v-else><AppIcon name="BookOpen" :size="14" /> 引用 {{ sourceCount(msg) }} 个来源 · {{ msg.source_refs.length }} 段 ▼</template>
          </button>
          <div v-if="refsOpen.has(i)" class="refs-body">
            <div v-for="(g, gk) in groupedRefs(msg)" :key="gk" class="ref-group">
              <div class="ref-group-title"><AppIcon name="FileText" :size="14" /> {{ g.book_name ? g.book_name + ' / ' : '' }}{{ g.file_name }}（{{ g.chunkCount }} 段）</div>
              <div v-for="(s, sk) in g.snippets" :key="sk" class="ref-snippet">{{ s }}</div>
            </div>
          </div>
        </div>

        <!-- 顶层计划外降级/意图：模型自身知识答案免责标注 -->
        <div v-if="msg.type === 'text' && msg.role === 'ai' && msg.knowledgeMode === 'model'" class="disclaimer"><AppIcon name="Lightbulb" :size="14" /> 非知识库信息，仅供参考</div>

        <!-- Spec-D：赞/踩反馈（message id 存在才可反馈） -->
        <div v-if="msg.type === 'text' && msg.role === 'ai' && msg.id != null" class="feedback">
          <button :class="['fb-btn', { active: msg.feedback === 'up' }]" @click="onFeedback(msg, 'up')"><AppIcon name="ThumbsUp" :size="14" /> 有用</button>
          <button :class="['fb-btn', { active: msg.feedback === 'down' }]" @click="onFeedback(msg, 'down')"><AppIcon name="ThumbsDown" :size="14" /> 没用</button>
          <input v-model="fbText[msg.id]" class="fb-text" placeholder="可选：补充说明"
                 @keyup.enter="onFeedback(msg, msg.feedback || 'up')" />
        </div>

        <!-- Spec-E：追问引导（AI 回答下方可点击 chips；点击作为新消息发送到当前会话） -->
        <div v-if="msg.type === 'text' && msg.role === 'ai' && msg.suggestions && msg.suggestions.length" class="followup-chips">
          <span class="followup-label">继续追问：</span>
          <button v-for="(s, si) in msg.suggestions" :key="si" class="chip" @click="emit('followup', s)">{{ s }}</button>
        </div>
      </div>
    </div>
    <div v-if="loading" class="msg-row ai">
      <div class="msg-label"><AppIcon name="Bot" :size="13" /> AI</div>
      <div class="msg-content">
        <span class="dot-pulse" />
        <span class="status-text">{{ statusText || '思考中…' }}<template v-if="elapsed > 0">（已等待 {{ elapsed }}s）</template></span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import sql from 'highlight.js/lib/languages/sql'
import java from 'highlight.js/lib/languages/java'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import c from 'highlight.js/lib/languages/c'
import 'highlight.js/styles/github.min.css'
import AppIcon from './AppIcon.vue'

// 阶段一 · P3：代码块语法高亮（highlight.js 按需注册常用语言，避免全量语言包过大；浅色主题对齐现有观感）
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)   // html 复用 xml 高亮
hljs.registerLanguage('css', css)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('java', java)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('c', c)

const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// marked 代码块 renderer：已知语言用 hljs 高亮，未知/无语言原样转义（保全：marked 渲染路径其余逻辑不动）
marked.use({
  renderer: {
    code(code, infostring) {
      const lang = (infostring || '').trim().split(/\s+/)[0] || ''
      let html = code
      try {
        if (lang && hljs.getLanguage(lang)) html = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
        else html = escapeHtml(code)
      } catch { html = escapeHtml(code) }
      const cls = lang ? `hljs language-${lang}` : 'hljs'
      return `<pre><code class="${cls}">${html}</code></pre>`
    },
  },
})

const props = defineProps({ messages: Array, loading: Boolean, statusText: String, elapsed: Number })
const emit = defineEmits(['feedback', 'followup'])
const container = ref(null)
const fbText = ref({})  // msg.id -> 可选文字反馈
const refsOpen = ref(new Set())  // 展开的引用下拉（按消息循环索引 i）

function toggleRefs(i) {
  const s = new Set(refsOpen.value)
  if (s.has(i)) s.delete(i); else s.add(i)
  refsOpen.value = s
}

// 引用按 (book_name, file_name) 分组；按 chunk_id 去重（顶层计划外《引用存储精简与去重修复》）
// 新 source_refs 无 snippet（只存引用），卡片显示该文件引用 chunk 数；旧数据含 snippet 则兼容展示前 3 段
function groupedRefs(msg) {
  const map = new Map()
  for (const ref of msg.source_refs || []) {
    const key = `${ref.book_name || ''}|${ref.file_name || ''}`
    if (!map.has(key)) {
      map.set(key, { book_name: ref.book_name || '', file_name: ref.file_name || '', chunkIds: new Set(), snippets: [] })
    }
    const g = map.get(key)
    if (ref.chunk_id) {
      if (g.chunkIds.has(ref.chunk_id)) continue  // 相同 chunk（重复向量）只保留一条
      g.chunkIds.add(ref.chunk_id)
    }
    if (ref.snippet) g.snippets.push(ref.snippet)  // 旧数据兼容
  }
  return Array.from(map.values()).map(g => ({
    book_name: g.book_name,
    file_name: g.file_name,
    chunkCount: g.chunkIds.size || g.snippets.length,
    snippets: g.snippets.slice(0, 3),
  }))
}

function sourceCount(msg) {
  return groupedRefs(msg).length
}

watch(() => props.messages.length, () => { nextTick(() => { if (container.value) container.value.scrollTop = container.value.scrollHeight }) })

function renderMarkdown(text) {
  return marked(text, { breaks: true }) || text
}

function onFeedback(msg, value) {
  emit('feedback', msg, value, fbText.value[msg.id] || '')
}
</script>

<style scoped>
.msg-list { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; }
.msg-row { display: flex; flex-direction: column; max-width: 85%; }
.msg-row.user { align-self: flex-end; }
.msg-row.ai { align-self: flex-start; }
.msg-label { font-size: 12px; color: #888; margin-bottom: 4px; }
.msg-content { padding: 10px 14px; border-radius: 12px; line-height: 1.6; font-size: 14px; overflow-wrap: break-word; }
.user .msg-content { background: #e8f0fe; color: #1a1a1a; }
.ai .msg-content { background: #fff; border: 1px solid #e8e8e8; }
.thinking-text { color: #888; font-style: italic; font-size: 13px; }
.tool-text { color: #e67e22; font-size: 13px; background: #fef9e7; padding: 6px 10px; border-radius: 8px; }
.separator { text-align: center; color: #bbb; font-size: 12px; margin: 4px 0; }
.separator span { background: #f5f5f5; padding: 0 12px; }
.answer-text :deep(p) { margin: .4em 0; }
.answer-text :deep(pre) { background: #f0f0f0; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 13px; }
.answer-text :deep(code) { background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
.answer-text :deep(pre code) { background: none; padding: 0; }
/* 引用折叠下拉（默认收起，按文件分组；顶层计划外） */
.refs-dropdown { margin-top: 8px; }
.refs-toggle {
  border: 1px solid #d0d0d0; background: #f8faff; border-radius: 6px;
  padding: 4px 10px; font-size: 12px; color: #1a73e8; cursor: pointer;
}
.refs-toggle:hover { background: #e8f0fe; }
.refs-body { margin-top: 6px; border-left: 3px solid #1a73e8; background: #f8faff; border-radius: 6px; padding: 6px 10px; }
.ref-group { margin-bottom: 8px; }
.ref-group:last-child { margin-bottom: 0; }
.ref-group-title { color: #1a73e8; font-weight: 600; font-size: 12px; margin-bottom: 3px; }
.ref-snippet { color: #555; font-size: 12px; line-height: 1.5; margin: 2px 0; }
/* Spec-D：赞/踩反馈 */
.feedback { display: flex; align-items: center; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.fb-btn { border: 1px solid #ddd; background: #fff; border-radius: 6px; padding: 3px 10px; font-size: 13px; cursor: pointer; }
.fb-btn.active { border-color: #1a73e8; color: #1a73e8; background: #e8f0fe; }
.fb-text { flex: 1; min-width: 120px; border: 1px solid #ddd; border-radius: 6px; padding: 4px 8px; font-size: 12px; }
.status-text { margin-left: 8px; color: #888; font-size: 13px; }
.disclaimer { margin-top: 6px; color: #b58900; font-size: 12px; background: #fffbe6; padding: 4px 8px; border-radius: 6px; }
/* Spec-E：意图徽标 + 追问建议 chips */
.intent-badge { display: inline-block; margin-top: 6px; padding: 2px 8px; background: #e8f0fe; color: #1a73e8; border-radius: 10px; font-size: 12px; }
.followup-chips { display: flex; align-items: center; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.followup-label { color: #888; font-size: 12px; }
.chip { border: 1px solid #1a73e8; color: #1a73e8; background: #fff; border-radius: 14px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.chip:hover { background: #e8f0fe; }
.dot-pulse { display: inline-block; width: 8px; height: 8px; background: #888; border-radius: 50%; animation: pulse 1.2s infinite; }
@keyframes pulse { 0%, 100% { opacity: .3; } 50% { opacity: 1; } }
</style>
