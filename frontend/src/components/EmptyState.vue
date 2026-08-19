<template>
  <div class="empty-state">
    <div class="empty-icon"><AppIcon :name="icon" :size="40" /></div>
    <p class="empty-title">{{ title }}</p>
    <p v-if="desc" class="empty-desc">{{ desc }}</p>
    <button v-if="actionText" class="empty-action" @click="action && action()">{{ actionText }}</button>
  </div>
</template>

<script setup>
// 空态组件（前端 P0）：无 job / 无数据时展示 + CTA（防白屏，引导用户行动）。
// icon 兼容：字符串（emoji 或 lucide 图标名，经 AppIcon 映射）→ 统一图标；签名不变，调用方无需改。
import AppIcon from './AppIcon.vue'
defineProps({
  icon: { type: String, default: '📭' },
  title: { type: String, default: '暂无数据' },
  desc: { type: String, default: '' },
  actionText: { type: String, default: '' },
  action: { type: Function, default: null },
})
</script>

<style scoped>
.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; padding: 48px 20px; color: #999; text-align: center;
}
.empty-icon { display: flex; justify-content: center; font-size: 40px; }
.empty-title { font-size: 15px; color: #555; }
.empty-desc { font-size: 13px; max-width: 360px; }
.empty-action {
  margin-top: 8px; padding: 8px 20px; border: none; border-radius: 6px;
  background: #1a73e8; color: #fff; cursor: pointer; font-size: 14px;
}
.empty-action:hover { background: #1765cc; }
</style>
