<template>
  <div class="kb-view">
    <!-- 左：10 类型边栏（列表/管理视角） -->
    <div class="kb-side">
      <button v-for="t in TYPES" :key="t.key" :class="['kb-type', { active: t.key === activeType }]" @click="selectType(t.key)">
        <AppIcon :name="t.icon" :size="14" /> {{ t.label }}
        <span class="kb-total">{{ totals[t.key] ?? '·' }}</span>
      </button>
    </div>

    <!-- 右：筛选行 + 分页列表 -->
    <div class="kb-main">
      <div class="kb-filters">
        <input v-for="f in activeConfig.filters" :key="f.key" v-model="filters[f.key]" class="kb-input"
               :placeholder="f.label" @keydown.enter="search" />
        <button class="btn btn-primary" :disabled="loading" @click="search"><AppIcon name="Search" :size="14" /> 查询</button>
        <button class="btn" @click="reset">重置</button>
        <span v-if="msg" class="kb-msg">{{ msg }}</span>
      </div>

      <n-data-table v-if="!loading && items.length" :columns="columns" :data="items" size="small"
                    :bordered="false" :scroll-x="1200" class="kb-table" />
      <EmptyState v-else-if="!loading" icon="📭" title="无数据" desc="切换左侧类型或调整筛选条件。" />

      <n-pagination v-if="total > pageSize" v-model="page" :page-size="pageSize" :item-count="total"
                    @update:page="load" class="kb-pager" />
    </div>
  </div>
</template>

<script setup>
// 知识库数据浏览（P1 补强 · 工作台「数据」tab）：左=10 类型边栏，右=分页/筛选列表。
// 列表/管理视角（横向全量），与 P2 方向 D 的详情/档案视角互补；数据源 browseBook（{total, items}）。
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { h } from 'vue'
import { browseBook } from '../api/novel'
import EmptyState from '../components/EmptyState.vue'
import AppIcon from '../components/AppIcon.vue'

const props = defineProps({
  bookId: { type: String, required: true },
  bookName: { type: String, default: '' },
})
const route = useRoute()
const router = useRouter()

