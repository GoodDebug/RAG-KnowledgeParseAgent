<template>
  <div class="entity-card-panel">
    <!-- 加载态：切换实体/切章时后台 card 请求未返回 -->
    <div v-if="loading" class="ecp-loading">
      <AppIcon name="LoaderCircle" :size="18" class="ecp-spin" /> 加载中…
    </div>

    <!-- 空态：未选择任何实体 → 引导（图谱内嵌版提示点节点；百科档案版用 emptyDesc 覆盖） -->
    <EmptyState
      v-else-if="!card"
      icon="📇" title="未选择实体"
      :desc="emptyDesc"
    />

    <!-- 有数据：纯展示卡片（子任务 05 五区：L0 身份锚点 / L1 基线 / L2 当前状态 / L3 弧光 / L4 伏笔规则·明暗） -->
    <div v-else class="ecp-body">
      <!-- ===== L0 身份锚点 ===== -->
      <div class="ecp-header">
        <h3 class="ecp-title" :title="card.name">{{ entityName || card.name }}</h3>
        <div class="ecp-badges">
          <span class="ecp-badge" :style="{ background: colorFor(card.type), borderColor: colorFor(card.type) }">{{ card.type }}</span>
          <span v-if="card.L0_identity?.narrative_role" class="ecp-role">{{ card.L0_identity.narrative_role }}</span>
          <span :class="['ecp-conf', confBadgeClass(card.confidence)]" :title="confBadgeTitle(card.confidence)">{{ confBadgeText(card.confidence) }}</span>
        </div>
      </div>
      <!-- L0 元信息行：弧光类型 · 首末出场 · 存活（as-of 当前章） -->
      <div v-if="card.L0_identity" class="ecp-meta">
        <span v-if="card.L0_identity.arc_type" class="ecp-meta-item">{{ card.L0_identity.arc_type }}</span>
        <span class="ecp-meta-item">第 {{ card.L0_identity.first_chapter }}~{{ card.L0_identity.last_chapter }} 章</span>
        <span class="ecp-meta-item">{{ card.L0_identity.is_active ? '存活' : '已下线' }}</span>
      </div>

      <!-- ===== L1 静态基线 ===== -->
      <section v-if="card.L1_baseline" class="ecp-section">
        <h4 class="ecp-sec-title"><AppIcon name="BookOpen" :size="14" /> 基线
          <ThreeStateBadge :state="card.L1_baseline.three_state" />
        </h4>
        <div v-if="card.L1_baseline.origin" class="ecp-field">
          <span class="ecp-label">出身</span><p class="ecp-value">{{ card.L1_baseline.origin }}</p>
        </div>
        <div v-if="hasKeys(card.L1_baseline.core_baseline)" class="ecp-field">
          <span class="ecp-label">欲望·恐惧·执念</span>
          <ul class="ecp-kvlist">
            <li v-for="(v, k) in card.L1_baseline.core_baseline" :key="k" class="ecp-kv">
              <span class="ecp-kv-k">{{ k }}</span><span class="ecp-kv-v">{{ v }}</span>
            </li>
          </ul>
        </div>
        <div v-if="card.L1_baseline.personality" class="ecp-field">
          <span class="ecp-label">性格</span><p class="ecp-value">{{ card.L1_baseline.personality }}</p>
        </div>
        <div v-if="card.L1_baseline.memory_points?.length" class="ecp-field">
          <span class="ecp-label">记忆点</span>
          <div class="ecp-chips">
            <span v-for="m in card.L1_baseline.memory_points" :key="m" class="ecp-chip">{{ m }}</span>
          </div>
        </div>
      </section>

      <!-- ===== L2 当前状态（as-of 当前章） ===== -->
      <section class="ecp-section">
        <h4 class="ecp-sec-title"><AppIcon name="Clock" :size="14" /> 当前状态
          <ThreeStateBadge v-if="card.L2_snapshot" :state="card.L2_snapshot.three_state" />
        </h4>
        <p v-if="card.L2_snapshot?.status_desc" class="ecp-status">
          {{ card.L2_snapshot.status_desc }}
          <span class="ecp-muted">· 第 {{ card.L2_snapshot.chapter_index }} 章</span>
        </p>
        <p v-else class="ecp-muted">无快照</p>

        <!-- attributes 子区（02 固定键结构） -->
        <div v-if="card.L2_snapshot?.attributes" class="ecp-attrwrap">
          <div v-if="card.L2_snapshot.attributes.physique" class="ecp-field">
            <span class="ecp-label">体质</span>
            <p class="ecp-value">{{ joinPhysique(card.L2_snapshot.attributes.physique) }}</p>
          </div>
          <div v-if="card.L2_snapshot.attributes.psychology" class="ecp-field">
            <span class="ecp-label">心理</span>
            <p class="ecp-value">
              表面：{{ card.L2_snapshot.attributes.psychology.surface_emotion || '—' }}
              内心：{{ card.L2_snapshot.attributes.psychology.inner_emotion || '—' }}
              <ThreeStateBadge v-if="card.L2_snapshot.attributes.psychology.inner_emotion" state="inference" />
            </p>
            <p v-if="card.L2_snapshot.attributes.psychology.mental_change" class="ecp-muted">转变：{{ card.L2_snapshot.attributes.psychology.mental_change }}</p>
          </div>
          <div v-if="hasKeys(card.L2_snapshot.attributes.action)" class="ecp-field">
            <span class="ecp-label">行动</span>
            <p class="ecp-value">{{ joinAction(card.L2_snapshot.attributes.action) }}</p>
          </div>
          <div v-if="card.L2_snapshot.attributes.items?.length" class="ecp-field">
            <span class="ecp-label">持有</span>
            <div class="ecp-chips"><span v-for="i in card.L2_snapshot.attributes.items" :key="i" class="ecp-chip">{{ i }}</span></div>
          </div>
          <div v-if="card.L2_snapshot.attributes.skills?.length" class="ecp-field">
            <span class="ecp-label">技能</span>
            <div class="ecp-chips"><span v-for="s in card.L2_snapshot.attributes.skills" :key="s" class="ecp-chip">{{ s }}</span></div>
          </div>
          <div v-if="card.L2_snapshot.attributes.doubts?.length" class="ecp-field">
            <span class="ecp-label">疑点</span>
            <ul class="ecp-list">
              <li v-for="d in card.L2_snapshot.attributes.doubts" :key="d" class="ecp-rel">
                <span class="ecp-rel-name">{{ d }}</span><ThreeStateBadge state="inference" />
              </li>
            </ul>
          </div>
          <div v-if="card.L2_snapshot.attributes.conflicts?.length" class="ecp-field">
            <span class="ecp-label">卷入冲突</span>
            <ul class="ecp-list">
              <li v-for="c in card.L2_snapshot.attributes.conflicts" :key="c" class="ecp-rel">
                <span class="ecp-rel-name">{{ c }}</span><ThreeStateBadge state="inference" />
              </li>
            </ul>
          </div>
        </div>
      </section>

      <!-- 别名（旧分区保留）：chips 列表 -->
      <section v-if="card.aliases?.length" class="ecp-section">
        <h4 class="ecp-sec-title"><AppIcon name="Tags" :size="14" /> 别名</h4>
        <div class="ecp-chips">
          <span v-for="a in card.aliases" :key="a" class="ecp-chip">{{ a }}</span>
        </div>
      </section>

      <!-- 关系（旧分区保留，as-of 当前章）：other_name — relation_type + 起止章节 -->
      <section v-if="card.relations?.length" class="ecp-section">
        <h4 class="ecp-sec-title"><AppIcon name="Share2" :size="14" /> 关系</h4>
        <ul class="ecp-list">
          <li v-for="r in card.relations" :key="r.other_entity_id || r.other_name" class="ecp-rel">
            <span class="ecp-rel-name">{{ r.other_name }}</span>
            <span class="ecp-sep">—</span>
            <span class="ecp-rel-type">{{ r.relation_type }}</span>
            <span class="ecp-muted">第 {{ chapterRange(r.start_chapter, r.end_chapter) }}</span>
          </li>
        </ul>
      </section>

      <!-- ===== L3 聚合弧光 ===== -->
      <section v-if="card.L3_arc" class="ecp-section">
        <h4 class="ecp-sec-title"><AppIcon name="TrendingUp" :size="14" /> 弧光</h4>
        <div v-if="card.L3_arc.snapshots?.length" class="ecp-growth">
          <SnapshotTimeline :snapshots="card.L3_arc.snapshots" :current-chapter="card.L2_snapshot?.chapter_index" :growth-markers="growthMarkers" />
        </div>
        <div v-if="card.L3_arc.events?.length" class="ecp-field">
          <span class="ecp-label">事件履历</span>
          <ul class="ecp-list">
            <li v-for="e in card.L3_arc.events" :key="e.event_id" class="ecp-rel">
              <span class="ecp-event-title">{{ e.event_title }}</span>
              <span class="ecp-muted">第 {{ chapterRange(e.start_chapter, e.end_chapter) }}</span>
            </li>
          </ul>
        </div>
        <div v-if="card.L3_arc.relation_evolution?.length" class="ecp-field">
          <span class="ecp-label">关系演变</span>
          <ul class="ecp-list">
            <li v-for="(r, i) in card.L3_arc.relation_evolution" :key="i" class="ecp-rel">
              <span class="ecp-rel-name">{{ r.other_name }}</span><span class="ecp-sep">—</span>
              <span class="ecp-rel-type">{{ r.relation_type }}</span>
              <span v-if="r.relation_trend && r.relation_trend !== '稳定'" class="ecp-trend">{{ r.relation_trend }}</span>
              <span class="ecp-muted">第 {{ chapterRange(r.start_chapter, r.end_chapter) }}</span>
            </li>
          </ul>
        </div>
        <div v-if="card.L3_arc.foreshadowing_line?.length" class="ecp-field">
          <span class="ecp-label">伏笔埋收线</span>
          <ul class="ecp-list">
            <li v-for="f in card.L3_arc.foreshadowing_line" :key="f.title" class="ecp-rel">
              <span class="ecp-event-title">{{ f.title }}</span>
              <span class="ecp-muted">{{ fsStatus(f) }}</span>
            </li>
          </ul>
        </div>
      </section>

      <!-- ===== L4 伏笔规则·明暗 ===== -->
      <section v-if="card.L4_narrative" class="ecp-section">
        <h4 class="ecp-sec-title"><AppIcon name="Eye" :size="14" /> 伏笔·规则·明暗</h4>
        <div v-if="card.L4_narrative.unresolved_secrets?.length" class="ecp-field">
          <span class="ecp-label">未回收秘密</span>
          <ul class="ecp-list">
            <li v-for="s in card.L4_narrative.unresolved_secrets" :key="s.title" class="ecp-rel">
              <span class="ecp-event-title">{{ s.title }}</span>
              <ThreeStateBadge state="inference" />
            </li>
          </ul>
        </div>
        <div v-if="card.L4_narrative.rules?.length" class="ecp-field">
          <span class="ecp-label">规则约束</span>
          <ul class="ecp-list">
            <li v-for="ru in card.L4_narrative.rules" :key="ru.rule_name" class="ecp-rel">
              <span class="ecp-event-title">{{ ru.rule_name }}</span>
              <span class="ecp-sep">—</span>
              <span class="ecp-rel-type">{{ ru.rule_type }}<template v-if="ru.subject_ability">·{{ ru.subject_ability }}</template></span>
              <span class="ecp-muted" :title="ru.rule_content">{{ truncate(ru.rule_content, 30) }}</span>
            </li>
          </ul>
        </div>
        <div v-if="card.L4_narrative.conflicts?.length" class="ecp-field">
          <span class="ecp-label">卷入冲突</span>
          <ul class="ecp-list">
            <li v-for="c in card.L4_narrative.conflicts" :key="c.conflict_title" class="ecp-rel">
              <span class="ecp-event-title">{{ c.conflict_title }}</span>
              <span class="ecp-sep">—</span>
              <span class="ecp-rel-type">{{ c.current_status }}</span>
            </li>
          </ul>
        </div>
        <div v-if="card.L4_narrative.surface_inner_relations?.length" class="ecp-field">
          <span class="ecp-label">明暗关系</span>
          <ul class="ecp-list">
            <li v-for="(r, i) in card.L4_narrative.surface_inner_relations" :key="i" class="ecp-rel">
              <span class="ecp-rel-name">{{ r.other_name }}</span><span class="ecp-sep">—</span>
              <span class="ecp-rel-type">{{ r.relation_type }}</span>
              <span class="ecp-muted" :title="r.surface_relation">表：{{ r.surface_relation || '—' }}</span>
              <span class="ecp-inner" :title="r.inner_relation">里：{{ r.inner_relation || '—' }}</span>
              <ThreeStateBadge v-if="r.inner_relation" state="inference" />
              <span v-if="r.relation_trend && r.relation_trend !== '稳定'" class="ecp-trend">{{ r.relation_trend }}</span>
            </li>
          </ul>
        </div>
        <div v-if="card.L4_narrative.narrative_types?.length" class="ecp-field">
          <span class="ecp-label">叙事类型</span>
          <ul class="ecp-list">
            <li v-for="n in card.L4_narrative.narrative_types" :key="n.event_title" class="ecp-rel">
              <span class="ecp-event-title">{{ n.event_title }}</span>
              <span v-if="n.narrative_type" class="ecp-trend">{{ n.narrative_type }}</span>
              <ThreeStateBadge state="inference" />
            </li>
          </ul>
        </div>
      </section>

      <!-- 参与事件（旧分区保留）：事件标题 + 起止章节 -->
      <section v-if="card.events?.length" class="ecp-section">
        <h4 class="ecp-sec-title"><AppIcon name="ScrollText" :size="14" /> 参与事件</h4>
        <ul class="ecp-list">
          <li v-for="e in card.events" :key="e.event_id" class="ecp-event">
            <span class="ecp-event-title">{{ e.event_title }}</span>
            <span class="ecp-muted">第 {{ chapterRange(e.start_chapter, e.end_chapter) }}</span>
          </li>
        </ul>
      </section>

      <!-- 证据摘要（旧分区保留）：章节标题 + 章节号 -->
      <section v-if="card.evidence" class="ecp-section">
        <h4 class="ecp-sec-title"><AppIcon name="Eye" :size="14" /> 证据摘要</h4>
        <p class="ecp-evidence">
          {{ card.evidence.chapter_title }}
          <span class="ecp-muted">· 第 {{ card.evidence.chapter_index }} 章</span>
        </p>
      </section>

      <!-- 查看原文：无 payload，上抛 view-source 由宿主定位原文 -->
      <button class="btn btn-primary ecp-source" @click="emit('view-source')">
        <AppIcon name="FileText" :size="14" /> 查看原文
      </button>
    </div>
  </div>
