<template>
  <div class="graph-view">
    <!-- 顶部工具条：搜索 + 章节滑块（时态）+ 语义分层（§4.3） -->
    <div class="gv-toolbar">
      <!-- 实体下拉：数据源 = 知识库浏览 entity 列表（解决「不知有哪些实体无从下手」） -->
      <div class="gv-search">
        <select v-model="entity" class="gv-input gv-select" @change="onSearch">
          <option value="" disabled>选择实体…</option>
          <option v-for="n in entityOptions" :key="n" :value="n">{{ n }}</option>
        </select>
        <input v-model="entity" class="gv-input" placeholder="或输入实体名" @keydown.enter="onSearch" />
        <button class="btn btn-primary" :disabled="!entity.trim() || loading" @click="onSearch">
          <AppIcon name="Search" :size="14" /> 探索
        </button>
      </div>
      <ChapterSlider :max="sliderMax" :value="currentChapterValue" :tooltip-titles="tooltipTitles" @change="onChapter" />
      <SemanticLayerBar :layers="layers" :active="activeLayer" @change="onLayerChange" />
    </div>

    <!-- 空态：无章节（滑块禁用 + EmptyState，§4.5） -->
    <EmptyState v-if="noChapter" icon="📖" title="暂无章节"
      desc="本书暂无解构章节，请先在「解构」tab 对本书执行一键解构，再回来探索图谱。" />

    <!-- 空态：无实体选择 / 名称解析失败（§4.5/§4.6）。rawGraph 存在时不算空态——
         分层滤空走 .gv-main（实体卡保留，图区单独提示，§4.7） -->
    <EmptyState v-else-if="!loading && !rawGraph && !nodes.length && !lines.length" icon="📈"
      :title="hasEntity ? '未找到实体' : '选择实体开始探索'"
      :desc="hasEntity
        ? '该实体名称未能解析到图谱实体，请从下拉中选择或检查名称。'
        : '基于 GET query 返回的实体/关系，默认加载「选中实体一跳子图」，防止大书全量渲染卡顿。'" />

    <!-- 主体：图 1-hop | 实体卡（右侧） -->
    <div v-else class="gv-main">
      <div class="gv-canvas">
        <!-- @ready/@onReady 双保险：relation-graph v3 通过 ready 事件给出实例，实例才有 setJsonData；
             节点点击 @node-click/@onNodeClick 双绑（沿用 ready 双保险，事件名不确定） -->
        <RelationGraph
          ref="graphRef"
          :options="options"
          @ready="onReady"
          @onReady="onReady"
          @node-click="onNodeClick"
          @onNodeClick="onNodeClick"
        >
          <!-- 节点 slot：#node 沿用现有样式；type 着色 + 选中高亮 -->
          <template #node="{ node }">
            <div
              class="rg-node"
              :class="{ 'is-selected': node.id === selectedNodeId }"
              :style="{ borderColor: colorFor(node.data?.type) }"
            >{{ node.text }}</div>
          </template>
        </RelationGraph>
        <p v-if="msg" class="gv-msg">{{ msg }}</p>
        <!-- 分层滤空：当前层滤除全部节点 → 图区提示切层；实体卡数据独立于图层，不重拉、不清空 -->
        <p v-else-if="layerFilteredEmpty" class="gv-msg">当前层无节点（实体卡仍保留）——切换到「全部」查看全部实体</p>
      </div>
      <div class="gv-card">
        <EntityCardPanel :card="card" :loading="loading" :entity-name="currentEntityValue" @view-source="onViewSource" />
      </div>
    </div>

    <!-- 时间线面板（Sub-4 §4.4）：内嵌泳道，插槽在 .gv-main 与 .gv-evidence 之间；全书视图不随实体选择门控，
         !noChapter 保证无章节不渲染；点事件 → onTimelineSelect 跳章 + 写 ?event= 高亮 -->
    <TimelinePanel
      v-if="!noChapter"
      :events="timeline"
      :selected-event-id="currentEventValue"
      :current-chapter="currentChapterValue"
      :max-chapter="sliderMax"
      :loading="timelineLoading"
      @select="onTimelineSelect"
    />

    <!-- 底部：原文证据（实体未选 / 无章节时不渲染；evidence:null 由 EvidencePanel 内部空态兜底） -->
    <div v-if="hasEntity && !noChapter" ref="evidenceRef" class="gv-evidence">
      <EvidencePanel :evidence="evidence" :aliases="evidenceAliases" :chapter="currentChapterValue" />
    </div>
  </div>
</template>

