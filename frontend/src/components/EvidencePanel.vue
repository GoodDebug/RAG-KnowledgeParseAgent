<template>
  <div class="evidence-panel">
    <!-- evidence=null：实体/别名未出现在该章 → 空态（不白屏） -->
    <EmptyState v-if="!evidence" icon="🔍" title="本章无该实体原文"
      :desc="`该实体/别名未出现在第 ${chapter} 章`" />
    <template v-else>
      <div class="ev-heading"><AppIcon name="ScrollText" :size="14" /> 原文证据 · 第 {{ evidence.chapter_index }} 章 {{ evidence.chapter_title }}</div>
      <!-- 受控 v-html：原文已 escapeHtml → 对转义后的实体名/别名做 <mark> 包裹，绝不直接注入未转义原文（Spec §5/§8） -->
      <p class="ev-text" v-html="highlightedText"></p>
      <div class="ev-range">位置 {{ evidence.char_start + 1 }} - {{ evidence.char_end }} 字</div>
    </template>
  </div>
</template>

<script setup>
// 原文证据面板（大修002 · Sub-3 §4.2 组件契约）：纯展示哑组件。
// 展示实体在指定章的原文证据窗口（±200 字）；实体名/别名用 <mark> 高亮；evidence=null → EmptyState。
// 数据全部经 props 进入，无 emit、不 import api、不发请求（GraphView 为唯一编排者）。
import { computed } from 'vue'
import EmptyState from './EmptyState.vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  evidence: { type: Object, default: null },   // {chapter_index, chapter_title, text, char_start, char_end}；未出现=null
  aliases: { type: Array, default: () => [] }, // 高亮词：实体名 + 别名（GraphView 编排时请把实体名并入，如 [card.name, ...card.aliases]）
  chapter: { type: Number, default: 0 },       // 当前章号（空态 desc 展示用）
})

// 与 MessageList escapeHtml 口径一致（& < > + 引号）：先整体转义原文，杜绝 HTML/脚本注入
const escapeHtml = (s) => String(s)
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;')

// 正则元字符转义：构建匹配表达式前先转义，避免别名含 . [ ] + 等破坏表达式
const escapeRegex = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// 受控高亮 HTML：原文 → escapeHtml → 对转义后的实体名/别名做 <mark> 包裹 → v-html。
// 顺序关键：先整体转义，再替换（插入的 <mark> 不在原文里，不会被二次转义）。
const highlightedText = computed(() => {
  const raw = props.evidence?.text
  if (!raw) return ''
  const escaped = escapeHtml(raw)
  // 高亮词 = 实体名 + 别名（调用方已把实体名并入 aliases）；去空/去重，长词优先（防短词先命中截断长词）
  const terms = [...new Set(props.aliases
    .map(a => (a == null ? '' : String(a).trim()))
    .filter(Boolean))]
    .map(a => escapeRegex(escapeHtml(a)))
    .sort((a, b) => b.length - a.length)
  if (!terms.length) return escaped
  return escaped.replace(new RegExp(`(${terms.join('|')})`, 'g'), '<mark>$1</mark>')
})
</script>

<style scoped>
.evidence-panel { border: 1px solid #e8e8e8; border-radius: 12px; background: #fff; padding: 14px 16px; }
.ev-heading { display: flex; align-items: center; gap: 6px; color: #1a73e8; font-size: 13px; font-weight: 600; margin-bottom: 10px; }
.ev-text { font-size: 14px; line-height: 1.7; color: #333; overflow-wrap: break-word; margin: 0 0 10px; }
/* v-html 生成的 <mark> 不受 scoped 影响，需 :deep 穿透样式 */
.ev-text :deep(mark) { background: #fef08a; color: #1a1a1a; padding: 0 2px; border-radius: 3px; }
.ev-range { font-size: 12px; color: #999; }
</style>
