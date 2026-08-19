<template>
  <div class="deconstruct-panel">
    <div class="dp-header">
      <h3><AppIcon name="Microscope" :size="16" /> 解构进度</h3>
      <span v-if="book && book.deconstruct_on" class="dp-book">{{ book.book_name }}</span>
    </div>

    <!-- 未上传：引导用户在 ingest-card 勾选开关并上传 -->
    <EmptyState
      v-if="!book"
      icon="🔬" title="尚未上传解构任务"
      desc="在上方「文档入库」选择文件并勾选「自动解构」，此处将实时显示该书的解构进度。"
    />

    <!-- 未启用解构：本次上传 deconstruct=0 → 无 novel_chapter -->
    <EmptyState
      v-else-if="!book.deconstruct_on"
      icon="🔇" title="本次未启用解构"
      desc="该书未写入章节数据（novel_chapter）。重传同文件时勾选「自动解构」开关即可生成解构任务。"
    />

    <!-- 有数据：最新 job + SSE 实时进度 -->
    <div v-else class="dp-body">
      <div v-if="checking" class="dp-loading">正在定位解构任务…</div>
      <EmptyState
        v-else-if="!latest"
        icon="🏗️" title="暂无解构任务"
        desc="上传后任务由后台异步创建，若始终为空请确认文件含章节结构后重试。"
      />

      <template v-else>
        <div class="dp-job">
          <div class="dp-job-row">
            <span class="job-id">{{ latest.job_id }}</span>
            <StatusBadge :status="latest.status" />
          </div>
          <div class="progress-wrap">
            <ProgressBar :percent="pct" />
            <span class="progress-text">{{ done }}/{{ total }} 完成 · 失败 {{ failed }}</span>
          </div>
        </div>

        <div class="dp-actions">
          <button class="btn btn-primary" :disabled="busy" @click="startDeconstruct">
            <AppIcon name="Settings" :size="16" /> {{ busy ? '解构中…' : '一键解构' }}
          </button>
          <button class="btn" @click="goJobs"><AppIcon name="ClipboardList" :size="16" /> 任务中心</button>
          <button class="btn" @click="goDetail"><AppIcon name="ListTodo" :size="16" /> 任务详情</button>
        </div>
        <p v-if="msg" class="dp-msg">{{ msg }}</p>
      </template>
    </div>
  </div>
</template>

<script setup>
// 解构进度面板（前端 P0 集成点）：只展示「当前上传的书」的解构数据。
// 上传 deconstruct=1 时后台异步切章建 job（trigger_type=upload）→ 本面板短轮询定位
// 最新 job → 订阅 novelStream（SSE）实时刷新进度条；SSE 静默超 3s 时回退 listJobs 同步。
import { ref, computed, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { listJobs, deconstruct, novelStream } from '../api/novel'
import EmptyState from './EmptyState.vue'
import AppIcon from './AppIcon.vue'
import StatusBadge from './StatusBadge.vue'
import ProgressBar from './ProgressBar.vue'

const props = defineProps({
  // { book_id, book_name, deconstruct_on }：本次 ingest-card 上传的书；null = 未上传
  book: { type: Object, default: null },
})

const router = useRouter()
const latest = ref(null)        // 最新解构 job（后端列表最新在前）
const checking = ref(true)      // 定位任务中（上传后异步建 job）
const busy = ref(false)         // 一键解构防连点
const msg = ref('')
let streamHandle = null
let waitTimer = null            // 等待后台建 job 的轮询（1s×12）
let fallbackTimer = null        // SSE 静默降级轮询（3s）
let lastEventTs = 0

const done = computed(() => latest.value?.done_chapters ?? 0)
const failed = computed(() => latest.value?.failed_chapters ?? 0)
const total = computed(() => latest.value?.total_chapters ?? 0)
const pct = computed(() => (total.value ? Math.min(100, Math.round((done.value / total.value) * 100)) : 0))

// ---------------- SSE / 轮询 ----------------

function clearTimers() {
  if (waitTimer) { clearInterval(waitTimer); waitTimer = null }
  if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null }
}
function stopStream() { streamHandle?.close(); streamHandle = null }

