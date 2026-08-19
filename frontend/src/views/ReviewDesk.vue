<template>
  <div class="rd-overlay" @click.self="$emit('close')">
    <div class="rd-panel">
      <div class="rd-header">
        <h3><AppIcon name="ClipboardCheck" :size="18" /> 裁决 · {{ issue.issue_id }}</h3>
        <button class="rd-close" @click="$emit('close')">✕</button>
      </div>

      <div class="rd-cols">
        <!-- 左：真实原文上下文（P2-1）——getIssueEvidence 拉取；有证据显示原文窗口，否则元数据兜底（不阻断裁决） -->
        <div class="rd-col">
          <div class="rd-col-title">原文上下文</div>
          <template v-if="evidence && !evidenceBusy">
            <!-- 章节标题 + 关键词高亮原文窗口（escapeHtml + 受控 <mark>，复用 EvidencePanel 模式）+ 字符区间 -->
            <div class="rd-ev-heading">第 {{ evidence.chapter_index }} 章 {{ evidence.chapter_title }}</div>
            <p class="rd-ev-text" v-html="highlightedText"></p>
            <div class="rd-ev-range">位置 {{ evidence.char_start + 1 }}-{{ evidence.char_end }} 字</div>
          </template>
          <template v-else>
            <!-- 加载态 / {evidence:null} / 拉取失败 → 回退现状元数据 -->
            <div v-if="evidenceBusy" class="rd-ev-loading">加载原文…</div>
            <div class="rd-desc">{{ issue.description }}</div>
            <div class="rd-row"><span>record_type</span><code>{{ issue.record_type }}</code></div>
            <div class="rd-row"><span>章节</span><code>{{ issue.chapter_title || '—' }}<span v-if="issue.chapter_id" class="rd-id"> · {{ issue.chapter_id }}</span></code></div>
            <div class="rd-row"><span>已入库值</span><pre class="rd-json">{{ issue.original_value || '—' }}</pre></div>
          </template>
        </div>
        <!-- 右：抽取 JSON + 修正值 -->
        <div class="rd-col">
          <div class="rd-col-title">抽取 JSON（suggested_value）</div>
          <pre class="rd-json">{{ prettySuggested }}</pre>
          <div class="rd-edit">
            <label>修正值 corrected_value（留空 = 用 suggested_value）</label>
            <textarea v-model="correctedValue" rows="4" class="rd-textarea" placeholder="可填写 JSON 修正记录"></textarea>
          </div>
        </div>
      </div>

      <div class="rd-actions">
        <button class="btn btn-primary" :disabled="busy" @click="doApprove"><AppIcon name="Check" :size="15" /> 通过 (A)</button>
        <button class="btn" :disabled="busy" @click="doFix"><AppIcon name="Pencil" :size="15" /> 修正 (E)</button>
        <button class="btn btn-danger" :disabled="busy" @click="doIgnore"><AppIcon name="X" :size="15" /> 忽略 (R)</button>
        <span class="rd-hint">快捷键 A/E/R（输入框聚焦时不触发）</span>
        <button class="btn btn-right" @click="exportLog">导出决策日志</button>
      </div>
      <p v-if="msg" class="rd-msg">{{ msg }}</p>
    </div>
  </div>
</template>

<script setup>
// 裁决工作台（P1 · 复核 tab 的 modal）：左真实原文上下文/右 JSON + 底部裁决条 Approve(confirm)/Edit(fix)/Reject(ignore)。
// P2-1：左栏改为 getIssueEvidence 拉取真实原文（关键词高亮）；无证据/拉取失败 → 元数据兜底，不阻断裁决。
// 键盘快捷键 A/E/R；每次裁决写 localStorage['reviewLog']（决策日志，可导出回流 few-shot 反例）。
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { confirmIssue, ignoreIssue, fixIssue, getIssueEvidence } from '../api/novel'
import AppIcon from '../components/AppIcon.vue'

const props = defineProps({
  issue: { type: Object, required: true },   // {issue_id, record_type, issue_type, severity, description, original_value, suggested_value, chapter_id}
  bookId: { type: String, required: true },
})
const emit = defineEmits(['done', 'close'])

const correctedValue = ref('')
const busy = ref(false)
const msg = ref('')

const evidence = ref(null)        // {chapter_index, chapter_title, text, char_start, char_end, matched_terms[]}；无证据 → null
const evidenceBusy = ref(false)   // 原文拉取加载态

const prettySuggested = computed(() => {
  const raw = props.issue.suggested_value
  if (!raw) return '—'
  try { return JSON.stringify(JSON.parse(raw), null, 2) } catch { return raw }
})

// 与 EvidencePanel/MessageList 口径一致的转义（& < > " '）：先整体转义原文，杜绝 HTML/脚本注入（Spec §5/§8）
const escapeHtml = (s) => String(s)
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;')
// 正则元字符转义：关键词含 . [ ] + 等时先转义，避免破坏匹配表达式
const escapeRegex = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// 受控高亮 HTML：原文 → escapeHtml → 对转义后的 matched_terms 做 <mark> 包裹 → v-html（复用 EvidencePanel 模式）。
// 关键词由服务端 matched_terms 返回，前端不做重复提取；长词优先防短词截断。
const highlightedText = computed(() => {
  const raw = evidence.value?.text
  if (!raw) return ''
  const escaped = escapeHtml(raw)
  const terms = [...new Set((evidence.value.matched_terms || [])
    .map(t => (t == null ? '' : String(t).trim()))
    .filter(Boolean))]
    .map(t => escapeRegex(escapeHtml(t)))
    .sort((a, b) => b.length - a.length)
  if (!terms.length) return escaped
  return escaped.replace(new RegExp(`(${terms.join('|')})`, 'g'), '<mark>$1</mark>')
})

