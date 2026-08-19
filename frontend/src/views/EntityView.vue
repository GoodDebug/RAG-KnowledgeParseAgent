<template>
  <div class="entity-view">
    <!-- 顶部工具条：实体搜索（下拉 browse entity + 输入 + 探索）+ 章节滑块（时态，缺省=当前态） -->
    <div class="ev-toolbar">
      <div class="ev-search">
        <!-- 实体下拉：数据源 = 知识库浏览 entity 列表（解决「不知有哪些实体无从下手」） -->
        <select v-model="entity" class="ev-input ev-select" @change="onSearch">
          <option value="" disabled>选择实体…</option>
          <option v-for="n in entityOptions" :key="n" :value="n">{{ n }}</option>
        </select>
        <input v-model="entity" class="ev-input" placeholder="或输入实体名" @keydown.enter="onSearch" />
        <button class="btn btn-primary" :disabled="!entity.trim() || loading" @click="onSearch">
          <AppIcon name="Search" :size="14" /> 探索
        </button>
      </div>
      <ChapterSlider :max="sliderMax" :value="currentChapterValue" :tooltip-titles="tooltipTitles" @change="onChapter" />
    </div>

    <!-- 空态：无章节（滑块禁用 + EmptyState） -->
    <EmptyState v-if="noChapter" icon="📖" title="暂无章节"
      desc="本书暂无解构章节，请先在「解构」tab 对本书执行一键解构，再回来探索实体百科。" />

    <!-- 空态：未选择实体（名称解析失败 / 无卡片由各面板内部兜底） -->
    <EmptyState v-else-if="!loading && !hasEntity" icon="📇" title="选择实体开始探索"
      desc="从下拉选择或输入实体名，查看实体在各章节的状态演化、出场分布与解构快照。" />

    <!-- 主体：实体卡（左）| 快照演化时间轴 + 出场热力图（右/下） -->
    <div v-else class="ev-main">
      <!-- 名称解析失败 / 请求错误：顶部提示（面板各自空态兜底） -->
      <p v-if="msg" class="ev-msg">{{ msg }}</p>
      <div class="ev-row">
        <div class="ev-card">
          <EntityCardPanel :card="card" :loading="loading" :entity-name="currentEntityValue" :empty-desc="'请在上方选择或搜索实体查看档案。'" @view-source="onViewSource" />
        </div>
        <div class="ev-side">
          <div ref="timelineRef" class="ev-timeline">
            <SnapshotTimeline :snapshots="snapshots" :current-chapter="currentChapterValue" />
          </div>
          <AppearanceHeatmap :snapshots="snapshots" :current-chapter="currentChapterValue" :max-chapter="sliderMax" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 实体百科档案面容器（大修002 · P2-2）：EntityView 为唯一数据编排者。
// 顶部 实体搜索（下拉 browse entity + 输入 + 探索）+ 章节滑块(时态)；主体 实体卡 + 快照演化时间轴 + 出场热力图。
// 消费 Sub-1 Knowledge API（getEntityCard/getSnapshots）+ browseBook('entity'/'alias') + listBookChapters；
// 三面板纯展示（props 进、不碰 API）。沿用 Sub-3 GraphView 编排模式：
//   resolveEntityId（idCache 按 bookId 隔离 + browse('alias') 反查）→ fetchToken 竞态守卫 →
//   watch([entity, chapter]) 单点收敛 run()（滑块/深链/后退/前进全统一）；交互写状态全走 setSelection。
// 时态走 URL query 单点：currentEntityValue/currentChapterValue 由 route.query 派生；
// 快照 getSnapshots 全量一次拉取喂 时间轴 + 热力图（出场=快照存在），切章不单独重拉。
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { listBookChapters, getEntityCard, getSnapshots, browseBook } from '../api/novel'
import EmptyState from '../components/EmptyState.vue'
import AppIcon from '../components/AppIcon.vue'
import ChapterSlider from '../components/ChapterSlider.vue'
import EntityCardPanel from '../components/EntityCardPanel.vue'
import SnapshotTimeline from '../components/SnapshotTimeline.vue'
import AppearanceHeatmap from '../components/AppearanceHeatmap.vue'

// 父级注入：book 上下文 + 共享选择写入（URL query 单一事实来源，EntityView 不直接 router.replace）
const props = defineProps({
  bookId: { type: String, required: true },
  bookName: { type: String, default: '' },
  setSelection: { type: Function, required: true },   // 合并 entity/chapter 到 route.query（Sub-2）
})
const route = useRoute()

// query 值可能为重复参数数组（?chapter=2&chapter=3），归一为字符串
function norm(v) { return Array.isArray(v) ? (v[0] || '') : (v || '') }

const entity = ref('')               // 输入框/下拉文本（本地承载；展示名取 currentEntityValue）
const entityOptions = ref([])        // 下拉实体名列表（browseBook('entity')）
const loading = ref(false)
const msg = ref('')
const card = ref(null)               // 实体卡（getEntityCard 响应；null = 空态）
const snapshots = ref([])            // 快照演化集（getSnapshots 响应 snapshots；每实体每章一行）
let lastEntityId = null              // 快照缓存键：实体变更才重拉（切章不重拉，§5 性能）
const sliderMax = ref(0)             // 章节滑块 max（listBookChapters 最大 chapter_index）
const tooltipTitles = ref([])        // 章节标题（滑块悬停）
const chaptersLoaded = ref(false)    // loadChapters 完成标记（避免初挂载误判「无章节」）
const timelineRef = ref(null)        // 快照时间轴 DOM（「查看原文」滚动定位）

let fetchToken = 0                   // 竞态守卫：过期响应不覆盖
const idCache = new Map()            // 名称 → entity_id 反查缓存（按 bookId 隔离）

