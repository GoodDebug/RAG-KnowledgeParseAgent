<template>
  <!-- 三态徽标（子任务 05 展示层）：fact=蓝 / inference=琥珀 / review=待复核灰 -->
  <span :class="threeStateClass(state)" :title="threeStateTitle(state)">{{ threeStateText(state) }}</span>
</template>

<script setup>
// 纯展示哑组件：props.state → 三态徽标（复用 cf-badge 弱视觉，样式在全局/父组件定义）。
import { threeStateClass, threeStateText, threeStateTitle } from '../utils/threeState'

defineProps({
  state: { type: String, default: 'fact' },   // fact / inference / review
})
</script>