// 拉取疑点原文证据：issue 变化即重拉；{evidence:null} / 拉取失败 → 置 null 走元数据兜底
async function loadEvidence() {
  evidenceBusy.value = true
  evidence.value = null
  try {
    const r = await getIssueEvidence(props.bookId, props.issue.issue_id)
    // 契约：无证据 → {evidence:null}；命中 → 直接返回 evidence 对象
    evidence.value = (r && r.evidence === null) ? null : (r || null)
  } catch { evidence.value = null }
  finally { evidenceBusy.value = false }
}
// 打开时 + issue 变化时拉取（immediate 覆盖 onMounted）
watch(() => props.issue, loadEvidence, { immediate: true })

// 决策日志：localStorage['reviewLog']（{ts, issue_id, verdict, corrected_value}）
function log(verdict, corrected = null) {
  try {
    const arr = JSON.parse(localStorage.getItem('reviewLog') || '[]')
    arr.push({ ts: Date.now(), issue_id: props.issue.issue_id, verdict, corrected_value: corrected })
    localStorage.setItem('reviewLog', JSON.stringify(arr))
  } catch { /* localStorage 不可用忽略 */ }
}
function exportLog() {
  const data = localStorage.getItem('reviewLog') || '[]'
  const blob = new Blob([data], { type: 'application/json;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'review-log.json'
  a.click()
}

async function doApprove() {
  busy.value = true; msg.value = ''
  try {
    const r = await confirmIssue(props.issue.issue_id)
    log('confirmed')
    msg.value = `已通过（${r.status}）`
    emit('done')
  } catch (e) { msg.value = String(e.message || e) }
  finally { busy.value = false }
}
async function doFix() {
  busy.value = true; msg.value = ''
  try {
    const val = correctedValue.value.trim() || null
    const r = await fixIssue(props.issue.issue_id, val)
    log('fixed', val)
    msg.value = `已修正（${r.status}）`
    emit('done')
  } catch (e) { msg.value = String(e.message || e) }
  finally { busy.value = false }
}
async function doIgnore() {
  busy.value = true; msg.value = ''
  try {
    const r = await ignoreIssue(props.issue.issue_id)
    log('ignored')
    msg.value = `已忽略（${r.status}）`
    emit('done')
  } catch (e) { msg.value = String(e.message || e) }
  finally { busy.value = false }
}

// 键盘快捷键 A/E/R（输入框/文本域聚焦时不触发，防误触）
function onKey(e) {
  const tag = document.activeElement?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA') return
  const k = e.key.toLowerCase()
  if (k === 'a') doApprove()
  else if (k === 'e') doFix()
  else if (k === 'r') doIgnore()
}
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.rd-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 30; display: flex; align-items: center; justify-content: center; }
.rd-panel { width: min(860px, 92vw); max-height: 86vh; background: #fff; border-radius: 12px; padding: 20px; display: flex; flex-direction: column; gap: 14px; box-shadow: 0 8px 30px rgba(0,0,0,.2); }
.rd-header { display: flex; justify-content: space-between; align-items: center; }
.rd-header h3 { font-size: 16px; }
.rd-close { border: none; background: none; font-size: 16px; cursor: pointer; color: #888; }
.rd-cols { display: flex; gap: 14px; }
.rd-col { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.rd-col-title { font-size: 13px; font-weight: 600; color: #555; }
.rd-desc { font-size: 13px; color: #333; background: #f8f9fb; padding: 8px 10px; border-radius: 8px; line-height: 1.5; }
.rd-ev-heading { font-size: 13px; font-weight: 600; color: #1a73e8; }
.rd-ev-text { font-size: 14px; line-height: 1.7; color: #333; overflow-wrap: break-word; margin: 0; background: #f8f9fb; padding: 10px 12px; border-radius: 8px; max-height: 220px; overflow: auto; }
/* v-html 生成的 <mark> 不受 scoped 影响，需 :deep 穿透（EvidencePanel 同款） */
.rd-ev-text :deep(mark) { background: #fef08a; color: #1a1a1a; padding: 0 2px; border-radius: 3px; }
.rd-ev-range { font-size: 12px; color: #999; }
.rd-ev-loading { font-size: 12px; color: #999; margin-bottom: 4px; }
.rd-row { display: flex; gap: 8px; align-items: center; font-size: 12px; color: #666; }
.rd-row span { flex-shrink: 0; color: #999; }
.rd-row code { font-size: 12px; }
.rd-id { color: #999; font-size: 11px; }
.rd-json { background: #0d1117; color: #e6edf3; border-radius: 8px; padding: 10px; font-size: 12px; overflow: auto; max-height: 220px; font-family: ui-monospace, monospace; white-space: pre-wrap; }
.rd-edit label { display: block; font-size: 12px; color: #666; margin-bottom: 4px; }
.rd-textarea { width: 100%; border: 1px solid #d0d0d0; border-radius: 8px; padding: 8px 10px; font-size: 12px; font-family: ui-monospace, monospace; resize: vertical; }
.rd-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 14px; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn-danger { background: #d93025; color: #fff; border-color: #d93025; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.btn-right { margin-left: auto; }
.rd-hint { font-size: 12px; color: #999; }
.rd-msg { color: #1a73e8; font-size: 13px; }
</style>
