<template>
  <div class="session-list">
    <button class="new-btn" @click="emit('create')"><AppIcon name="Plus" :size="14" /> 新建会话</button>
    <div v-if="!sessions.length" class="empty">暂无会话，点击「新建会话」开始</div>
    <div
      v-for="s in sessions"
      :key="s.key"
      :class="['session-item', { active: s.key === activeKey }]"
      @click="emit('select', s.key)"
    >
      <div class="session-title">{{ s.title || '新会话' }}</div>
      <div class="session-time">{{ formatTime(s.created_at) }}</div>
    </div>
  </div>
</template>

<script setup>
// 会话列表侧栏（Spec-E）：纯展示组件，业务状态由父组件 ChatView 管理（无 Pinia）
import AppIcon from './AppIcon.vue'
const props = defineProps({
  sessions: { type: Array, default: () => [] },  // [{id,title,created_at,key}]
  activeKey: { type: String, default: '' },
})
const emit = defineEmits(['select', 'create'])

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = n => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
</script>

<style scoped>
.session-list { display: flex; flex-direction: column; gap: 6px; padding: 10px; height: 100%; overflow-y: auto; }
.new-btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 8px; border: 1px solid #1a73e8; background: #1a73e8; color: #fff; border-radius: 8px; cursor: pointer; font-size: 13px; }
.new-btn:hover { opacity: .9; }
.empty { color: #aaa; font-size: 13px; padding: 16px 4px; text-align: center; }
.session-item { padding: 8px 10px; border-radius: 8px; cursor: pointer; background: #fff; border: 1px solid #eee; transition: background .15s; }
.session-item:hover { background: #f5f5f5; }
.session-item.active { background: #e8f0fe; border-color: #1a73e8; }
.session-title { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-time { font-size: 11px; color: #999; margin-top: 2px; }
</style>