</template>

<script setup>
// 实体卡片面板（子任务 05 展示层）：纯展示哑组件，不 import api、不发请求。
// card 数据由 GraphView/EntityView 作为唯一数据编排者注入（backend getEntityCard 响应），
// 本组件按 L0-L4 五区渲染（身份锚点 / 基线 / 当前状态 / 弧光 / 伏笔规则·明暗）+ 三态徽标 + 置信度弱视觉；
// 旧分区（别名/关系/参与事件/证据摘要）保留作向后兼容；card 缺 L0-L4 键时 v-if 跳过不报错。
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import EmptyState from './EmptyState.vue'
import SnapshotTimeline from './SnapshotTimeline.vue'
import ThreeStateBadge from './ThreeStateBadge.vue'

const props = defineProps({
  card: { type: Object, default: null },       // backend getEntityCard 响应（null = 空态）
  loading: { type: Boolean, default: false },  // 是否加载中（切换实体/切章时置 true）
  entityName: { type: String, default: '' },   // 当前选中实体名（头部展示）
  emptyDesc: { type: String, default: '点击图谱节点查看实体卡。' },  // 空态引导
})
const emit = defineEmits(['view-source'])

// L3 成长线：基于"该章有 升级/转折/打脸/揭秘 事件"标记突破/转折节点（子任务 05）
const GROWTH_TYPES = ['升级', '转折', '打脸', '揭秘']
const growthMarkers = computed(() => {
  const arc = props.card?.L3_arc
  if (!arc) return {}
  const m = {}
  for (const e of arc.events || []) {
    const ch = Number(e.start_chapter)
    const nt = e.narrative_type
    if (ch && GROWTH_TYPES.includes(nt) && !m[ch]) m[ch] = nt === '升级' ? '突破' : '转折'
  }
  return m
})

