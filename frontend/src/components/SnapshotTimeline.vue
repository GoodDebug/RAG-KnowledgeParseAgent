<template>
  <div class="snapshot-timeline">
    <!-- 空态：无解构快照 → 提示该实体尚未解构（每实体每章一行的快照集为空） -->
    <EmptyState v-if="!rows.length" icon="🧬" title="该实体无解构快照" />

    <!-- 有快照：垂直时间轴，每章一行（章号 + 状态描述 + 属性摘要 + 置信度徽标） -->
    <ol v-else class="st-list">
      <li
        v-for="r in rows"
        :key="r.chapter_index"
        class="st-row"
        :class="{ 'is-current': r.current, 'is-jump': r.jump }"
      >
        <!-- 主行：第 N 章 · status_desc · 属性摘要（key: value 拼接，纯插值无 v-html） -->
        <div class="st-main">
          <span class="st-chapter">第 {{ r.chapter_index }} 章</span>
          <span class="st-status">{{ r.status_desc }}</span>
          <span v-if="r.growth" class="st-growth">{{ r.growth }}</span>
          <span v-if="r.attrs" class="st-attrs">{{ r.attrs }}</span>
        </div>

        <!-- 侧边：异常状态跳变红警 + 置信度徽标（NULL 待复核 / <0.6 低分 / 正常蓝） -->
        <div class="st-side">
          <span
            v-if="r.jump"
            class="st-jump-tip"
            :title="`第 ${r.chapter_index} 章状态相对上一章跳变：${r.status_desc}`"
          >
            <AppIcon name="TriangleAlert" :size="12" /> 异常状态跳变
          </span>
          <span :class="['st-conf', r.confClass]" :title="r.confTitle">{{ r.confText }}</span>
        </div>
      </li>
    </ol>
  </div>
</template>

<script setup>
// 快照演化时间轴（大修002 · P2-2 实体百科）：纯展示哑组件，不 import api、不发请求。
// snapshots（getSnapshots 响应，每实体每章一行）由编排容器注入；本组件只做垂直时间轴渲染：
//   每章一行（第 N 章 · status_desc + attributes 摘要）；相邻章 status_desc 突变 → 红警（异常状态跳变）；
//   置信度徽标（NULL 待复核 / <0.6 低分 / 正常）；currentChapter 命中行高亮；空态兜底。
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  snapshots: { type: Array, default: () => [] },   // 快照数组（元素含 chapter_index/status_desc/attributes/confidence/review_status）
  currentChapter: { type: Number, default: 0 },    // 当前章号：命中 chapter_index 的行高亮
  growthMarkers: { type: Object, default: () => ({}) }, // 子任务 05 成长线：{chapter_index → '突破'|'转折'}，命中行标小标签
})

// 按 chapter_index 升序排序（浅拷贝；后端通常已按章序返回，此处防御性兜底，保证红警比较时序正确）
const ordered = computed(() => {
  const arr = Array.isArray(props.snapshots) ? props.snapshots.slice() : []
  return arr.sort((a, b) => (Number(a.chapter_index) || 0) - (Number(b.chapter_index) || 0))
})

