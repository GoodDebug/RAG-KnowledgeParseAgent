<template>
  <div class="chapter-slider">
    <!-- 原生 range 滑块：仅 @change（松手/回车）提交，禁 @input 逐像素防 fetch 风暴 -->
    <input
      class="cs-input"
      type="range"
      min="1"
      :max="max"
      :value="value"
      :disabled="max < 1"
      :title="currentTitle"
      @change="onChange"
    />
    <span class="cs-label">第 {{ value }}/{{ max }} 章</span>
  </div>
</template>

<script setup>
// 章节滑块（大修002 · Sub-3 §4.2 组件契约）：纯展示哑组件。
// 原生 range 滑块 + 「第 N/max 章」标签；悬停 title 显示当前章节标题（tooltipTitles 取当前位）。
// 数据全部经 props 进入，仅 emit change(Number)，不 import api、不发请求（GraphView 为唯一编排者）。
import { computed } from 'vue'

const props = defineProps({
  max: { type: Number, default: 0 },                 // 最大章数（listBookChapters 结果长度）
  value: { type: Number, default: 1 },               // 当前章号（1 基，与 URL ?chapter= 一致）
  tooltipTitles: { type: Array, default: () => [] }, // 章节标题数组（按章序 0 基，缺省兜底「第 N 章」）
})

const emit = defineEmits(['change'])

// 当前章节标题：tooltipTitles 按章序 0 基下标，value 为 1 基章号 → value - 1 定位
const currentTitle = computed(() => {
  const idx = props.value - 1
  return props.tooltipTitles?.[idx] || `第 ${props.value} 章`
})

// 仅 @change 提交：值变化才 emit（点滑轨松手未动不重复提交），避免父级冗余刷新
function onChange(e) {
  const n = Number(e.target.value)
  if (Number.isFinite(n) && n !== props.value) emit('change', n)
}
</script>

<style scoped>
.chapter-slider { display: inline-flex; align-items: center; gap: 10px; }
.cs-input { accent-color: #1a73e8; width: 160px; cursor: pointer; }
.cs-input:disabled { opacity: .6; cursor: not-allowed; }
.cs-label { min-width: 76px; font-size: 13px; color: #555; white-space: nowrap; }
</style>