// 10 类型配置：label/icon/cols(展示列)/filters(筛选字段)
const TYPES = [
  { key: 'entity', label: '实体', icon: 'User',
    cols: [{ key: 'entity_name', title: '名称' }, { key: 'entity_type', title: '类型' }, { key: 'first_chapter_index', title: '首章' }, { key: 'last_chapter_index', title: '末章' }, { key: 'is_active', title: '活跃' }],
    filters: [{ key: 'name', label: '名称' }, { key: 'entity_type', label: '类型' }, { key: 'is_active', label: '活跃' }] },
  { key: 'entity_snapshot', label: '章节实体状态', icon: 'Camera',
    cols: [{ key: 'entity_name', title: '实体' }, { key: 'entity_type', title: '类型' }, { key: 'chapter_index', title: '章' }, { key: 'status_desc', title: '状态' }],
    filters: [{ key: 'entity_name', label: '实体' }, { key: 'entity_type', label: '类型' }, { key: 'chapter_index', label: '章' }] },
  { key: 'relation', label: '实体间关系', icon: 'Share2',
    cols: [{ key: 'source_name', title: '源实体' }, { key: 'target_name', title: '目标实体' }, { key: 'relation_type', title: '关系' }, { key: 'valid_period', title: '时效' }, { key: 'start_chapter', title: '起章' }, { key: 'end_chapter', title: '止章' }],
    filters: [{ key: 'entity_name', label: '实体' }, { key: 'relation_type', label: '关系' }, { key: 'valid_period', label: '时效' }, { key: 'chapter_from', label: '起章≥' }, { key: 'chapter_to', label: '止章≤' }] },
  { key: 'timeline_event', label: '时间线剧情', icon: 'Clock',
    cols: [{ key: 'event_title', title: '事件' }, { key: 'event_level', title: '级别' }, { key: 'global_sort', title: '全局序' }, { key: 'start_chapter', title: '起章' }, { key: 'end_chapter', title: '止章' }],
    filters: [{ key: 'event_level', label: '级别' }, { key: 'title', label: '标题' }, { key: 'chapter_from', label: '起章≥' }, { key: 'chapter_to', label: '止章≤' }] },
  { key: 'location', label: '地点', icon: 'MapPin',
    cols: [{ key: 'location_name', title: '地点' }, { key: 'location_level', title: '层级' }, { key: 'first_chapter_index', title: '首章' }, { key: 'last_chapter_index', title: '末章' }],
    filters: [{ key: 'name', label: '地点' }, { key: 'location_level', label: '层级' }] },
  { key: 'foreshadowing', label: '伏笔埋点', icon: 'Eye',
    cols: [{ key: 'title', title: '伏笔' }, { key: 'status', title: '状态' }, { key: 'setup_chapter', title: '埋设章' }, { key: 'reveal_chapter', title: '揭示章' }],
    filters: [{ key: 'status', label: '状态' }, { key: 'title', label: '标题' }] },
  { key: 'conflict', label: '冲突', icon: 'Swords',
    cols: [{ key: 'conflict_title', title: '冲突' }, { key: 'conflict_type', title: '类型' }, { key: 'side_a', title: '甲' }, { key: 'side_b', title: '乙' }, { key: 'current_status', title: '状态' }],
    filters: [{ key: 'title', label: '标题' }, { key: 'conflict_type', label: '类型' }, { key: 'current_status', label: '状态' }] },
  { key: 'rule', label: '设定/规则/世界观', icon: 'ScrollText',
    cols: [{ key: 'rule_name', title: '规则' }, { key: 'rule_type', title: '类型' }, { key: 'subject_ability', title: '主体能力' }, { key: 'valid_from_chapter', title: '生效章' }, { key: 'valid_to_chapter', title: '失效章' }],
    filters: [{ key: 'name', label: '名称' }, { key: 'rule_type', label: '类型' }] },
  { key: 'alias', label: '实体别名', icon: 'Tags',
    cols: [{ key: 'alias_name', title: '别名' }, { key: 'alias_type', title: '类型' }, { key: 'entity_id', title: '实体ID' }],
    filters: [{ key: 'alias_name', label: '别名' }, { key: 'alias_type', label: '类型' }] },
  { key: 'validation', label: '复核疑点', icon: 'ClipboardCheck',
    cols: [{ key: 'record_type', title: '记录类型' }, { key: 'issue_type', title: '问题' }, { key: 'severity', title: '严重度' }, { key: 'status', title: '状态' }, { key: 'chapter_title', title: '章节' }],
    filters: [{ key: 'status', label: '状态' }, { key: 'severity', label: '严重度' }, { key: 'issue_type', label: '问题' }] },
]

// 8 个内容类型（有目标知识行置信度，展示「置信度」列）；alias / validation 为映射/复核视图，不加列
const CONF_TYPES = ['entity', 'entity_snapshot', 'relation', 'timeline_event', 'location', 'foreshadowing', 'conflict', 'rule']

const activeType = ref('entity')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const msg = ref('')
const filters = reactive({})
const totals = reactive({})

const activeConfig = computed(() => TYPES.find(t => t.key === activeType.value) || TYPES[0])
const columns = computed(() => {
  const cols = activeConfig.value.cols.map(c => ({ key: c.key, title: c.title }))
  // 8 个内容类型加「置信度」列：有值显示数字（<0.6 低分高亮），NULL=未复核 → 「待复核」徽标
  if (CONF_TYPES.includes(activeType.value)) {
    cols.push({ key: 'confidence', title: '置信度', width: 90, align: 'center', render: renderConfidence })
  }
  if (activeType.value === 'entity') {
    // P2-2 §4.5：实体行操作列 = 「图谱」+「查看百科」双下钻（并排 flex）
    cols.push({ key: 'action', title: '', width: 150, align: 'center', render: (row) => h('div', { class: 'kb-actions' }, [
      h('button', { class: 'kb-graph', onClick: () => goGraph(row.entity_name) }, '图谱'),
      h('button', { class: 'kb-graph', onClick: () => goEntity(row.entity_name) }, '查看百科'),
    ]) })
  }
  return cols
})