// ---- 渲染 helper（纯函数，避免模板逻辑） ----
function colorFor(type) {
  const map = {
    human: '#1a73e8', faction: '#e67e22', item: '#188038', skill: '#b06000',
    spirit: '#7b1fa2', task: '#d93025', rule: '#888',
  }
  return map[type] || '#999'
}
function chapterRange(start, end) {
  if (start == null && end == null) return ''
  const s = start ?? end
  const e = end ?? start
  return s === e ? `${s} 章` : `${s}~${e} 章`
}
function parseConfidence(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
function confBadgeClass(v) {
  const c = parseConfidence(v)
  if (c === null) return 'cf-badge cf-null'
  return c < 0.6 ? 'cf-badge cf-low' : 'cf-badge cf-ok'
}
function confBadgeText(v) {
  const c = parseConfidence(v)
  return c === null ? '待复核' : c.toFixed(2)
}
function confBadgeTitle(v) {
  const c = parseConfidence(v)
  return c === null ? '置信度：未复核' : `置信度：${c.toFixed(2)}`
}
// attributes 子区拼接
function joinPhysique(p) {
  return [p.health_status, p.power_level, p.body_special].filter(Boolean).join(' · ')
}
function joinAction(a) {
  return [a.key_behavior, a.key_line && `「${a.key_line}」`, a.gain_loss && `得失：${a.gain_loss}`].filter(Boolean).join(' · ')
}
// 对象有非空键（core_baseline/action 空对象不渲染）
function hasKeys(o) {
  return !!o && typeof o === 'object' && Object.values(o).some((v) => v !== '' && v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0))
}
// 伏笔状态文案
function fsStatus(f) {
  if (f.status === 'revealed') return `已回收·第 ${f.reveal_chapter} 章`
  if (f.status === 'abandoned') return '已废弃'
  return `未回收·埋设第 ${f.setup_chapter} 章`
}
function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? `${s.slice(0, n)}…` : s
}
</script>

