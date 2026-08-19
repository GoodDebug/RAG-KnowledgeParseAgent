<template>
  <div class="novel-workspace">
    <!-- 左列：书籍列表（session-side 式，复用 ChatView 布局模式） -->
    <div class="book-side">
      <BookList :books="books" :active-book-id="currentBookId" @select="selectBook" />
    </div>

    <!-- 右列：tab 容器（P1）——解构=现有三栏原样；复核/图谱为新面板 -->
    <div v-if="currentBookId" class="ws-main">
      <div class="ws-tabs">
        <button :class="{ active: currentTab === 'deconstruct' }" @click="selectTab('deconstruct')"><AppIcon name="Settings" :size="14" /> 解构</button>
        <button :class="{ active: currentTab === 'review' }" @click="selectTab('review')"><AppIcon name="ClipboardCheck" :size="14" /> 复核</button>
        <button :class="{ active: currentTab === 'graph' }" @click="selectTab('graph')"><AppIcon name="Share2" :size="14" /> 图谱</button>
        <button :class="{ active: currentTab === 'entity' }" @click="selectTab('entity')"><AppIcon name="BookOpen" :size="14" /> 百科</button>
        <button :class="{ active: currentTab === 'browse' }" @click="selectTab('browse')"><AppIcon name="Tags" :size="14" /> 数据</button>
      </div>

      <template v-if="currentTab === 'deconstruct'">
        <div class="ws-pane ws-top">
          <BookWorkbench
            :key="currentBookId"
            :book-id="currentBookId"
            :book-name="currentBookName"
            @deconstructed="onDeconstructed"
          />
        </div>
        <div class="ws-pane ws-mid">
          <NovelJobsView
            :key="currentBookId + ':' + jobsTick"
            :book-id="currentBookId"
            :book-name="currentBookName"
            @select="selectJob"
          />
        </div>
        <div class="ws-pane ws-bottom">
          <NovelJobDetail v-if="currentJobId" :key="currentJobId" :job-id="currentJobId" />
          <EmptyState v-else icon="⏳" title="选择任务查看详情" desc="在中栏任务列表点击任务卡片，此处显示实时进度。" />
        </div>
      </template>

      <ReviewView v-else-if="currentTab === 'review'" :key="currentBookId + ':review'" :book-id="currentBookId" :book-name="currentBookName" />
      <GraphView v-else-if="currentTab === 'graph'" :key="currentBookId + ':graph'" :book-id="currentBookId" :book-name="currentBookName" :set-selection="setSelection" />
      <EntityView v-else-if="currentTab === 'entity'" :key="currentBookId + ':entity'" :book-id="currentBookId" :book-name="currentBookName" :set-selection="setSelection" />
      <KnowledgeBrowserView v-else-if="currentTab === 'browse'" :key="currentBookId + ':browse'" :book-id="currentBookId" :book-name="currentBookName" />
    </div>
  </div>
</template>

<script setup>
// 解构工作台（统一界面）：左=书籍列表，右=上（书总览/一键解构）中（任务列表）下（任务详情 SSE）。
// URL query 作单一事实来源：currentBookId/currentJobId 由 route.query 派生，左列高亮/三栏随之同步，
// reconcile() 在书缺失/深链/反查时回写 URL，watch(route.query) 覆盖后退/前进/redirect 跳入。
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listBookNames } from '../api'
import { listJobs, getJob } from '../api/novel'
import BookList from '../components/BookList.vue'
import EmptyState from '../components/EmptyState.vue'
import BookWorkbench from './BookWorkbench.vue'
import NovelJobsView from './NovelJobsView.vue'
import NovelJobDetail from './NovelJobDetail.vue'
import ReviewView from './ReviewView.vue'
import GraphView from './GraphView.vue'
import EntityView from './EntityView.vue'
import KnowledgeBrowserView from './KnowledgeBrowserView.vue'

const route = useRoute()
const router = useRouter()
const books = ref([])
const jobsTick = ref(0)     // 中栏刷新信号（解构成功后 +1 触发 key 重挂载）
const reconciling = ref(false)

// query 值可能为重复参数数组（?book_id=a&book_id=b），归一为字符串
const norm = (v) => (Array.isArray(v) ? (v[0] || '') : (v || ''))

const currentBookId = computed(() => norm(route.query.book_id))
const currentJobId = computed(() => norm(route.query.job_id))
const currentTab = computed(() => norm(route.query.tab) || 'deconstruct')   // P1：tab 单一事实来源（缺省解构）
const currentBookName = computed(() => books.value.find(b => b.book_id === currentBookId.value)?.book_name || '')
// 大修002 Sub-2：共享选择状态（selectedEntity/currentChapter/selectedEvent）→ URL query 单一事实来源
const currentEntity = computed(() => norm(route.query.entity))
const currentChapter = computed(() => norm(route.query.chapter))
const currentEvent = computed(() => norm(route.query.event))