<script setup>
// 图谱统一视图容器（大修002 · Sub-3）：GraphView 为唯一数据编排者。
// 顶部 搜索+章节滑块(时态)+语义分层；主体 图 1-hop | 实体卡；底部 原文证据。
// 消费 Sub-2 Knowledge API（listBookChapters/getKnowledgeGraph/getEntityCard/getEvidence）
// + browseBook('entity'/'alias')；四面板纯展示（props 进、不碰 API）。
// 时态走 URL query 单点：currentEntity/currentChapter 由 route.query 派生，
// watch(route.query) → run() 单点收敛（滑块/深链/后退/前进全统一），交互写状态全走 setSelection。
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import RelationGraph from '@relation-graph/vue'
import { listBookChapters, getKnowledgeGraph, getEntityCard, getEvidence, browseBook, getTimeline } from '../api/novel'
import EmptyState from '../components/EmptyState.vue'
import AppIcon from '../components/AppIcon.vue'
import ChapterSlider from '../components/ChapterSlider.vue'
import SemanticLayerBar from '../components/SemanticLayerBar.vue'
import EntityCardPanel from '../components/EntityCardPanel.vue'
import EvidencePanel from '../components/EvidencePanel.vue'
import TimelinePanel from '../components/TimelinePanel.vue'   // 时间线泳道面板（Sub-4：纯展示哑组件）

// 父级注入：book 上下文 + 共享选择写入（URL query 单一事实来源，GraphView 不直接 router.replace）
const props = defineProps({
  bookId: { type: String, required: true },
  bookName: { type: String, default: '' },
  setSelection: { type: Function, required: true },   // 合并 entity/chapter/event 到 route.query（Sub-2）
})
const route = useRoute()

// query 值可能为重复参数数组（?chapter=2&chapter=3），归一为字符串
function norm(v) { return Array.isArray(v) ? (v[0] || '') : (v || '') }

const entity = ref('')
const entityOptions = ref([])          // 下拉实体名列表（browseBook('entity')）
const nodes = ref([])                  // relation-graph 节点（id = entity_id）
const lines = ref([])                  // relation-graph 线
const loading = ref(false)
const msg = ref('')
const card = ref(null)                 // 实体卡（getEntityCard 响应）
const evidence = ref(null)             // 原文证据窗口（getEvidence 响应；未出现=null）
const rawGraph = ref(null)             // 后端原始图谱响应（切层复用，不重拉）
const selectedNodeId = ref(null)       // 节点选中高亮
const sliderMax = ref(0)               // 章节滑块 max（listBookChapters 最大 chapter_index）
const tooltipTitles = ref([])          // 章节标题（滑块悬停）
const chaptersLoaded = ref(false)      // loadChapters 完成标记（避免初挂载误判「无章节」）
const activeLayer = ref('all')         // 语义分层激活项（本地视图偏好，不写 URL）
const evidenceRef = ref(null)          // 底部证据面板 DOM（查看原文滚动定位）
const graphRef = ref(null)             // relation-graph 组件实例引用
const timeline = ref([])               // 全书时间线事件（getTimeline 响应 events；onMounted 一次性全量拉取，Sub-4 §4.4）
const timelineLoading = ref(true)      // 时间线拉取中（传给 TimelinePanel，防「暂无事件」空态闪现）

let graphInstance = null               // relation-graph 实例（@ready 给出，实例才有 setJsonData）
let pendingData = null                 // 图未就绪时暂存的数据，@ready 触发后补设
let fetchToken = 0                     // 竞态守卫：过期响应不覆盖
let graphRootId = null                 // 映射后的 rootId（分层滤除 center 时回退 filtered[0]）
const idCache = new Map()              // 名称 → entity_id 反查缓存（按 bookId 隔离）

// 语义分层配置（§4.7）：全部/人物/势力/地点/事件/伏笔/规则（AppIcon 名已在 NAME_MAP 白名单）
const layers = [
  { key: 'all', label: '全部', icon: 'Tags' },
  { key: 'human', label: '人物', icon: 'User' },
  { key: 'faction', label: '势力', icon: 'Swords' },
  { key: 'location', label: '地点', icon: 'MapPin' },
  { key: 'event', label: '事件', icon: 'Clock' },
  { key: 'foreshadowing', label: '伏笔', icon: 'Sparkles' },
  { key: 'rule', label: '规则', icon: 'ScrollText' },
]

