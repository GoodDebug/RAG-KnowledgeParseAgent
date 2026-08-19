<template>
  <div class="book-list">
    <div class="list-title"><AppIcon name="BookOpen" :size="14" /> 书籍</div>
    <div v-if="!books.length" class="empty">
      <p>暂无书籍</p>
      <router-link to="/ingest" class="go-ingest">去知识库导入 →</router-link>
    </div>
    <div
      v-for="b in books"
      :key="b.book_id || b.book_name"
      :class="['book-item', { active: b.book_id === activeBookId }]"
      @click="emit('select', b)"
    >
      <div class="book-name">{{ b.book_name }}</div>
      <div class="book-meta">{{ b.doc_count ?? '' }} 文件 · {{ b.chunk_total ?? '' }} Chunk</div>
    </div>
  </div>
</template>

<script setup>
// 书籍左列列表（解构工作台）：仿 SessionList 的纯展示模式。
// 业务状态（当前选中书）由父组件 NovelWorkspaceView 经 URL query 管理（无 Pinia）。
import AppIcon from './AppIcon.vue'
defineProps({
  books: { type: Array, default: () => [] },   // [{book_name, book_id, doc_count, chunk_total}]
  activeBookId: { type: String, default: '' },
})
const emit = defineEmits(['select'])
</script>

<style scoped>
.book-list { display: flex; flex-direction: column; gap: 6px; padding: 10px; height: 100%; overflow-y: auto; }
.list-title { font-size: 13px; font-weight: 600; color: #666; padding: 2px 4px 6px; }
.empty { color: #aaa; font-size: 13px; padding: 16px 4px; text-align: center; }
.go-ingest { display: inline-block; margin-top: 8px; font-size: 13px; color: #1a73e8; text-decoration: none; }
.go-ingest:hover { text-decoration: underline; }
.book-item { padding: 8px 10px; border-radius: 8px; cursor: pointer; background: #fff; border: 1px solid #eee; transition: background .15s; }
.book-item:hover { background: #f5f5f5; }
.book-item.active { background: #e8f0fe; border-color: #1a73e8; }
.book-name { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.book-meta { font-size: 11px; color: #999; margin-top: 2px; }
</style>