// 置信度解析：缺失/非数字 → null（视为未复核）
function parseConfidence(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
// 置信度单元格渲染：NULL → 「待复核」徽标；有值 → 两位小数，<0.6 低分高亮
function renderConfidence(row) {
  const c = parseConfidence(row.confidence)
  if (c === null) return h('span', { class: 'cf-badge cf-null' }, '待复核')
  return h('span', { class: ['cf-num', c < 0.6 ? 'cf-low' : 'cf-ok'] }, c.toFixed(2))
}

async function load() {
  loading.value = true; msg.value = ''
  try {
    const params = { limit: pageSize, offset: (page.value - 1) * pageSize }
    for (const [k, v] of Object.entries(filters)) if (v !== '') params[k] = v
    const r = await browseBook(props.bookId, activeType.value, params)
    items.value = r.items || []
    total.value = r.total || 0
    totals[activeType.value] = r.total
  } catch (e) {
    items.value = []; total.value = 0; msg.value = String(e.message || e)
  } finally { loading.value = false }
}
function selectType(key) {
  if (key === activeType.value) return
  activeType.value = key
  Object.keys(filters).forEach(k => delete filters[k])
  page.value = 1
  load()
}
function search() { page.value = 1; load() }
function reset() { Object.keys(filters).forEach(k => delete filters[k]); page.value = 1; load() }
// 下钻：图谱 tab（URL query 单一事实来源：book_id + tab + entity）
function goGraph(entityName) {
  router.replace({ query: { book_id: props.bookId, tab: 'graph', entity: entityName } })
}
// 下钻：百科 tab（P2-2 §4.5，复用 goGraph 的 query 下钻模式 → ?tab=entity&entity=名）
function goEntity(entityName) {
  router.replace({ query: { book_id: props.bookId, tab: 'entity', entity: entityName } })
}

onMounted(load)
</script>

<style scoped>
.kb-view { display: flex; height: 100%; }
.kb-side { width: 180px; flex-shrink: 0; border-right: 1px solid #e8e8e8; background: #f8faff; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 4px; }
.kb-type { display: flex; align-items: center; gap: 6px; width: 100%; padding: 8px 10px; border: none; border-radius: 8px; background: transparent; cursor: pointer; font-size: 13px; color: #555; text-align: left; }
.kb-type:hover { background: #e8f0fe; }
.kb-type.active { background: #e8f0fe; color: #1a73e8; font-weight: 600; }
.kb-total { margin-left: auto; font-size: 11px; color: #999; }
.kb-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 10px; padding: 16px; overflow-y: auto; }
.kb-filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.kb-input { width: 150px; padding: 7px 10px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 13px; outline: none; }
.kb-input:focus { border-color: #1a73e8; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.kb-msg { color: #d93025; font-size: 13px; }
.kb-table { flex: 1; }
.kb-pager { justify-content: center; }
.kb-actions { display: flex; gap: 4px; justify-content: center; }   /* 实体行「图谱」+「查看百科」并排 */
.kb-graph { border: 1px solid #1a73e8; color: #1a73e8; background: #fff; border-radius: 6px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.cf-badge { display: inline-block; padding: 1px 8px; border-radius: 8px; font-size: 12px; background: #f5f5f5; color: #999; }
.cf-num { font-size: 12px; font-weight: 600; }
.cf-ok { color: #1a73e8; }
.cf-low { color: #b06000; background: #fef7e0; padding: 1px 6px; border-radius: 8px; }
</style>