// 选书：丢 job_id（下栏 v-if 卸载 → onUnmounted 关 SSE），保留当前 tab
function selectBook(b) {
  if (!b?.book_id || b.book_id === currentBookId.value) return
  router.replace({ query: { book_id: String(b.book_id), tab: currentTab.value } })
}
// 选 job：写回 URL，下栏经 key 重挂载订阅该 job 的 SSE，保留当前 tab
function selectJob(id) {
  if (!id || !currentBookId.value) return
  router.replace({ query: { book_id: currentBookId.value, job_id: String(id), tab: currentTab.value } })
}
// 切 tab：改 tab，保留 book_id 与共享选择 entity/chapter/event（同书选择，Sub-2 不丢参数）
function selectTab(t) {
  if (t === currentTab.value) return
  const next = { book_id: currentBookId.value, tab: t }
  if (currentEntity.value) next.entity = currentEntity.value
  if (currentChapter.value) next.chapter = currentChapter.value
  if (currentEvent.value) next.event = currentEvent.value
  router.replace({ query: next })
}
// 共享选择状态写入（Sub-3 滑块/实体卡/事件用）：合并到当前 query，保留 book/tab/job
function setSelection({ entity, chapter, event } = {}) {
  const next = { book_id: currentBookId.value, tab: currentTab.value }
  if (currentJobId.value) next.job_id = currentJobId.value
  const put = (k, v) => { if (v !== undefined && v !== null && v !== '') next[k] = String(v); else delete next[k] }
  put('entity', entity); put('chapter', chapter); put('event', event)
  router.replace({ query: next })
}
// 上栏一键解构成功：刷新中栏任务列表 + 自动打开下栏新任务
function onDeconstructed(jobId) {
  jobsTick.value += 1
  if (jobId) selectJob(jobId)
}

// 统一协调：把 URL query 收敛到「books 内真实存在的书 + 可选 job + 合法 tab」
async function reconcile() {
  if (reconciling.value) return
  reconciling.value = true
  try {
    if (!books.value.length) {
      if (norm(route.query.book_id) || norm(route.query.job_id) || norm(route.query.tab)) router.replace({ query: {} })
      return
    }
    let qb = norm(route.query.book_id)
    let qj = norm(route.query.job_id)
    const qt = norm(route.query.tab) || 'deconstruct'   // P1：非法/缺省 tab → deconstruct
    const qe = norm(route.query.entity)                 // Sub-2：共享选择（深链保留；切书清空）
    const qc = norm(route.query.chapter)
    const qev = norm(route.query.event)
    let target = qb ? books.value.find(b => b.book_id === qb) : null
    // job_id 无有效 book_id → 用 getJob 反查所属书（跨用户/已删会抛错，兜底回退第一本）
    if (!target && qj) {
      try {
        const j = await getJob(qj)
        const found = books.value.find(b => b.book_id === j.book_id)
        if (found) { target = found; qb = found.book_id }
      } catch { /* 反查失败走兜底 */ }
    }
    if (!target) { target = books.value[0]; qb = books.value[0].book_id; qj = '' }
    // 仅在实际不一致时写回（vue-router 对相同 location 去重，配合 reconciling 防死循环）
    const bookChanged = qb !== norm(route.query.book_id)
    const next = { book_id: String(qb), tab: qt }
    if (qj) next.job_id = String(qj)
    // book 稳定 → 保留 entity/chapter/event（深链/后退不丢）；book 回退/切变 → 清空（旧书选择失效）
    if (!bookChanged) {
      if (qe) next.entity = qe
      if (qc) next.chapter = qc
      if (qev) next.event = qev
    }
    const cur = { tab: norm(route.query.tab) || 'deconstruct' }
    if (norm(route.query.book_id)) cur.book_id = norm(route.query.book_id)
    if (norm(route.query.job_id)) cur.job_id = norm(route.query.job_id)
    if (norm(route.query.entity)) cur.entity = norm(route.query.entity)
    if (norm(route.query.chapter)) cur.chapter = norm(route.query.chapter)
    if (norm(route.query.event)) cur.event = norm(route.query.event)
    if (JSON.stringify(cur) !== JSON.stringify(next)) router.replace({ query: next })
  } finally {
    reconciling.value = false
  }
}

// 加载书籍：后端 books（含 book_id）+ localStorage bookIdMap 双保险（沿用原 BookSwitcher 逻辑）
async function loadBooks() {
  try {
    const list = await listBookNames()
    const map = JSON.parse(localStorage.getItem('bookIdMap') || '{}')
    const merged = [...list]
    for (const [name, id] of Object.entries(map)) {
      if (!merged.find(b => b.book_name === name) && id) merged.push({ book_name: name, book_id: id })
    }
    books.value = merged
  } catch {
    books.value = []
  }
  reconcile()
}

// 覆盖后退/前进/深链 redirect/DeconstructPanel 跳入时的 query 变化
watch(() => route.query, reconcile)

onMounted(loadBooks)
</script>

<style scoped>
.novel-workspace { display: flex; height: 100%; }   /* 撑满 .main-content（ChatView 同款） */
.book-side {
  width: 220px; flex-shrink: 0; border-right: 1px solid #e8e8e8;
  background: #f8faff; overflow-y: auto;             /* 复用 ChatView .session-side */
}
.ws-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ws-tabs { display: flex; gap: 4px; padding: 8px 16px 0; background: #fff; border-bottom: 1px solid #e8e8e8; flex-shrink: 0; }
.ws-tabs button {
  display: inline-flex; align-items: center; gap: 5px; padding: 7px 16px;
  border: 1px solid transparent; border-bottom: none; border-radius: 8px 8px 0 0;
  background: transparent; cursor: pointer; font-size: 14px; color: #666;
}
.ws-tabs button.active { background: #f5f5f5; color: #1a73e8; font-weight: 600; border-color: #e0e0e0; }
.ws-pane { min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
.ws-top { flex: 3; }
.ws-mid { flex: 4; border-top: 1px solid #e8e8e8; }
.ws-bottom { flex: 6; border-top: 1px solid #e8e8e8; }
/* 关键坑：三个视图根节点只有 padding+overflow-y:auto 无 height——
   必须由栏约束（flex:1; min-height:0）后才能内部滚动，否则内容被 .main-content 裁剪。
   Vue3 scoped 下子组件根元素带父 scopeId，.ws-pane > * 可命中。 */
.ws-pane > * { flex: 1; min-height: 0; width: 100%; }
</style>
