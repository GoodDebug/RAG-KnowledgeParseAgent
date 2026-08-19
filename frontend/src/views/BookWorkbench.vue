<template>
  <div class="workbench">
    <div class="wb-header">
      <h2><AppIcon name="Book" :size="18" /> {{ bookName || bookId }}</h2>
      <div class="wb-actions">
        <button class="btn btn-primary" :disabled="busy" @click="startDeconstruct">
          <AppIcon name="Settings" :size="16" /> {{ busy ? '解构中…' : '一键解构' }}
        </button>
      </div>
    </div>

    <!-- 总览卡：最新 job / pending 复核 / 章节数 -->
    <div v-if="overview" class="wb-cards">
      <div class="wb-card"><span class="k">最新任务</span><span class="v">{{ overview.jobStatus || '无' }}</span></div>
      <div class="wb-card"><span class="k">待复核</span><span class="v">{{ overview.pending }}</span></div>
      <div class="wb-card"><span class="k">章节</span><span class="v">{{ overview.chapters }}</span></div>
    </div>

    <!-- 空态：无任务 → 引导一键解构 -->
    <EmptyState
      v-if="!busy && overview && overview.jobStatus === null"
      icon="🏗️" title="该书尚未解构"
      desc="导入小说后点击「一键解构」，把章节原文交给 8 个 Agent 抽取实体/关系/时间线等。"
      action-text="一键解构"
      :action="startDeconstruct"
    />
    <p v-if="msg" class="wb-msg">{{ msg }}</p>
  </div>
</template>

<script setup>
// 书工作台（前端 P0）：总览最新 job / pending 复核 / 章节数；空态引导一键解构。
import { ref, onMounted } from 'vue'
import { listJobs, deconstruct, getJob, listValidation } from '../api/novel'
import EmptyState from '../components/EmptyState.vue'
import AppIcon from '../components/AppIcon.vue'

// 工作台（解构工作台顶栏）：bookId/bookName 由父组件传入（原 route.params.book_id 改为 props）。
// 总览最新 job / pending 复核 / 章节数；「一键解构」成功后 emit('deconstructed', job_id) 由工作台刷新任务列表。
const props = defineProps({
  bookId: { type: String, required: true },
  bookName: { type: String, default: '' },   // 可选：书名校验展示，缺省回退 bookId
})
const emit = defineEmits(['deconstructed'])
const overview = ref(null)
const busy = ref(false)
const msg = ref('')

async function load() {
  try {
    const jobs = await listJobs(props.bookId)
    const latest = jobs[0] || null
    let chapters = 0
    if (latest && (latest.status === 'done' || latest.status === 'failed')) {
      try { chapters = (await getJob(latest.job_id)).chapters.length } catch { /* 忽略 */ }
    }
    let pending = 0
    try { pending = (await listValidation(props.bookId)).summary?.pending_total || 0 } catch { /* 忽略 */ }
    overview.value = { jobStatus: latest ? latest.status : null, pending, chapters }
  } catch { overview.value = null }
}

async function startDeconstruct() {
  busy.value = true; msg.value = ''
  try {
    const r = await deconstruct(props.bookId)
    msg.value = `已创建解构任务 ${r.job_id}`
    emit('deconstructed', r.job_id)   // 通知工作台：刷新任务列表 + 自动打开下栏新任务
  } catch (e) {
    msg.value = String(e.message || e)
  } finally { busy.value = false }
}
onMounted(load)
</script>

<style scoped>
.workbench { padding: 20px; overflow-y: auto; }
.wb-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.wb-actions { display: flex; gap: 8px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.wb-cards { display: flex; gap: 12px; margin-bottom: 16px; }
.wb-card { flex: 1; background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 14px; }
.wb-card .k { display: block; font-size: 12px; color: #999; }
.wb-card .v { font-size: 18px; font-weight: 600; }
.wb-msg { color: #1a73e8; margin-top: 8px; }
</style>