// 当前章节值：URL ?chapter= 缺省时回退当前态 sliderMax（与后端 _max_chapter 缺省语义一致，不污染 URL）
const currentChapterValue = computed(() => Number(norm(route.query.chapter)) || sliderMax.value)
// 当前实体名（route.query 派生；本地 ref entity 仅承载输入框/下拉文本）
const currentEntityValue = computed(() => norm(route.query.entity))
// 是否已在 URL 选择实体（决定空态标题 / 主体显隐）
const hasEntity = computed(() => !!currentEntityValue.value)
// 无章节：chapters 已加载且 max<1 → 滑块禁用 + EmptyState
const noChapter = computed(() => chaptersLoaded.value && sliderMax.value < 1)

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

// §4.4 名称 → entity_id 反查：idCache 命中直返；未命中走 alias 浏览（entity 浏览 select 不含 entity_id）
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

// §4.4 时态联动链路（单点收敛）：watch(route.query) 与 onMounted 深链都走这里
async function run() {
  const name = norm(route.query.entity)
  if (!name) { clearAll(); return }                // 无实体 → 卡片/快照全清 + EmptyState
  // 章节缺省 = 当前态；clamp 进 [1, sliderMax]（防滑块越界 / 后端空章兜底）
  const chapter = Math.min(Math.max(Number(norm(route.query.chapter)) || sliderMax.value, 1), sliderMax.value || 1)
  // 竞态守卫：进 run 即占位（先于首个 await），名称反查期间的新 run 会使本 run 过期
  const token = ++fetchToken
  loading.value = true
  const entityId = await resolveEntityId(name)
  if (token !== fetchToken) return                 // 名称反查期间已被更新 run 抢占 → 过期丢弃
  if (!entityId) {                                 // 名称解析失败 → 未找到实体空态
    loading.value = false
    card.value = null
    snapshots.value = []
    msg.value = '未找到实体'
    return
  }
  try {
    // 快照全量一次拉取，仅实体变更时重拉（切章复用已拉快照，§5 性能「切章不重拉时间轴」）
    const snapNeed = lastEntityId !== entityId
    lastEntityId = entityId
    if (snapNeed) snapshots.value = []              // 防旧实体快照残留闪现
    const [cardData, snapData] = await Promise.all([
      getEntityCard(props.bookId, entityId, { chapter }),          // 实体卡 as-of chapter（每章重拉）
      snapNeed ? getSnapshots(props.bookId, entityId) : Promise.resolve({ snapshots: snapshots.value }),
    ])
    if (token !== fetchToken) return               // 竞态守卫：过期响应直接丢弃，不覆盖新结果
    card.value = cardData
    snapshots.value = snapData?.snapshots || []
    msg.value = ''
  } catch (e) {
    if (token !== fetchToken) return
    msg.value = String(e.message || e)
    card.value = null
    snapshots.value = []
    lastEntityId = null                            // 拉取失败 → 下次 run 重拉
  } finally {
    if (token === fetchToken) loading.value = false
  }
}

// 搜索/下拉选实体：名称 → setSelection → watch → run（名称解析在 run 内统一反查）
function onSearch() {
  const name = entity.value.trim()
  if (!name) return
  props.setSelection({ entity: name, chapter: currentChapterValue.value })
}

// 章节滑块提交（仅 @change，防 fetch 风暴）：写 URL → watch → run 卡片 as-of N
function onChapter(n) {
  props.setSelection({ entity: currentEntityValue.value, chapter: n })
}

// 实体卡「查看原文」：把快照演化时间轴平滑滚入视口（P2-2 语境下原文证据未铺面板，
// 时间轴含 attributes/status_desc，滚到它辅助对照当前章节状态）
function onViewSource() {
  timelineRef.value?.scrollIntoView({ behavior: 'smooth' })
}

// 清空所有视图（无实体 / 切书重挂前兜底）→ EmptyState
function clearAll() {
  fetchToken++                                   // 使在途 run 过期（无实体清空后不让旧 fetch 覆盖回来）
  card.value = null
  snapshots.value = []
  lastEntityId = null                            // 快照缓存失效（下次 run 重拉）
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

// 输入框/下拉与 URL 实体名同步：深链/后退/前进时输入框跟随当前实体（仅同步非空值，本地输入不受打扰）
watch(() => route.query.entity, (v) => { if (v) entity.value = norm(v) })

onMounted(async () => {
  loadEntities()      // 实体下拉（与章节并行，无需等待）
  await loadChapters() // 滑块 max/title 先就绪，保证深链缺省章节取到真实当前态
  initPipeline()      // 读深链（query.entity）→ 首次 run
})
</script>

<style scoped>
.entity-view { flex: 1; min-height: 0; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.ev-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.ev-search { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ev-input { width: 240px; padding: 8px 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 14px; outline: none; }
.ev-input:focus { border-color: #1a73e8; }
.ev-select { width: 180px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 14px; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
/* 主体列：卡片(左，固定宽) | 时间轴+热力图(右，自适应) */
.ev-main { display: flex; flex-direction: column; gap: 8px; }
.ev-row { display: flex; gap: 12px; flex: 1; min-height: 0; }
.ev-card { width: 340px; flex-shrink: 0; display: flex; min-height: 0; }
/* Vue3 scoped 下子组件根元素带父 scopeId，.ev-card > * 可命中 → 实体卡内部滚动不塌陷 */
.ev-card > * { flex: 1; min-height: 0; }
.ev-side { flex: 1; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.ev-timeline { flex-shrink: 0; scroll-margin-top: 12px; }
.ev-msg { color: #1a73e8; font-size: 13px; }
</style>
