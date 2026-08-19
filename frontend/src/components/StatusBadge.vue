<template>
  <span class="badge" :class="'b-' + status">{{ label ?? status }}</span>
</template>

<script setup>
// 状态徽章（阶段一 · P4）：统一 b-pending/running/done/failed/processing 样式。
// 样式逐字沿用原三处重复定义，视觉零变化；label 可选（默认显示 status，供"加载中"等自定义文案）。
defineProps({
  status: { type: String, required: true },
  label: { type: String, default: null },
})
</script>

<style scoped>
.badge { padding: 2px 10px; border-radius: 10px; font-size: 12px; }
.b-pending { background: #fef7e0; color: #b06000; }
.b-processing { background: #e8f0fe; color: #1a73e8; }
.b-running { background: #e8f0fe; color: #1a73e8; }
.b-done { background: #e6f4ea; color: #188038; }
.b-failed { background: #fce8e6; color: #d93025; }
</style>
