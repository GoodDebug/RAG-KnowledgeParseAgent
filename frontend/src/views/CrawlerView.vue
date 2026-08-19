<template>
  <div class="crawler-view">
    <!-- 模式切换 -->
    <div class="tabs">
      <button :class="{ active: tab === 'general' }" @click="tab = 'general'"><AppIcon name="Globe" :size="14" /> 通用爬虫</button>
      <button :class="{ active: tab === 'novel' }" @click="tab = 'novel'"><AppIcon name="Book" :size="14" /> 小说爬取</button>
    </div>

    <!-- ========== 通用爬虫 ========== -->
    <template v-if="tab === 'general'">
      <div class="crawler-card">
        <h2><AppIcon name="Globe" :size="18" /> 网页文章爬取</h2>
        <p class="desc">输入 URL 抓取文章内容，可预览后下载。</p>
        <div class="form-group">
          <label>爬取模式</label>
          <div class="mode-select">
            <label><input v-model="mode" type="radio" value="dynamic" /> 动态渲染</label>
            <label><input v-model="mode" type="radio" value="intercept" /> API 拦截</label>
          </div>
        </div>
        <div class="form-group">
          <label>URL</label>
          <input v-model="url" class="form-input" placeholder="输入文章 URL" @keydown.enter.prevent="startCrawl" />
        </div>
        <button class="btn-primary" :disabled="!url.trim() || crawling" @click="startCrawl">
          <AppIcon :name="crawling ? 'LoaderCircle' : 'Rocket'" :size="16" /> {{ crawling ? '爬取中...' : '开始爬取' }}
        </button>
        <p v-if="error" class="error">{{ error }}</p>
      </div>
      <div v-if="result" class="crawler-card result-card">
        <h3><AppIcon name="FileText" :size="16" /> {{ result.title }}</h3>
        <div class="meta">{{ result.filename }} · {{ result.content_length }} 字</div>
        <pre class="preview">{{ result.content }}</pre>
        <button class="btn-download" @click="downloadResult"><AppIcon name="Download" :size="15" /> 下载</button>
      </div>
    </template>

    <!-- ========== 小说爬取 ========== -->
    <template v-if="tab === 'novel'">
      <div class="crawler-card">
        <h2><AppIcon name="Book" :size="18" /> 小说章节爬取</h2>
        <p class="desc">输入小说主页 URL，自动发现所有章节并批量下载。</p>
        <div class="form-group">
          <label>小说主页 URL</label>
          <input v-model="novelUrl" class="form-input" placeholder="如 https://www.biquge2345.com/xiaoshuo/8136/" />
        </div>
        <button class="btn-primary" :disabled="!novelUrl.trim() || novelLoading" @click="discoverChapters">
          <AppIcon :name="novelLoading ? 'LoaderCircle' : 'ClipboardList'" :size="16" /> {{ novelLoading ? '解析中...' : '发现章节' }}
        </button>
        <p v-if="novelError" class="error">{{ novelError }}</p>
      </div>

      <div v-if="chapters.length" class="crawler-card">
        <div class="ch-header">
          <h3><AppIcon name="ClipboardList" :size="16" /> 共 {{ chapters.length }} 章</h3>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <label><input v-model="selectAll" type="checkbox" @change="toggleAll" /> 全选</label>
            <button class="btn-primary btn-sm" :disabled="!selectedChapters.length || novelCrawling" @click="crawlSelected">
              <AppIcon :name="novelCrawling ? 'LoaderCircle' : 'Download'" :size="14" /> {{ novelCrawling ? novelProgress : '下载选中' }}
            </button>
            <button class="btn-dir" :disabled="!selectedChapters.length || novelCrawling" @click="saveToDir">
              <AppIcon name="Save" :size="14" /> {{ dirName ? `保存到 ${dirName}` : '保存到目录' }}
            </button>
          </div>
        </div>
        <div class="ch-list">
          <div v-for="(ch, i) in chapters" :key="i" class="ch-item">
            <label><input v-model="ch.selected" type="checkbox" /> <span :class="ch.type === 'extra' ? 'tag-extra' : ''">{{ ch.title }}</span></label>
          </div>
        </div>
      </div>

      <div v-if="novelResults.length" class="crawler-card">
        <h3><AppIcon name="CheckCircle2" :size="16" /> {{ novelCrawling ? `处理中 ${novelProgress}` : `下载完成（${novelResults.filter(r => r.success).length}/${novelResults.length}）` }}</h3>
        <div v-for="r in novelResults" :key="r.title" class="batch-item" :class="{ fail: !r.success }">
          <span><AppIcon :name="r.success ? 'CheckCircle2' : 'XCircle'" :size="14" /> {{ r.title }}</span>
          <button v-if="r.success" class="btn-small" @click="downloadBlob(r.content, r.filename)"><AppIcon name="Download" :size="13" /></button>
          <span v-if="r.error" class="error">{{ r.error }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { crawlSingle, novelChapters, novelCrawl } from '../api'
import AppIcon from '../components/AppIcon.vue'

const tab = ref('general')

// 通用爬虫
const url = ref('')
const mode = ref('dynamic')
const result = ref(null)
const crawling = ref(false)
const error = ref('')

// 小说爬取
const novelUrl = ref('')
const chapters = ref([])
const selectAll = ref(false)
const novelLoading = ref(false)
const novelCrawling = ref(false)
const novelProgress = ref('')
const novelError = ref('')
const novelResults = ref([])
const dirHandle = ref(null)
const dirName = ref('')

const selectedChapters = computed(() => chapters.value.filter(c => c.selected))

function downloadBlob(content, filename) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename || 'article.txt'
  a.click()
}