// relation-graph 选项（节点 slot 着色、线标签在上、禁拖拽缩放保持稳定）
const options = {
  defaultExpandMarkerPosition: 'none',
  draggable: false,
  zoomToFitByDoubleClick: false,
  nodeUseSlot: true,
  lineUseTextPosition: 'top',
  defaultJunctionPoint: 'border',
  defaultNodeWidth: 120,
  defaultNodeHeight: 42,
  defaultLineColor: '#ccc',
  defaultLineShape: 2,
}

// 实体 type → 颜色（对齐现有视觉 token；EntityCardPanel 徽章同款配色）
function colorFor(type) {
  const map = {
    human: '#1a73e8', faction: '#e67e22', item: '#188038', skill: '#b06000',
    spirit: '#7b1fa2', task: '#d93025', rule: '#888',
  }
  return map[type] || '#999'
}

// 当前章节值：URL ?chapter= 缺省时回退当前态 sliderMax（与后端 _max_chapter 缺省语义一致，不污染 URL）
const currentChapterValue = computed(() => Number(norm(route.query.chapter)) || sliderMax.value)
// 当前实体名（route.query 派生；本地 ref entity 仅承载输入框/下拉文本）
const currentEntityValue = computed(() => norm(route.query.entity))
// 当前选中事件 id（?event= 派生；滑章保留、切实体/搜索清空，Sub-4 §4.4/§4.5）
const currentEventValue = computed(() => norm(route.query.event))
// 是否已在 URL 选择实体（决定空态标题 / 证据区显隐）
const hasEntity = computed(() => !!currentEntityValue.value)
// 无章节：chapters 已加载且 max<1 → 滑块禁用 + EmptyState
const noChapter = computed(() => chaptersLoaded.value && sliderMax.value < 1)
// 分层滤空：有图数据但当前层滤除全部节点 → 图区提示（实体卡保留，§4.7）
const layerFilteredEmpty = computed(() => !!rawGraph.value && !nodes.value.length && !lines.value.length)
// 证据高亮词 = 实体名 + 别名（EvidencePanel 契约要求实体名并入 aliases）
const evidenceAliases = computed(() => {
  const name = currentEntityValue.value
  const aliases = (card.value?.aliases || []).filter(Boolean)
  return name ? [name, ...aliases] : aliases
})

// @ready（v3 通过该事件给出真实实例）；同时绑 @ready 与 @onReady 双保险（事件名不确定）
function onReady(instance) {
  graphInstance = instance
  if (pendingData) {
    try { instance.setJsonData(pendingData) } catch (e) { msg.value = String(e.message || e) }
    pendingData = null
  }
}

// 加载章节（滑块 max/title）：chapter_index 升序，取最大值作 max，标题按章序 0 基存
async function loadChapters() {
  try {
    const chapters = await listBookChapters(props.bookId) || []
    sliderMax.value = chapters.reduce((m, c) => Math.max(m, Number(c.chapter_index) || 0), 0)
    tooltipTitles.value = chapters.map(c => c.chapter_title).filter(Boolean)
  } catch {
    sliderMax.value = 0
    tooltipTitles.value = []
  } finally {
    chaptersLoaded.value = true
  }
}

// 加载实体下拉（知识库浏览 entity 列表）
async function loadEntities() {
  try {
    const r = await browseBook(props.bookId, 'entity', { limit: 100 })
    entityOptions.value = (r.items || []).map(it => it.entity_name).filter(Boolean)
  } catch { entityOptions.value = [] }
}

// 加载全书时间线（Sub-4 §4.4）：onMounted 一次性全量拉取（空参 → 后端返回全部事件），
// 滑章/点实体/切层不重拉；切书经 :key 重挂 → onMounted 自动重拉新书时间线
async function loadTimeline() {
  try {
    const r = await getTimeline(props.bookId, {})
    timeline.value = r?.events || []
  } catch { timeline.value = [] }
  finally { timelineLoading.value = false }
}

// §4.6 名称 → entity_id 反查：idCache 命中直返；未命中走 alias 浏览（entity 浏览 select 不含 entity_id）
async function resolveEntityId(name) {
  const key = `${props.bookId}:${name}`
  if (idCache.has(key)) return idCache.get(key)
  let id = null
  try {
    const r = await browseBook(props.bookId, 'alias', { alias_name: name, limit: 1 })
    id = r?.items?.[0]?.entity_id || null
  } catch { /* 解析失败 → null（未找到实体） */ }
  idCache.set(key, id)
  return id
}