<style scoped>
.entity-card-panel { background: #fff; border-radius: 12px; border: 1px solid #e8e8e8; padding: 16px; overflow-y: auto; }
.ecp-loading { display: flex; align-items: center; justify-content: center; gap: 8px; color: #999; font-size: 14px; padding: 24px 0; }
.ecp-spin { animation: ecp-spin 1s linear infinite; }
@keyframes ecp-spin { to { transform: rotate(360deg); } }
.ecp-body { display: flex; flex-direction: column; gap: 16px; }
.ecp-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.ecp-title { font-size: 16px; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ecp-badge { flex-shrink: 0; color: #fff; font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.ecp-badges { display: flex; align-items: center; gap: 6px; flex-shrink: 0; min-width: 0; }
.ecp-role { flex-shrink: 0; font-size: 12px; color: #1a73e8; background: #e8f0fe; padding: 2px 10px; border-radius: 999px; }
.ecp-conf { white-space: nowrap; }
.ecp-meta { display: flex; flex-wrap: wrap; gap: 6px; }
.ecp-meta-item { font-size: 12px; color: #777; background: #f5f5f5; padding: 2px 8px; border-radius: 6px; }
.ecp-section { display: flex; flex-direction: column; gap: 8px; }
.ecp-sec-title { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #555; margin: 0; font-weight: 600; }
.ecp-field { display: flex; flex-direction: column; gap: 2px; }
.ecp-label { font-size: 12px; color: #999; }
.ecp-value { margin: 0; font-size: 13px; color: #333; }
.ecp-kvlist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.ecp-kv { display: flex; gap: 6px; font-size: 13px; }
.ecp-kv-k { color: #888; flex-shrink: 0; }
.ecp-kv-v { color: #333; }
.ecp-attrwrap { display: flex; flex-direction: column; gap: 6px; }
.ecp-muted { color: #999; font-size: 13px; }
.ecp-status { margin: 0; font-size: 13px; color: #333; }
.ecp-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.ecp-chip { background: #f5f5f5; border: 1px solid #d0d0d0; color: #555; font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.ecp-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.ecp-rel, .ecp-event { display: flex; align-items: center; gap: 6px; font-size: 13px; flex-wrap: wrap; }
.ecp-rel-name { font-weight: 500; color: #1a73e8; }
.ecp-sep { color: #999; }
.ecp-rel-type { color: #333; }
.ecp-event-title { color: #333; }
.ecp-inner { color: #b06000; font-size: 12px; }
.ecp-trend { color: #7b1fa2; background: #f3ecfa; font-size: 11px; padding: 1px 6px; border-radius: 999px; }
.ecp-growth { border: 1px solid #f0eef5; border-radius: 8px; padding: 8px; background: #fafafc; }
.ecp-evidence { margin: 0; font-size: 13px; color: #333; }
/* 三态/置信度弱视觉（cf-* 对齐既有） */
.cf-badge { display: inline-block; padding: 1px 8px; border-radius: 8px; font-size: 12px; background: #f5f5f5; color: #999; }
.cf-null { background: #f5f5f5; color: #999; }
.cf-low { color: #b06000; background: #fef7e0; }
.cf-ok { color: #1a73e8; background: #e8f0fe; }
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 8px 16px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 14px; }
.btn-primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn-primary:hover { background: #1765cc; }
.ecp-source { align-self: flex-start; }
</style>
