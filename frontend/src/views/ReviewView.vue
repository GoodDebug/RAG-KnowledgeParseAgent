<template>
  <div class="review-view">
    <div class="rv-header">
      <h2><AppIcon name="ClipboardCheck" :size="18" /> 复核 · {{ bookName || bookId }}</h2>
      <span class="rv-count">待复核 {{ total }}</span>
    </div>

    <EmptyState v-if="!loading && issues.length === 0" icon="✅" title="暂无待复核疑点"
      desc="解构完成后经一致性校验产生的疑点会在此排队。" />

    <template v-else>
      <!-- 批量操作条：勾选批量重写（P1 既有）+ 一键确认低风险（P2-1，severity=info 全部 confirm） -->
      <div class="rv-batch">
        <template v-if="selectedIds.length">
          <span>已选 {{ selectedIds.length }} 条</span>
          <button class="btn btn-primary" :disabled="batchBusy" @click="runRepersist">
            <AppIcon name="RefreshCw" :size="14" /> 批量重写
          </button>
          <button class="btn" @click="selectedIds = []">取消</button>
        </template>
        <button class="btn btn-primary" :disabled="batchBusy || infoIds.length === 0" @click="runConfirmInfo">
          <AppIcon name="CheckCircle2" :size="14" /> 一键确认低风险
          <span v-if="infoIds.length" class="rv-info-count">({{ infoIds.length }})</span>
        </button>
        <span v-if="infoIds.length === 0" class="rv-msg rv-msg-none">无低风险项</span>
        <span v-if="batchMsg" class="rv-msg">{{ batchMsg }}</span>
      </div>

      <!-- 三 tab 队列：快速批(info) / 需细看(warning) / 高风险(critical) -->
      <!-- n-tabs 默认 v-model 属性即 value：用无参 v-model 满足 vue/no-v-model-argument 规则 -->
      <n-tabs v-model="activeTab" type="line" size="small">
        <n-tab-pane name="fast" :tab="`快速批 (${bySeverity.fast.length})`">
          <div class="issue-list">
            <div v-for="iss in bySeverity.fast" :key="iss.issue_id" class="issue-card" @click="open(iss)">
              <input type="checkbox" :checked="selectedIds.includes(iss.issue_id)" @click.stop="toggle(iss.issue_id)" />
              <div class="issue-body">
                <div class="issue-row">
                  <span class="issue-type">{{ iss.issue_type }}</span>
                  <span class="sev" :class="'s-' + iss.severity">{{ iss.severity }}</span>
                </div>
                <div class="issue-desc">{{ (iss.description || '').slice(0, 90) }}</div>
                <div class="issue-meta">
                  {{ iss.record_type }} · 章 {{ iss.chapter_title || iss.chapter_id || '-' }}
                  <span v-if="hasConfidence(iss.confidence)" class="rv-conf" :class="{ 'rv-conf-low': confLow(iss.confidence) }">置信度 {{ confText(iss.confidence) }}</span>
                  <span v-else class="rv-conf rv-conf-null">待复核</span>
                </div>
              </div>
            </div>
          </div>
        </n-tab-pane>
        <n-tab-pane name="careful" :tab="`需细看 (${bySeverity.careful.length})`">
          <div class="issue-list">
            <div v-for="iss in bySeverity.careful" :key="iss.issue_id" class="issue-card" @click="open(iss)">
              <input type="checkbox" :checked="selectedIds.includes(iss.issue_id)" @click.stop="toggle(iss.issue_id)" />
              <div class="issue-body">
                <div class="issue-row"><span class="issue-type">{{ iss.issue_type }}</span><span class="sev" :class="'s-' + iss.severity">{{ iss.severity }}</span></div>
                <div class="issue-desc">{{ (iss.description || '').slice(0, 90) }}</div>
                <div class="issue-meta">
                  {{ iss.record_type }} · 章 {{ iss.chapter_title || iss.chapter_id || '-' }}
                  <span v-if="hasConfidence(iss.confidence)" class="rv-conf" :class="{ 'rv-conf-low': confLow(iss.confidence) }">置信度 {{ confText(iss.confidence) }}</span>
                  <span v-else class="rv-conf rv-conf-null">待复核</span>
                </div>
              </div>
            </div>
          </div>
        </n-tab-pane>
        <n-tab-pane name="high" :tab="`高风险 (${bySeverity.high.length})`">
          <div class="issue-list">
            <div v-for="iss in bySeverity.high" :key="iss.issue_id" class="issue-card" @click="open(iss)">
              <input type="checkbox" :checked="selectedIds.includes(iss.issue_id)" @click.stop="toggle(iss.issue_id)" />
              <div class="issue-body">
                <div class="issue-row"><span class="issue-type">{{ iss.issue_type }}</span><span class="sev" :class="'s-' + iss.severity">{{ iss.severity }}</span></div>
                <div class="issue-desc">{{ (iss.description || '').slice(0, 90) }}</div>
                <div class="issue-meta">
                  {{ iss.record_type }} · 章 {{ iss.chapter_title || iss.chapter_id || '-' }}
                  <span v-if="hasConfidence(iss.confidence)" class="rv-conf" :class="{ 'rv-conf-low': confLow(iss.confidence) }">置信度 {{ confText(iss.confidence) }}</span>
                  <span v-else class="rv-conf rv-conf-null">待复核</span>
                </div>
              </div>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>
    </template>

    <!-- 裁决工作台（modal） -->
    <ReviewDesk v-if="activeIssue" :issue="activeIssue" :book-id="bookId" @done="onReviewDone" @close="activeIssue = null" />
  </div>
