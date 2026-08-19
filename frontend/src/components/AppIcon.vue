<template>
  <span class="app-icon-wrap">
    <!-- 命中映射：渲染 lucide 图标（inline-flex 垂直居中，随 currentColor） -->
    <component :is="resolved" v-if="resolved" :size="size" :stroke-width="strokeWidth" aria-hidden="true" />
    <!-- #default slot：调用方直接传 lucide 组件对象 -->
    <slot v-else-if="!isEmoji" />
    <!-- 未命中的 emoji：原样保留（G1.2 白名单） -->
    <span v-else class="app-icon-emoji">{{ name }}</span>
  </span>
</template>

<script setup>
// 统一图标入口（阶段一 · 图标库改造）：
// name 支持 emoji 字符串（经 EMOJI_MAP 映射）或 lucide 图标名（经 NAME_MAP）；
// 也可用 #default slot 直接传 lucide 组件。统一 size/stroke-width，避免各视图风格漂移。
import { computed } from 'vue'
import {
  Book, BookOpen, FolderOpen, FileText, ClipboardList, Package, Settings, Wrench,
  Microscope, Search, KeyRound, RotateCw, VolumeX, Bug, Construction, Trash2, Rocket,
  LogOut, Globe, MessageSquare, Lightbulb, Save, Upload, Inbox, CheckCircle2, XCircle,
  TriangleAlert, Plus, Download, LoaderCircle, Pause, ThumbsUp, ThumbsDown, Bot, Target,
  Flag, Sparkles, ListTodo, RefreshCw, X, Check, ClipboardCheck, Share2, Pencil,
  User, Camera, Clock, MapPin, Eye, Swords, ScrollText, Tags,
} from 'lucide-vue-next'

const props = defineProps({
  name: { type: String, default: '' },          // emoji 或 lucide 图标名
  size: { type: [Number, String], default: 18 },
  strokeWidth: { type: [Number, String], default: 1.75 },
})

// emoji → lucide 映射（前端大改 P0 · 4.1 契约；FE0F 变体选择器剥掉后匹配）
const EMOJI_MAP = {
  '📖': Book, '📚': BookOpen, '📂': FolderOpen, '📄': FileText, '📋': ClipboardList, '📦': Package,
  '⚙': Settings, '🔧': Wrench, '🔬': Microscope, '🔍': Search, '🔑': KeyRound, '🔁': RotateCw,
  '🔇': VolumeX, '🕷': Bug, '🏗': Construction, '🗑': Trash2, '🚀': Rocket, '🚪': LogOut,
  '🌐': Globe, '💬': MessageSquare, '💡': Lightbulb, '💾': Save, '📤': Upload, '📥': Inbox,
  '📭': Inbox, '✅': CheckCircle2, '❌': XCircle, '⚠': TriangleAlert, '➕': Plus, '⬇': Download,
  '⏳': LoaderCircle, '⏸': Pause, '👍': ThumbsUp, '👎': ThumbsDown, '🤖': Bot, '🎯': Target,
  '🏁': Flag, '✨': Sparkles,
}
// lucide 图标名 → 组件（供 name="Book" 等直接引用）
const NAME_MAP = {
  Book, BookOpen, FolderOpen, FileText, ClipboardList, Package, Settings, Wrench, Microscope,
  Search, KeyRound, RotateCw, VolumeX, Bug, Construction, Trash2, Rocket, LogOut, Globe,
  MessageSquare, Lightbulb, Save, Upload, Inbox, CheckCircle2, XCircle, TriangleAlert, Plus,
  Download, LoaderCircle, Pause, ThumbsUp, ThumbsDown, Bot, Target, Flag, Sparkles, ListTodo,
  RefreshCw, X, Check, ClipboardCheck, Share2, Pencil, User, Camera, Clock, MapPin, Eye,
  Swords, ScrollText, Tags,
}

const resolved = computed(() => {
  if (!props.name) return null
  const key = String(props.name).replace(/️/g, '')   // 剥 FE0F：🏗️ → 🏗
  return EMOJI_MAP[key] || NAME_MAP[key] || null
})
// 是否 emoji 但未命中映射 → 原样渲染
const isEmoji = computed(() => !resolved.value && /[\u{1F000}-\u{1FAFF}\u{2300}-\u{2BFF}]/u.test(String(props.name)))
</script>

<style scoped>
.app-icon-wrap { display: inline-flex; align-items: center; }
.app-icon-emoji { font-size: inherit; line-height: 1; }
</style>