// §4.5 时态联动链路（单点收敛）：watch(route.query) 与 onMounted 深链都走这里
async function run() {
  const name = norm(route.query.entity)
  if (!name) { clearAll(); return }                // 无实体 → 图/卡/证据全清 + EmptyState
  // 章节缺省 = 当前态；clamp 进 [1, sliderMax]（防滑块越界 / 后端空章兜底）
  const chapter = Math.min(Math.max(Number(norm(route.query.chapter)) || sliderMax.value, 1), sliderMax.value || 1)
  // 竞态守卫：进 run 即占位（先于首个 await），名称反查期间的新 run 会使本 run 过期
  const token = ++fetchToken
  loading.value = true
  const entityId = await resolveEntityId(name)
  if (token !== fetchToken) return                 // 名称反查期间已被更新 run 抢占 → 过期丢弃
  if (!entityId) {                                 // 名称解析失败 → 未找到实体空态
    loading.value = false
    nodes.value = []; lines.value = []
    card.value = null; evidence.value = null
    rawGraph.value = null
    selectedNodeId.value = null
    msg.value = '未找到实体'
    return
  }
  try {
    // 三路并行：图 1-hop + 实体卡 + 原文证据（全 as-of chapter）
    const [graph, cardData, evidenceData] = await Promise.all([
      getKnowledgeGraph(props.bookId, { entityId, chapter }),
      getEntityCard(props.bookId, entityId, { chapter }),
      getEvidence(props.bookId, entityId, { chapter }),
    ])
    if (token !== fetchToken) return               // 竞态守卫：过期响应直接丢弃，不覆盖新结果
    rawGraph.value = graph
    applyLayer(graph)                              // §4.4 映射 + §4.7 分层过滤 → render()
    card.value = cardData
    evidence.value = evidenceData
    msg.value = ''
  } catch (e) {
    if (token !== fetchToken) return
    msg.value = String(e.message || e)
    nodes.value = []; lines.value = []
    card.value = null; evidence.value = null
    rawGraph.value = null
  } finally {
    if (token === fetchToken) loading.value = false
  }
}

// §4.4 数据映射 + §4.7 语义分层：后端响应 → relation-graph {rootId, nodes, lines}
// 节点 id 一律用后端 entity_id（名称可跨类型重复，不能作 id）；边字段 edges → lines
function applyLayer(graph) {
  const layer = activeLayer.value
  const gNodes = graph?.nodes || []
  const gEdges = graph?.edges || []
  const centerId = graph?.center?.entity_id
  // 分层过滤：location 兼容 place/地点 双标签；event/foreshadowing 不触发过滤（P0 提示层）；
  // 未列入层的类型只出现在「全部」
  let fNodes = gNodes
  if (layer === 'location') {
    fNodes = gNodes.filter(n => n.type === 'location' || n.type === 'place' || n.type === '地点')
  } else if (layer !== 'all' && layer !== 'event' && layer !== 'foreshadowing') {
    fNodes = gNodes.filter(n => n.type === layer)
  }
  // 边仅保留两端都命中层的（防断线悬空）
  const idSet = new Set(fNodes.map(n => n.entity_id))
  const fEdges = gEdges.filter(l => idSet.has(l.from) && idSet.has(l.to))
  // rootId：center 被滤除时回退 filtered[0]（实体卡数据独立于图层，不重拉、不跟随滤除）
  graphRootId = (idSet.has(centerId) || !fNodes.length) ? centerId : fNodes[0].entity_id
  nodes.value = fNodes.map(n => ({ id: n.entity_id, text: n.name, data: { entity_id: n.entity_id, name: n.name, type: n.type } }))
  lines.value = fEdges.map(l => ({ from: l.from, to: l.to, text: l.relation_type, data: { relation_type: l.relation_type, weight: l.weight } }))
  render()
}

// 渲染到 relation-graph：canvas 由 v-else 在 nextTick 才挂载、@ready 才触发——
// 必须先等一帧，否则 graphInstance 为 null → setJsonData 空操作 → 空白画布
async function render() {
  const data = { rootId: graphRootId || nodes.value[0]?.id, nodes: nodes.value, lines: lines.value }
  await nextTick()
  const inst = graphInstance || graphRef.value?.getInstance?.()
  if (inst) {
    try { inst.setJsonData(data) } catch (e) { msg.value = String(e.message || e) }
  } else {
    pendingData = data   // @ready 尚未触发 → 暂存，由 onReady 补设
  }
}