</template>

<script setup>
// 复核队列（P1 · 工作台「复核」tab）：GET validation → 三 tab（快速批/需细看/高风险）+ 批量 repersist。
// issue 卡点击打开 ReviewDesk 裁决；空态 EmptyState。
import { ref, computed, onMounted } from 'vue'
import { listValidation, repersistBook, confirmIssues } from '../api/novel'
import EmptyState from '../components/EmptyState.vue'
import AppIcon from '../components/AppIcon.vue'
import ReviewDesk from './ReviewDesk.vue'

const props = defineProps({
  bookId: { type: String, required: true },
  bookName: { type: String, default: '' },
})

const issues = ref([])
const loading = ref(true)
const activeTab = ref('fast')
const selectedIds = ref([])
const activeIssue = ref(null)
const batchBusy = ref(false)
const batchMsg = ref('')

const total = computed(() => issues.value.length)
// severity → tab 分组：info=快速批 / warning=需细看 / critical=高风险
// 组内按置信度升序（NULL=未复核 排最前 = 最需复核）；同值保持原序（Array.sort 稳定）
const bySeverity = computed(() => {
  const g = { fast: [], careful: [], high: [] }
  for (const i of issues.value) {
    if (i.severity === 'critical') g.high.push(i)
    else if (i.severity === 'warning') g.careful.push(i)
    else g.fast.push(i)
  }
  g.fast.sort(byConf)
  g.careful.sort(byConf)
  g.high.sort(byConf)
  return g
})

// 置信度解析：缺失/非数字 → null（视为未复核）
function parseConfidence(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
// 组内次级排序键：NULL 视为 -Infinity（最需复核，排最前），其余数值升序
function byConf(a, b) {
  const ka = parseConfidence(a.confidence) ?? -Infinity
  const kb = parseConfidence(b.confidence) ?? -Infinity
  if (ka === kb) return 0
  return ka < kb ? -1 : 1
}
// 卡片置信度展示辅助：是否有值 / 两位小数字符串 / 是否低分（<0.6）
function hasConfidence(v) { return parseConfidence(v) !== null }
function confText(v) { return parseConfidence(v).toFixed(2) }
function confLow(v) { return (parseConfidence(v) ?? 1) < 0.6 }

async function load() {
  loading.value = true
  try {
    const r = await listValidation(props.bookId)
    issues.value = r.pending || []
  } catch { issues.value = [] }
  loading.value = false
}
function open(iss) { activeIssue.value = iss }
function toggle(id) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter(x => x !== id) : [...selectedIds.value, id]
}
async function runRepersist() {
  batchBusy.value = true; batchMsg.value = ''
  try {
    const r = await repersistBook(props.bookId, selectedIds.value)
    batchMsg.value = `批量重写：成功 ${r.succeeded}/${r.total}`
    selectedIds.value = []
    await load()
  } catch (e) { batchMsg.value = String(e.message || e) }
  finally { batchBusy.value = false }
}
// 低风险待确认集合：全部 pending 中 severity==='info'（快速批 tab 组），与勾选无关
const infoIds = computed(() => issues.value
  .filter(i => i.severity === 'info')
  .map(i => i.issue_id))
// 一键确认低风险：info 全部 confirm → 清勾选 + 刷新队列（原 issue 不再 pending，队列自动减少）
async function runConfirmInfo() {
  batchBusy.value = true; batchMsg.value = ''
  try {
    const r = await confirmIssues(props.bookId, infoIds.value)
    batchMsg.value = `批量确认低风险：成功 ${r.succeeded}/${r.total}`
    selectedIds.value = []
    await load()
  } catch (e) { batchMsg.value = String(e.message || e) }
  finally { batchBusy.value = false }
}
async function onReviewDone() {
  activeIssue.value = null
  await load()
}

onMounted(load)
</script>

<style scoped>
.review-view { padding: 20px; overflow-y: auto; }
.rv-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.rv-count { font-size: 13px; color: #666; }
.rv-batch { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; padding: 8px 12px; background: #e8f0fe; border-radius: 8px; flex-wrap: wrap; }
.rv-info-count { font-size: 11px; opacity: .85; }
.rv-msg { color: #1a73e8; font-size: 13px; }
.rv-msg-none { color: #999; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.issue-list { display: flex; flex-direction: column; gap: 8px; }
.issue-card { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; cursor: pointer; }
.issue-card:hover { border-color: #1a73e8; }
.issue-card input[type=checkbox] { margin-top: 3px; }
.issue-body { flex: 1; min-width: 0; }
.issue-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.issue-type { font-size: 12px; color: #555; background: #f5f5f5; padding: 1px 8px; border-radius: 8px; }
.sev { font-size: 11px; padding: 1px 8px; border-radius: 8px; }
.s-info { background: #e8f0fe; color: #1a73e8; }
.s-warning { background: #fef7e0; color: #b06000; }
.s-critical { background: #fce8e6; color: #d93025; }
.issue-desc { font-size: 13px; color: #333; line-height: 1.4; }
.issue-meta { font-size: 12px; color: #999; margin-top: 4px; }
.rv-conf { display: inline-block; margin-left: 10px; padding: 0 8px; border-radius: 8px; font-size: 11px; line-height: 18px; background: #e8f0fe; color: #1a73e8; }
.rv-conf-low { background: #fef7e0; color: #b06000; }
.rv-conf-null { background: #f5f5f5; color: #999; }
</style>
