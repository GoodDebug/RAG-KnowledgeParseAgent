<template>
  <div class="appearance-heatmap">
    <!-- 头部：标题 + 出场章节数 -->
    <div class="ah-head">
      <span class="ah-title">出场分布</span>
      <span v-if="blocks.length" class="ah-count">{{ blocks.length }} 章出场</span>
    </div>

    <!-- 水平章节轴（0~maxChapter）：出场章节块 fill，未出场留白 -->
    <div class="ah-track-wrap">
      <div class="ah-track">
        <!-- currentChapter 竖线：仅位置指示（视觉），left = currentChapter/maxChapter*100% -->
        <div v-if="lineLeft != null" class="ah-cur" :style="{ left: lineLeft + '%' }"></div>
        <!-- 出场章节块：absolute 按 chapter_index/maxChapter 定位，hover 显示「第 N 章」 -->
        <span
          v-for="b in blocks"
          :key="b.chapter_index"
          class="ah-block"
          :style="{ left: b.left + '%' }"
          :data-tip="`第 ${b.chapter_index} 章`"
        ></span>
      </div>
      <!-- 刻度：0（左）～ 第 maxChapter 章（右） -->
      <div class="ah-scale">
        <span class="ah-min">0</span>
        <span class="ah-max">第 {{ maxChapter }} 章</span>
      </div>
    </div>
  </div>
</template>

<script setup>
// 出场分布热力图（大修002 · P2-2 实体百科）：纯展示哑组件，不 import api、不发请求。
// snapshots 即「出场章节集」（getSnapshots，每实体每章一行，有行即该章出场）；
// 按 chapter_index/maxChapter 沿水平章节轴放置填充块（absolute 定位），未出场章节留白；
// currentChapter 竖线仅作位置指示（不参与过滤）；块 hover 显示章节号 tooltip；零依赖 scoped CSS。
import { computed } from 'vue'

const props = defineProps({
  snapshots: { type: Array, default: () => [] },  // 快照数组（取 chapter_index 判定出场章节）
  currentChapter: { type: Number, default: 0 },   // 当前章号（竖线位置指示）
  maxChapter: { type: Number, default: 0 },       // 全书最大章号（章节轴刻度基准）
})

// 章节轴基准：maxChapter<=0 时降级为 1，避免除零（此时轴上无有效出场块）
const max = computed(() => (props.maxChapter > 0 ? props.maxChapter : 1))

// 出场章节块：去重 + 升序 + 定位 left = chapter_index/max*100%（非法/越界章号剔除）
const blocks = computed(() => {
  const seen = new Set()
  for (const s of Array.isArray(props.snapshots) ? props.snapshots : []) {
    const c = Number(s.chapter_index)
    if (Number.isFinite(c) && c >= 0 && c <= max.value) seen.add(c)
  }
  return [...seen].sort((a, b) => a - b).map((c) => ({ chapter_index: c, left: (c / max.value) * 100 }))
})

// currentChapter 竖线位置：clamp 到 [0, maxChapter]；maxChapter<=0 时不渲染
const lineLeft = computed(() => {
  if (!(props.maxChapter > 0)) return null
  const c = Math.min(Math.max(props.currentChapter || 0, 0), props.maxChapter)
  return (c / props.maxChapter) * 100
})
</script>

<style scoped>
.appearance-heatmap { border: 1px solid #e8e8e8; border-radius: 12px; background: #fff; padding: 14px 16px; }
.ah-head { display: flex; align-items: center; gap: 6px; color: #1a73e8; font-size: 13px; font-weight: 600; margin-bottom: 10px; }
.ah-count { margin-left: auto; color: #999; font-size: 12px; font-weight: 400; }
/* 内边距兜底：边缘章节块（0/maxChapter）translatex(-50%) 后不被裁切 */
.ah-track-wrap { padding: 0 8px; }
.ah-track { position: relative; height: 24px; background: #fafbfc; border-radius: 6px; }
/* currentChapter 竖线：仅位置指示，不拦截鼠标 */
.ah-cur { position: absolute; top: -3px; bottom: -3px; width: 2px; background: #1a73e8; opacity: .7; pointer-events: none; z-index: 5; }
/* 出场章节块：absolute 沿章节轴定位，translateX(-50%) 居中于章节位置 */
.ah-block {
  position: absolute; top: 4px; bottom: 4px; width: 10px; transform: translateX(-50%);
  background: #1a73e8; border-radius: 3px;
}
/* 块 tooltip（第 N 章）：CSS ::after + attr(data-tip)，零依赖 */
.ah-block::after {
  content: attr(data-tip);
  position: absolute; bottom: calc(100% + 6px); left: 50%; transform: translateX(-50%);
  background: #333; color: #fff; font-size: 11px; line-height: 1; padding: 4px 8px; border-radius: 4px;
  white-space: nowrap; opacity: 0; pointer-events: none; transition: opacity .15s; z-index: 10;
}
.ah-block:hover::after { opacity: 1; }
/* 刻度：左右两端 0 / 第 maxChapter 章 */
.ah-scale { display: flex; justify-content: space-between; margin-top: 4px; font-size: 11px; color: #999; }
</style>
