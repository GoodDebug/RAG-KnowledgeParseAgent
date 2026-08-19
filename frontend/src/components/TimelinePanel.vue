<template>
  <div class="timeline-panel">
    <!-- 加载中：timeline 拉取未返回 → 轻提示头部，不闪现「暂无事件」空态（弱网下避免误导） -->
    <div v-if="loading && !events.length" class="tl-head">
      <AppIcon name="Clock" :size="14" />
      <span class="tl-title">时间线</span>
      <span class="tl-count">加载中…</span>
    </div>

    <!-- 空态：已加载但无事件 → EmptyState（有章节但无事件同样走这里） -->
    <EmptyState
      v-else-if="showEmpty"
      icon="📅" title="暂无事件"
      desc="本书暂无时间线事件，先对本书执行解构。"
    />

    <template v-else>
      <!-- 头部：图标 + 「时间线」 + 全书事件条数 -->
      <div class="tl-head">
        <AppIcon name="Clock" :size="14" />
        <span class="tl-title">时间线</span>
        <span class="tl-count">全书事件 {{ events.length }} 条</span>
      </div>

      <!-- 泳道区：横向滚动，纵向固定高度（块绝对定位不撑高） -->
      <div class="tl-scroll">
        <div class="tl-inner">
          <!-- currentChapter 竖线：仅位置指示（不参与过滤），left = currentChapter/maxChapter*100% -->
          <div v-if="lineLeft != null" class="tl-cur" :style="{ left: lineLeft + '%' }"></div>

          <!-- 上行泳道：stage 大阶段 -->
          <div class="tl-lane">
            <div
              v-for="ev in stageEvents"
              :key="ev.event_id"
              class="tl-block tl-block-stage"
              :class="{ 'is-selected': ev.event_id === selectedEventId }"
              :style="blockStyle(ev)"
              :title="blockTooltip(ev)"
              @click="emit('select', ev)"
            >
              <div class="tl-block-head">
                <span class="tl-block-title">{{ ev.event_title }}</span>
                <span class="tl-badge tl-badge-stage">{{ levelLabel(ev) }}</span>
              </div>
              <div class="tl-block-meta">{{ chapterLabel(ev) }}</div>
              <div v-if="ev.involved_entities?.length" class="tl-chips">
                <span v-for="ent in ev.involved_entities" :key="ent" class="tl-chip">{{ ent }}</span>
              </div>
            </div>
          </div>

          <!-- 下行泳道：event 具体事件 -->
          <div class="tl-lane">
            <div
              v-for="ev in eventEvents"
              :key="ev.event_id"
              class="tl-block"
              :class="{ 'is-selected': ev.event_id === selectedEventId }"
              :style="blockStyle(ev)"
              :title="blockTooltip(ev)"
              @click="emit('select', ev)"
            >
              <div class="tl-block-head">
                <span class="tl-block-title">{{ ev.event_title }}</span>
                <span class="tl-badge tl-badge-event">{{ levelLabel(ev) }}</span>
              </div>
              <div class="tl-block-meta">{{ chapterLabel(ev) }}</div>
              <div v-if="ev.involved_entities?.length" class="tl-chips">
                <span v-for="ent in ev.involved_entities" :key="ent" class="tl-chip">{{ ent }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
// 时间线泳道面板（大修002 · Sub4 §4.2/§4.3）：纯展示哑组件，不 import api、不发请求。
// 数据（getTimeline 响应 events）由 GraphView 作为唯一编排者经 props 注入；
// 组件只做双泳道渲染 + 章节轴定位 + selectedEvent 高亮 + currentChapter 竖线 + 空态兜底。
// 点击事件块 → emit('select', event) 上抛整个 event 对象，由 GraphView 触发联动。
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  events: { type: Array, default: () => [] },                    // backend getTimeline 响应 events 数组
  selectedEventId: { type: String, default: null },              // 选中事件 id（?event=），命中该块高亮
  currentChapter: { type: Number, default: 0 },                  // 当前章号（竖线位置指示，非过滤）
  maxChapter: { type: Number, default: 0 },                      // 全书最大章号（章节轴刻度基准）
  loading: { type: Boolean, default: false },                    // timeline 拉取中（避免空态闪现，对齐 EntityCardPanel loading 惯例）
})
const emit = defineEmits(['select'])                             // 点击事件块 → 上抛整个 event 对象

// 空态：无事件（含未传/空数组）→ EmptyState；有章节但无事件同样兜底
const showEmpty = computed(() => !Array.isArray(props.events) || props.events.length === 0)

// 双泳道划分：stage 上行 / 其余（event）下行；顺序沿用后端 global_sort 升序（filter 保序）
const stageEvents = computed(() => props.events.filter(e => e.event_level === 'stage'))
const eventEvents = computed(() => props.events.filter(e => e.event_level !== 'stage'))

