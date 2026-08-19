<template>
  <!-- 语义分层分段按钮（Sub3 · §4.2 哑组件）：全部/人物/势力/地点/事件/伏笔/规则 -->
  <div class="semantic-layer-bar" role="group" aria-label="语义分层">
    <button
      v-for="layer in layers"
      :key="layer.key"
      type="button"
      class="slb-btn"
      :class="{ 'is-active': layer.key === active }"
      :aria-pressed="layer.key === active"
      @click="emit('change', layer.key)"
    >
      <AppIcon :name="layer.icon" :size="14" />
      <span class="slb-label">{{ layer.label }}</span>
    </button>
  </div>
</template>

<script setup>
// 语义分层条（Sub3 · §4.2 纯展示哑组件）：一维分段按钮，图层由 GraphView 传入并作为唯一数据编排者。
// 本组件不 import api、不发请求——只做「高亮 active + 点击上报 change(key)」的纯视图职责。
import AppIcon from './AppIcon.vue'

defineProps({
  layers: { type: Array, default: () => [] },   // [{key, label, icon}]，GraphView 传入
  active: { type: String, default: '' },        // 当前激活 layer.key
})
const emit = defineEmits(['change'])
</script>

<style scoped>
.semantic-layer-bar {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border: 1px solid #d0d0d0;
  border-radius: 8px;
  background: #fff;
}
.slb-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #555;
  font-size: 13px;
  cursor: pointer;
  transition: background .15s, color .15s;
}
.slb-btn:hover { background: #f2f6fd; }
.slb-btn.is-active {
  background: #1a73e8;
  color: #fff;
}
.slb-label { line-height: 1; white-space: nowrap; }
</style>
