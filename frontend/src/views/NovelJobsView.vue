<template>
  <div class="jobs-view">
    <div class="jv-header">
      <h2><AppIcon name="ClipboardList" :size="18" /> 解构任务 · {{ bookName || bookId }}</h2>
      <button class="btn btn-primary" :disabled="busy" @click="startDeconstruct">
        <AppIcon name="Settings" :size="16" /> {{ busy ? '解构中…' : '一键解构' }}
      </button>
    </div>

    <EmptyState
      v-if="!busy && jobs.length === 0"
      icon="🏗️" title="暂无解构任务"
      desc="导入小说后点「一键解构」，开始抽取实体/关系/时间线。"
      action-text="一键解构"
      :action="startDeconstruct"
    />

    <div class="job-list">
      <div v-for="j in jobs" :key="j.job_id" class="job-card" @click="goDetail(j.job_id)">
        <div class="job-row">
          <span class="job-id">{{ j.job_id }}</span>
          <StatusBadge :status="j.status" />
        </div>
        <div class="job-row job-meta">
          <span>{{ j.trigger_type }}</span>
          <span>总 {{ j.total_chapters }} · 完成 {{ j.done_chapters }} · 失败 {{ j.failed_chapters }}</span>
          <span>{{ j.finished_at || j.started_at || '—' }}</span>
        </div>
      </div>
    </div>
    <p v-if="msg" class="jv-msg">{{ msg }}</p>
  </div>
</template>

<script setup>
// 解构任务列表（前端 P0）：job 卡（最新在前）+ 一键解构。
import { ref, onMounted } from 'vue'
import { listJobs, deconstruct } from '../api/novel'
import EmptyState from '../components/EmptyState.vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'

// 任务列表（解构工作台中栏）：bookId 由父组件传入；点选 job → emit('select', job_id) 让下栏展示详情。
const props = defineProps({
  bookId: { type: String, required: true },
  bookName: { type: String, default: '' },
})
const emit = defineEmits(['select'])
const jobs = ref([])
const busy = ref(false)
const msg = ref('')

async function load() {
  try { jobs.value = await listJobs(props.bookId) } catch { jobs.value = [] }
}
async function startDeconstruct() {
  busy.value = true; msg.value = ''
  try {
    const r = await deconstruct(props.bookId)
    msg.value = `已创建任务 ${r.job_id}`
    emit('select', r.job_id)   // 自动在下栏打开新任务（SSE 跟进）
    await load()
  } catch (e) {
    msg.value = String(e.message || e)   // 409 running / 404 无章节
  } finally { busy.value = false }
}
function goDetail(jobId) { emit('select', jobId) }
onMounted(load)
</script>

<style scoped>
.jobs-view { padding: 20px; overflow-y: auto; }
.jv-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.job-list { display: flex; flex-direction: column; gap: 10px; }
.job-card { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 14px; cursor: pointer; }
.job-card:hover { border-color: #1a73e8; }
.job-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.job-id { font-size: 13px; color: #555; }
.job-meta { font-size: 12px; color: #999; margin-top: 6px; }
.jv-msg { color: #1a73e8; margin-top: 8px; }
</style>