// 属性摘要：attributes（对象 或 MySQL JSON 串）→ "key: value · key: value" 拼接；空 → ''
// 后端 raw SQL 读 MySQL JSON 列返回字符串 → 先 JSON.parse 再拼接；非 JSON 串原样展示。
// 仅文本插值渲染（杜绝 v-html），防止注入；空值键（null/undefined/''）剔除
function attrsText(attrs) {
  let obj = attrs
  if (typeof attrs === 'string') {
    try { obj = JSON.parse(attrs) } catch { return attrs || '' }
  }
  if (!obj || typeof obj !== 'object') return ''
  const parts = Object.entries(obj)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}: ${v}`)
  return parts.join(' · ')
}

// 置信度解析：缺失/空串/非数字 → null（视为未复核），对齐 KnowledgeBrowserView 惯例
function parseConfidence(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
// 置信度徽标样式（复用 cf-badge/cf-null/cf-low 视觉）：NULL → 待复核（灰）；<0.6 → 低分（琥珀）；否则正常（蓝）
function confBadgeClass(v) {
  const c = parseConfidence(v)
  if (c === null) return 'cf-badge cf-null'
  return c < 0.6 ? 'cf-badge cf-low' : 'cf-badge cf-ok'
}
// 置信度徽标文案：NULL → 待复核；有值 → 两位小数
function confBadgeText(v) {
  const c = parseConfidence(v)
  return c === null ? '待复核' : c.toFixed(2)
}
// 置信度徽标悬停提示：说明当前置信度口径
function confBadgeTitle(v) {
  const c = parseConfidence(v)
  return c === null ? '置信度：未复核' : `置信度：${c.toFixed(2)}`
}

// 异常状态跳变：当前行 status_desc 相对上一行（按章序）发生突变 → true（首行无前值，不算）
function isJump(i) {
  if (i <= 0) return false
  const cur = ordered.value[i]
  const prev = ordered.value[i - 1]
  return !!cur.status_desc && cur.status_desc !== prev.status_desc
}

// 行数据预计算：一次遍历补齐 属性摘要 / 置信度样式·文案·提示 / 红警 / 当前行，模板零逻辑
const rows = computed(() =>
  ordered.value.map((s, i) => ({
    chapter_index: Number(s.chapter_index) || 0,
    status_desc: s.status_desc || '',
    attrs: attrsText(s.attributes),
    confClass: confBadgeClass(s.confidence),
    confText: confBadgeText(s.confidence),
    confTitle: confBadgeTitle(s.confidence),
    jump: isJump(i),
    current: Number(s.chapter_index) === Number(props.currentChapter),
    growth: props.growthMarkers?.[Number(s.chapter_index)] || '',   // 子任务 05 成长线标签
  }))
)
</script>

<style scoped>
.snapshot-timeline { border: 1px solid #e8e8e8; border-radius: 12px; background: #fff; padding: 14px 16px; }
/* 垂直时间轴列表：无默认序号，每章一行 */
.st-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.st-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 8px 10px; border-radius: 8px; background: #fafbfc; border-left: 3px solid transparent;
  font-size: 13px;
}
/* 当前章：蓝底 + 蓝左边框高亮 */
.st-row.is-current { background: #eaf2ff; border-left-color: #1a73e8; }
/* 异常状态跳变：红警底 + 红左边框（与当前章同现时以红警为主） */
.st-row.is-jump { background: #fdecea; border-left-color: #d93025; }
.st-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.st-chapter { flex-shrink: 0; color: #1a73e8; font-weight: 600; }
.st-status { color: #333; font-weight: 500; }
/* 子任务 05 成长线标签：弱视觉小徽章（突破/转折），不与红警/置信度冲突 */
.st-growth {
  flex-shrink: 0; color: #7b1fa2; background: #f3ecfa;
  border: 1px solid #d8c6ee; font-size: 11px; line-height: 1; padding: 3px 7px; border-radius: 999px; white-space: nowrap;
}
/* 属性摘要：次要灰字，超长省略（key: value 拼接已由脚本保证纯文本） */
.st-attrs { color: #999; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.st-side { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
/* 异常状态跳变红警标签 */
.st-jump-tip {
  display: inline-flex; align-items: center; gap: 4px; color: #d93025; background: #fff;
  border: 1px solid #d93025; font-size: 11px; line-height: 1; padding: 3px 7px; border-radius: 999px; white-space: nowrap;
}
/* 置信度徽标（对齐 KnowledgeBrowserView cf-* 视觉：灰 / 琥珀 / 蓝） */
.cf-badge { display: inline-block; padding: 1px 8px; border-radius: 8px; font-size: 12px; background: #f5f5f5; color: #999; white-space: nowrap; }
.cf-null { background: #f5f5f5; color: #999; }
.cf-low { color: #b06000; background: #fef7e0; }
.cf-ok { color: #1a73e8; background: #e8f0fe; }
</style>