// 从 DB 拉最新 job：有则置 latest 并订阅 SSE，返回 true
async function syncFromDb() {
  if (!props.book?.book_id) return false
  try {
    const jobs = await listJobs(props.book.book_id)
    if (jobs.length) {
      latest.value = jobs[0]
      subscribe(jobs[0])
      return true
    }
  } catch { /* 网络/鉴权暂不可用，等待下一轮 */ }
  return false
}

function subscribe(job) {
  stopStream()
  if (job && (job.status === 'pending' || job.status === 'running')) {
    lastEventTs = Date.now()
    streamHandle = novelStream(job.job_id, onEvent)
  }
}

function onEvent(ev) {
  lastEventTs = Date.now()
  const t = ev.type
  if (t === 'progress' && latest.value) {
    latest.value = { ...latest.value, done_chapters: ev.done, failed_chapters: ev.failed }
  }
  if (t === 'job_done') { latest.value = { ...latest.value, status: 'done' }; stopStream() }
  if (t === 'job_failed') { latest.value = { ...latest.value, status: 'failed' }; stopStream() }
  if (t === 'stream_error') msg.value = '⚠ SSE 断开，已启用轮询降级'
}

// 上传成功后后台异步建 job → 短轮询等待其出现（≤12s），出现后订阅 SSE；超时判空态
function waitForJob() {
  checking.value = true
  let tries = 0
  waitTimer = setInterval(async () => {
    tries += 1
    if (await syncFromDb() || tries >= 12) {
      clearInterval(waitTimer); waitTimer = null
      checking.value = false
      if (!latest.value) msg.value = '未能定位解构任务，请确认文件含章节结构'
      else startFallback()
    }
  }, 1000)
}

// SSE 静默 >3s 且 job 非终态 → 从 DB 同步一次（防 SSE 断开后进度冻结）
function startFallback() {
  fallbackTimer = setInterval(() => {
    const st = latest.value?.status
    if (st && st !== 'done' && st !== 'failed' && Date.now() - lastEventTs > 3000) {
      syncFromDb()
    }
  }, 3000)
}

// ---------------- 操作 ----------------

async function startDeconstruct() {
  if (!props.book?.book_id) return
  busy.value = true; msg.value = ''
  try {
    await deconstruct(props.book.book_id)
    msg.value = '已触发解构'
    await syncFromDb()
  } catch (e) {
    msg.value = String(e.message || e)   // 409 running / 404 无章节
  } finally { busy.value = false }
}

// 跳解构工作台（旧独立路由 /books/:id/jobs、/jobs/:id 已 redirect 到 /deconstruct，query 预选）
function goJobs() { router.push({ path: '/deconstruct', query: { book_id: props.book.book_id } }) }
function goDetail() { if (latest.value) router.push({ path: '/deconstruct', query: { book_id: props.book.book_id, job_id: latest.value.job_id } }) }

// ---------------- 生命周期 ----------------

// 每次上传的书变化 → 重置并重新定位任务（仅 deconstruct_on 的书才有数据）
watch(() => props.book, (b) => {
  clearTimers(); stopStream()
  latest.value = null; msg.value = ''; checking.value = true
  if (b?.book_id && b.deconstruct_on) waitForJob()
  else checking.value = false
}, { immediate: true })

onUnmounted(() => { clearTimers(); stopStream() })
</script>

<style scoped>
.deconstruct-panel { background: #fff; border-radius: 12px; padding: 20px; border: 1px solid #e8e8e8; }
.dp-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.dp-header h3 { font-size: 16px; }
.dp-book { font-size: 13px; color: #666; }
.dp-loading { color: #999; font-size: 14px; padding: 12px 0; }
.dp-job { display: flex; flex-direction: column; gap: 10px; padding: 12px 0; }
.dp-job-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.job-id { font-size: 13px; color: #555; }
.progress-wrap { display: flex; align-items: center; gap: 10px; }
.progress-text { font-size: 12px; color: #666; white-space: nowrap; }
.dp-actions { display: flex; gap: 8px; margin-top: 8px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 14px; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.dp-msg { color: #1a73e8; font-size: 13px; margin-top: 8px; }
</style>