function downloadResult() { if (result.value) downloadBlob(result.value.content, result.value.filename) }

async function startCrawl() {
  error.value = ''; result.value = null; crawling.value = true
  try {
    result.value = await crawlSingle(url.value.trim(), mode.value)
    if (!result.value.success) { error.value = result.value.error; result.value = null }
  } catch (e) { error.value = '爬取失败：' + e.message }
  finally { crawling.value = false }
}

async function discoverChapters() {
  novelError.value = ''; chapters.value = []; novelResults.value = []; novelLoading.value = true
  try {
    const res = await novelChapters(novelUrl.value.trim())
    if (!res.success) { novelError.value = res.error; return }
    chapters.value = (res.chapters || []).map(c => ({ ...c, selected: c.type === "chapter" }))
  } catch (e) { novelError.value = '解析失败：' + e.message }
  finally { novelLoading.value = false }
}

function toggleAll() { chapters.value.forEach(c => c.selected = selectAll.value) }

async function crawlSelected() {
  novelResults.value = []; novelCrawling.value = true
  const selected = selectedChapters.value
  const batchSize = 20

  for (let i = 0; i < selected.length; i += batchSize) {
    const batch = selected.slice(i, i + batchSize)
    novelProgress.value = `${Math.min(i + batchSize, selected.length)}/${selected.length}`
    try {
      const res = await novelCrawl(batch, novelUrl.value)
      novelResults.value.push(...(res.results || []))
    } catch { /* 跳过失败批次 */ }
  }
  novelCrawling.value = false
  novelProgress.value = ''
}

async function selectDir() {
  try {
    dirHandle.value = await window.showDirectoryPicker({ mode: 'readwrite' })
    dirName.value = dirHandle.value.name
  } catch { /* 用户取消选择 */ }
}

async function saveToDir() {
  if (!dirHandle.value) {
    try {
      dirHandle.value = await window.showDirectoryPicker({ mode: 'readwrite' })
      dirName.value = dirHandle.value.name
    } catch { return /* 用户取消 */ }
  }
  novelResults.value = []; novelCrawling.value = true
  const selected = selectedChapters.value
  const batchSize = 20

  for (let i = 0; i < selected.length; i += batchSize) {
    const batch = selected.slice(i, i + batchSize)
    novelProgress.value = `爬取中 ${Math.min(i + batchSize, selected.length)}/${selected.length}`
    try {
      const res = await novelCrawl(batch, novelUrl.value)
      const results = res.results || []
      // 写入目录
      for (const r of results) {
        if (r.success) {
          try {
            const fh = await dirHandle.value.getFileHandle(r.filename, { create: true })
            const w = await fh.createWritable()
            await w.write(r.content)
            await w.close()
          } catch { /* 单章写入失败 */ }
        }
      }
      novelResults.value.push(...results)
      novelProgress.value = `已保存 ${Math.min(i + batchSize, selected.length)}/${selected.length}`
    } catch { /* 跳过失败批次 */ }
  }
  novelCrawling.value = false
  novelProgress.value = ''
}
</script>

