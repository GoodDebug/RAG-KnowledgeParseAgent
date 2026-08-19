<template>
  <div class="job-detail">
    <div class="jd-header">
      <h2><AppIcon name="ListTodo" :size="18" /> 解构任务 · {{ jobId }}</h2>
      <StatusBadge :status="job?.status || 'pending'" :label="job?.status || '加载中'" />
    </div>

    <!-- 进度条 -->
    <div class="progress-wrap" v-if="total > 0">
      <ProgressBar :percent="pct" />
      <span class="progress-text">{{ done }}/{{ total }} 完成 · 失败 {{ failed }}</span>
    </div>

    <!-- 章节表 -->
    <table class="chapter-table">
      <thead><tr><th>章</th><th>标题</th><th>状态</th><th>重试</th><th>缩窗</th><th>错误</th><th></th></tr></thead>
      <tbody>
        <tr v-for="c in chapters" :key="c.chapter_id">
          <td>{{ c.chapter_index }}</td>
          <td>{{ c.chapter_title }}</td>
          <td><StatusBadge :status="c.status" /></td>
          <td>{{ c.retry_count }}</td>
          <td>{{ c.shrink_level }}</td>
          <td class="err" :title="c.error_msg">{{ (c.error_msg || '').slice(0, 40) }}</td>
          <td><button v-if="c.status === 'failed'" class="btn btn-small" @click="retryChapter(c)">重试</button></td>
        </tr>
      </tbody>
    </table>
    <EmptyState v-if="!loading && chapters.length === 0" icon="📄" title="无章节" />

    <!-- 事件流日志 -->
    <div class="event-log">
      <div class="log-title">事件流（SSE）</div>
      <div class="log-body">
        <div v-for="(e, i) in logs" :key="i" class="log-line">{{ e }}</div>
        <div v-if="logs.length === 0" class="log-line log-empty">等待事件…</div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 任务详情（前端 P0）：章节表 + SSE 实时进度/事件流 + 失败章重试 + 轮询降级兜底。
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getJob, retry, novelStream } from '../api/novel'
import EmptyState from '../components/EmptyState.vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ProgressBar from '../components/ProgressBar.vue'

// 任务详情（解构工作台下栏）：jobId 由父组件传入（原 route.params.job_id 改为 props）。
// SSE/轮询生命周期靠工作台 v-if + :key 重挂载保证清理（本组件无需 watch）。
const props = defineProps({ jobId: { type: String, required: true } })
const job = ref(null)
const chapters = ref([])
const logs = ref([])
const loading = ref(true)
let streamHandle = null
let pollTimer = null
let lastEventTs = Date.now()

const done = computed(() => job.value?.done_chapters ?? 0)
const failed = computed(() => job.value?.failed_chapters ?? 0)
const total = computed(() => job.value?.total_chapters ?? 0)
const pct = computed(() => (total.value ? Math.round((done.value / total.value) * 100) : 0))

const STATUS_MAP = {
  chapter_started: (c) => ({ ...c, status: 'processing' }),
  chapter_done: (c) => ({ ...c, status: 'done' }),
  chapter_failed: (c) => ({ ...c, status: 'failed', error_msg: c.error }),
}

async function refresh() {
  try {
    job.value = await getJob(props.jobId)
    // 章节表以 DB 为准（SSE 事件只做实时覆盖；轮询降级刷新回 DB）
    chapters.value = job.value.chapters || []
    loading.value = false
  } catch { loading.value = false }
}

function onEvent(ev) {
  lastEventTs = Date.now()
  const t = ev.type
  if (t === 'job_started') {
    // 任务重跑开始：置 running，清空上一轮日志
    job.value = { ...job.value, status: 'running' }
    logs.value = []
  }
  if (t === 'chapter_started' || t === 'chapter_done' || t === 'chapter_failed') {
    const upd = STATUS_MAP[t](ev)
    chapters.value = chapters.value.map(c => c.chapter_id === ev.chapter_id ? { ...c, ...upd } : c)
  }
  if (t === 'progress') job.value = { ...job.value, done_chapters: ev.done, failed_chapters: ev.failed }
  if (t === 'agent_started' || t === 'agent_done' || t === 'agent_failed') logs.value.push(`[${ev.agent}] ${t}${ev.items ? ' ×'+ev.items : ''}${ev.error ? ' '+ev.error : ''}`)
  if (t === 'job_done' || t === 'job_failed') { job.value = { ...job.value, status: t === 'job_done' ? 'done' : 'failed' } }
  if (t === 'stream_error') logs.value.push('⚠ SSE 断开，启用轮询降级')
  if (logs.value.length > 200) logs.value = logs.value.slice(-200)
}

// 轮询降级：3s 无 SSE 事件且 job 非终态 → 定时刷新
function ensurePolling() {
  pollTimer = setInterval(() => {
    if (Date.now() - lastEventTs > 3000 && job.value?.status !== 'done' && job.value?.status !== 'failed') {
      refresh()
    }
  }, 3000)
}

async function retryChapter(c) {
  try { await retry(props.jobId, [c.chapter_id]); logs.value.push(`🔁 重试 ${c.chapter_title}`); await refresh() }
  catch (e) { logs.value.push(`⚠ 重试失败 ${e.message}`) }
}

onMounted(() => { refresh(); streamHandle = novelStream(props.jobId, onEvent); ensurePolling() })
onUnmounted(() => { streamHandle?.close(); clearInterval(pollTimer) })
</script>

<style scoped>
.job-detail { padding: 20px; overflow-y: auto; }
.jd-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.progress-wrap { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.progress-text { font-size: 12px; color: #666; white-space: nowrap; }
.chapter-table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; }
.chapter-table th, .chapter-table td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; font-size: 13px; text-align: left; }
.chapter-table th { background: #fafafa; color: #666; }
.err { color: #d93025; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.btn-small { padding: 3px 10px; border: 1px solid #ccc; border-radius: 5px; background: #fff; cursor: pointer; font-size: 12px; }
.event-log { margin-top: 16px; }
.log-title { font-size: 13px; color: #666; margin-bottom: 6px; }
.log-body { background: #0d1117; color: #e6edf3; border-radius: 8px; padding: 10px; font-size: 12px; max-height: 200px; overflow-y: auto; font-family: ui-monospace, monospace; }
.log-line { padding: 1px 0; }
.log-empty { color: #666; }
</style>