// 语义分层切换（§4.7）：本地偏好不写 URL；切层复用已拉 graphData，不重拉
function onLayerChange(key) {
  activeLayer.value = key
  if (key === 'event' || key === 'foreshadowing') {
    // P0 数据源缺口：事件/伏笔数据在 timeline/foreshadowing 表，未入 entity 图 → 提示，不触发过滤
    msg.value = '事件/伏笔数据见时间线面板（Sub-4/P1）与实体卡 evidence'
    return
  }
  msg.value = ''
  if (rawGraph.value) applyLayer(rawGraph.value)
}

// 节点点击：选中高亮 + 写 URL（全走 setSelection，GraphView 不直接 router.replace）
function onNodeClick(node) {
  if (!node?.data) return
  selectedNodeId.value = node.id
  props.setSelection({ entity: node.data.name, chapter: currentChapterValue.value })
}

// 章节滑块提交（仅 @change，防 fetch 风暴）：写 URL → watch → run 全视图 as-of N
// Sub-4 §4.4：滑章保留选中事件（currentEventValue 一并写入），泳道高亮不丢
function onChapter(n) {
  props.setSelection({ entity: currentEntityValue.value, chapter: n, event: currentEventValue.value })
}

// 时间线事件点击（Sub-4 §4.4）：跳转到事件起始章并写 ?event= 高亮；
// start_chapter clamp 进 [1, sliderMax]（防滑块越界）；不自动设实体（顶层设计 §3.3 契约）
function onTimelineSelect(event) {
  const ch = Math.min(Math.max(Number(event?.start_chapter) || 1, 1), sliderMax.value || 1)
  props.setSelection({ entity: currentEntityValue.value, chapter: ch, event: event.event_id })
}

// 搜索/下拉选实体：名称 → setSelection → watch → run（名称解析在 run 内统一反查）
function onSearch() {
  const name = entity.value.trim()
  if (!name) return
  props.setSelection({ entity: name, chapter: currentChapterValue.value })
}

// 实体卡「查看原文」：把底部证据面板平滑滚入视口（EvidencePanel 自身按当前章渲染）
function onViewSource() {
  evidenceRef.value?.scrollIntoView({ behavior: 'smooth' })
}

// 清空所有视图（无实体 / 切书重挂前兜底）→ EmptyState
function clearAll() {
  fetchToken++                                   // 使在途 run 过期（无实体清空后不让旧 fetch 覆盖回来）
  nodes.value = []
  lines.value = []
  card.value = null
  evidence.value = null
  rawGraph.value = null
  selectedNodeId.value = null
  msg.value = ''
  loading.value = false
}

// 深链直达：URL 已带 entity 时立即跑一次（?entity=五条汐&chapter=2 → 直达该实体该章）
function initPipeline() {
  if (norm(route.query.entity)) run()
}

// 单点 watch：滑块/深链/后退/前进全走这里（数组 getter 形式，避免单 getter 返回新数组的 always-fire 陷阱）
watch(
  [() => route.query.entity, () => route.query.chapter],
  run
)

onMounted(async () => {
  loadEntities()      // 实体下拉（与章节并行，无需等待）
  loadTimeline()      // 全书时间线（Sub-4 §4.4）：与章节并行、不 await（fire-and-forget 全量拉取）
  await loadChapters() // 滑块 max/title 先就绪，保证深链缺省章节取到真实当前态
  initPipeline()      // 读深链（query.entity）→ 首次 run
})
</script>

<style scoped>
.graph-view { flex: 1; min-height: 0; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.gv-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.gv-search { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.gv-input { width: 240px; padding: 8px 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 14px; outline: none; }
.gv-input:focus { border-color: #1a73e8; }
.gv-select { width: 180px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 14px; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
/* 主体行：图(左) | 实体卡(右) */
.gv-main { display: flex; gap: 12px; flex: 1; min-height: 0; }
.gv-canvas { flex: 1; min-height: 420px; border: 1px solid #e8e8e8; border-radius: 12px; overflow: hidden; position: relative; }
.gv-card { width: 340px; flex-shrink: 0; display: flex; min-height: 0; }
/* Vue3 scoped 下子组件根元素带父 scopeId，.gv-card > * 可命中 → 实体卡内部滚动不塌陷 */
.gv-card > * { flex: 1; min-height: 0; }
.gv-evidence { flex-shrink: 0; scroll-margin-top: 12px; }
.gv-msg { color: #1a73e8; font-size: 13px; margin-top: 8px; }
.rg-node { padding: 6px 12px; border: 2px solid; border-radius: 8px; background: #fff; font-size: 13px; white-space: nowrap; }
.rg-node.is-selected { box-shadow: 0 0 0 2px #1a73e8; background: #eaf2ff; }
</style>