<style scoped>
.crawler-view { display: flex; flex-direction: column; gap: 16px; padding: 16px; max-width: 800px; margin: 0 auto; width: 100%; overflow-y: auto; }
.tabs { display: flex; gap: 0; background: #fff; border-radius: 12px 12px 0 0; border: 1px solid #e0e0e0; border-bottom: none; overflow: hidden; }
.tabs button { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 10px 16px; border: none; background: #f8f8f8; cursor: pointer; font-size: 14px; color: #666; transition: all .15s; }
.tabs button.active { background: #fff; color: #1a73e8; font-weight: 600; }
.crawler-card { background: #fff; border-radius: 0 0 12px 12px; padding: 20px; border: 1px solid #e8e8e8; }
.crawler-card h2 { margin-bottom: 4px; }
.desc { color: #888; font-size: 13px; margin-bottom: 16px; }
.form-group { margin-bottom: 12px; }
.form-group label { display: block; font-size: 13px; color: #555; margin-bottom: 4px; font-weight: 600; }
.form-input { width: 100%; padding: 10px 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 14px; outline: none; }
.form-input:focus { border-color: #1a73e8; }
.mode-select { display: flex; gap: 16px; }
.mode-select label { font-weight: 400; font-size: 14px; cursor: pointer; }
.btn-primary { width: 100%; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px; background: #1a73e8; color: #fff; border: none; border-radius: 8px; font-size: 15px; cursor: pointer; margin-top: 4px; }
.btn-primary:disabled { opacity: .4; cursor: not-allowed; }
.btn-primary:hover:not(:disabled) { opacity: .85; }
.btn-sm { width: auto; padding: 6px 16px; font-size: 13px; margin: 0; }
.error { color: #d93025; font-size: 13px; margin-top: 8px; }
.result-card { border-left: 4px solid #34a853; }
.meta { color: #888; font-size: 12px; margin: 4px 0 12px; }
.preview { background: #f8f8f8; padding: 12px; border-radius: 8px; font-size: 13px; line-height: 1.6; max-height: 400px; overflow-y: auto; white-space: pre-wrap; }
.btn-download { width: 100%; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 8px; margin-top: 12px; background: #34a853; color: #fff; border: none; border-radius: 8px; font-size: 14px; cursor: pointer; }
.btn-download:hover, .btn-small:hover { opacity: .85; }
.ch-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px; }
.ch-header h3 { margin: 0; }
.ch-header label { font-size: 13px; cursor: pointer; margin-right: 12px; }
.ch-list { max-height: 400px; overflow-y: auto; border: 1px solid #eee; border-radius: 8px; padding: 4px; }
.ch-item { padding: 4px 8px; font-size: 13px; }
.ch-item label { cursor: pointer; display: block; }
.ch-item:hover { background: #f5f5f5; }
.batch-item { padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: 13px; display: flex; gap: 8px; align-items: center; }
.batch-item.fail { color: #d93025; }
.btn-small { display: inline-flex; align-items: center; padding: 2px 8px; background: #34a853; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; margin-left: auto; }
.btn-dir { display: inline-flex; align-items: center; gap: 5px; padding: 6px 14px; background: #e8f0fe; color: #1a73e8; border: 1px solid #1a73e8; border-radius: 6px; font-size: 13px; cursor: pointer; white-space: nowrap; }
.btn-dir:disabled { opacity: .4; cursor: not-allowed; }
.btn-dir:hover:not(:disabled) { background: #d2e3fc; }
.tag-extra { color: #e67e22; font-size: 12px; margin-left: 4px; padding: 1px 6px; background: #fef3e2; border-radius: 4px; }
</style>
