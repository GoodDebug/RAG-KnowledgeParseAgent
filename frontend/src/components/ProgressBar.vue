<template>
  <div class="progress-bar"><div class="progress-fill" :style="{ width: pct + '%' }"></div></div>
</template>

<script setup>
// 进度条（阶段一 · P4）：统一 progress-bar/fill 样式。
// 样式逐字沿用原两处重复定义，视觉零变化；percent 0-100 驱动填充宽度。
import { computed } from 'vue'
const props = defineProps({ percent: { type: Number, default: 0 } })
const pct = computed(() => Math.max(0, Math.min(100, Number(props.percent) || 0)))
</script>

<style scoped>
.progress-bar { flex: 1; height: 8px; background: #eee; border-radius: 4px; overflow: hidden; }
.progress-fill { height: 100%; background: #1a73e8; transition: width .3s; }
</style>