// 当前章竖线位置：currentChapter/maxChapter*100%；maxChapter<=0 时不渲染
const lineLeft = computed(() => {
  const max = props.maxChapter > 0 ? props.maxChapter : 0
  if (!max) return null
  const c = Math.min(Math.max(props.currentChapter || 0, 0), max)
  return (c / max) * 100
})

// 数值安全化：缺失/NaN/负数一律归一为 0（start_chapter=0 → left=0 左对齐，不误判缺失）
function safeNum(v) {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : 0
}

// 事件块定位（章节轴）：left = start_chapter/maxChapter*100%；width = 章节跨度(end-start)/maxChapter*100%。
// end 缺失或 < start → 视作单章（width=0%，由 CSS min-width 兜底保证标题可读）。
function blockStyle(ev) {
  const max = props.maxChapter > 0 ? props.maxChapter : 1
  const s = safeNum(ev.start_chapter)
  let e = safeNum(ev.end_chapter)
  if (!Number.isFinite(Number(ev.end_chapter)) || e < s) e = s
  return { left: `${(s / max) * 100}%`, width: `${((e - s) / max) * 100}%` }
}

// 章节区间文案：end 缺失/0（进行中）或异常 end<start → 视作单章（第 X 章）；
// 起止相同只显单章；start=0（起点不明）→ 「第 X 章起」；否则区间「第 X~Y 章」
function chapterLabel(ev) {
  const s = safeNum(ev.start_chapter)
  const rawE = Number(ev.end_chapter)
  let e = Number.isFinite(rawE) && rawE > 0 ? rawE : s
  if (e < s) e = s
  if (e === s) return `第 ${s} 章`
  if (s === 0) return `第 ${e} 章起`
  return `第 ${s}~${e} 章`
}

// level 徽章文案：stage → 阶段 / 其余 → 事件
function levelLabel(ev) {
  return ev.event_level === 'stage' ? '阶段' : '事件'
}

// 悬停 tooltip：事件内容 + 时间描述（防长文挤布局；v-text/插值渲染，杜绝注入）
function blockTooltip(ev) {
  const parts = []
  if (ev.event_content) parts.push(ev.event_content)
  if (ev.time_desc) parts.push(`时间：${ev.time_desc}`)
  return parts.join('\n')
}
</script>

<style scoped>
.timeline-panel { border: 1px solid #e8e8e8; border-radius: 12px; background: #fff; padding: 14px 16px; }
.tl-head { display: flex; align-items: center; gap: 6px; color: #1a73e8; font-size: 13px; font-weight: 600; margin-bottom: 10px; }
.tl-count { margin-left: auto; color: #999; font-size: 12px; font-weight: 400; }
/* 横向滚动；固定纵向高度（块绝对定位不撑高，防密集挤压） */
.tl-scroll { overflow-x: auto; }
.tl-inner { position: relative; }
.tl-lane { position: relative; height: 82px; background: #fafbfc; border-radius: 6px; }
.tl-lane + .tl-lane { margin-top: 6px; }
/* currentChapter 竖线：跨两条泳道，仅位置指示 */
.tl-cur { position: absolute; top: -2px; bottom: -2px; width: 2px; background: #1a73e8; opacity: .7; pointer-events: none; z-index: 5; }
/* 事件块：absolute 按 left%/width% 沿章节轴定位；min-width 兜底保证标题可读 */
.tl-block {
  position: absolute; top: 4px; bottom: 4px; min-width: 130px;
  padding: 4px 8px; box-sizing: border-box; border: 1px solid #e0e4e8; border-radius: 6px;
  background: #fff; overflow: hidden; cursor: pointer; font-size: 12px; color: #333;
  display: flex; flex-direction: column; gap: 2px; transition: border-color .15s, background .15s;
}
.tl-block:hover { border-color: #1a73e8; }
/* selected：边框 + 底色高亮 */
.tl-block.is-selected { border: 1.5px solid #1a73e8; background: #eaf2ff; }
.tl-block-head { display: flex; align-items: center; gap: 4px; min-width: 0; }
.tl-block-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
/* stage 大阶段标题加粗，与 event 常规区分 */
.tl-block-stage .tl-block-title { font-weight: 700; }
.tl-badge { flex: 0 0 auto; font-size: 10px; line-height: 1; padding: 2px 5px; border-radius: 999px; }
/* level 标识：stage 深色加粗 / event 常规 */
.tl-badge-stage { background: #1f2d3d; color: #fff; font-weight: 700; }
.tl-badge-event { background: #eef1f5; color: #55606e; font-weight: 400; }
.tl-block-meta { color: #999; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* involved_entities chips：单行省略（溢出裁剪 + 省略号） */
.tl-chips { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.tl-chip {
  display: inline-block; background: #f5f5f5; border: 1px solid #e0e0e0; color: #555;
  font-size: 10px; line-height: 1; padding: 2px 6px; border-radius: 999px; margin-right: 4px;
}
</style>
