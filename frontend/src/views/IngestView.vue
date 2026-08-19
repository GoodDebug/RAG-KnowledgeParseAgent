<template>
  <div class="ingest-view">
    <div class="ingest-card">
      <h2><AppIcon name="FolderOpen" :size="18" /> 文档入库</h2>
      <p class="desc">上传 .txt / .md / .pdf 文档，系统自动解析并向量化入库（Spec-C /api/documents）。</p>

      <div class="form-group">
        <label>书籍名称</label>
        <input v-model="bookName" class="form-input" placeholder="如：斗破苍穹 / 售后政策" />
      </div>

      <div class="form-group">
        <label>选择文件</label>
        <FileSelector :files="selectedFiles" @change="onFilesChange" />
      </div>

      <!-- 解构开关：勾选后上传带 deconstruct=1，同一后台任务并行跑 Milvus 入库 + LangGraph 解构 -->
      <div class="form-group toggle-row">
        <label class="toggle-label">
          <input type="checkbox" v-model="deconstructEnabled" :disabled="ingesting" />
          <AppIcon name="Microscope" :size="16" /> 导入后自动解构（小说章节抽取）
        </label>
        <p class="toggle-hint">适用于含章节结构的文本（小说/剧本）；勾选后上传将同时写入 novel_chapter 并启动解构流水线。</p>
      </div>

      <button class="btn-primary" :disabled="!canIngest" @click="startIngest">
        <AppIcon :name="ingesting ? 'LoaderCircle' : 'Upload'" :size="16" /> {{ ingesting ? '入库中...' : '开始入库' }}
      </button>

      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="success" class="success">{{ success }}</p>
    </div>

    <!-- 两列布局：左=book_name 瀑布流（套餐菜单）；右=选中 book 的 document 瀑布流（菜品） -->
    <div class="kb-layout">
      <div class="kb-col">
        <h3><AppIcon name="Package" :size="16" /> 书籍（{{ books.length }}）</h3>
        <div v-if="!books.length" class="empty">暂无书籍</div>
        <div
          v-for="b in books"
          :key="b.book_name"
          :class="['book-item', { active: b.book_name === selectedBook }]"
          @click="selectBook(b.book_name)"
        >
          <div class="book-info">
            <span class="book-name">{{ b.book_name }}</span>
            <span class="book-meta">{{ b.doc_count }} 文件 · {{ b.chunk_total }} Chunk</span>
          </div>
          <button class="del-btn" title="删除整本书（含所有文档与向量）" @click.stop="removeBook(b)"><AppIcon name="Trash2" :size="14" /></button>
        </div>
      </div>

      <div class="kb-col">
        <h3><AppIcon name="FileText" :size="16" /> 文档{{ selectedBook ? `：${selectedBook}` : '' }}</h3>
        <div v-if="!selectedBook" class="empty">点击左侧选择书籍</div>
        <div v-else-if="!docs.length" class="empty">该书籍暂无文档</div>
        <div v-for="d in docs" :key="d.id" class="book-item">
          <div class="book-info">
            <span class="book-name">{{ d.file_name }}</span>
            <span class="book-meta">{{ d.uploaded_at || '—' }} · {{ d.chunk_count ?? '—' }} Chunk</span>
          </div>
          <div class="book-actions">
            <span :class="['status-badge', 'status-' + d.status]">{{ STATUS_TEXT[d.status] || d.status }}</span>
            <button class="del-btn" title="删除该文件（仅当前 document）" @click="removeDoc(d)"><AppIcon name="Trash2" :size="14" /></button>
          </div>
        </div>
      </div>
    </div>

    <!-- 解构进度面板：只展示「当前上传的书」的解构数据（内嵌 SSE 实时进度） -->
    <DeconstructPanel :book="uploadedBook" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import FileSelector from '../components/FileSelector.vue'
import DeconstructPanel from '../components/DeconstructPanel.vue'
import AppIcon from '../components/AppIcon.vue'
import { uploadDocuments, listDocuments, listBookNames, deleteDocument, deleteBook } from '../api'

const router = useRouter()
const STATUS_TEXT = { processing: '处理中', ready: '就绪', failed: '失败' }

const bookName = ref('')
const selectedFiles = ref([])
const books = ref([])
const selectedBook = ref('')
const docs = ref([])
const ingesting = ref(false)
const error = ref('')
const success = ref('')
// 解构开关（默认关，与后端 NOVEL_DECONSTRUCT_ON_UPLOAD=0 一致）+ 本次上传的书（驱动解构面板）
const deconstructEnabled = ref(false)
const uploadedBook = ref(null)
let pollTimer = null

const canIngest = computed(() => bookName.value.trim() && selectedFiles.value.length && !ingesting.value)

function onFilesChange(files) { selectedFiles.value = files }

const BATCH_SIZE = 50

async function startIngest() {
  error.value = ''; success.value = ''
  ingesting.value = true

  const book = bookName.value.trim()
  const files = selectedFiles.value
  const deconstructOn = deconstructEnabled.value
  console.log(`📤 入库启动: book_name=${book}, 文件总数=${files.length}, 每批${BATCH_SIZE}个, deconstruct=${deconstructOn}`)
  for (let i = 0; i < files.length; i += BATCH_SIZE) {
    const batch = files.slice(i, i + BATCH_SIZE)
    try {
      const res = await uploadDocuments(book, batch, deconstructOn)
      console.log(`  第${Math.floor(i / BATCH_SIZE) + 1}批已提交:`, res)
      // 首批成功后记录本次上传的书 → 驱动解构面板（重复上传覆盖为最新）
      if (i === 0) uploadedBook.value = { book_id: res.book_id, book_name: book, deconstruct_on: deconstructOn }
    } catch (e) {
      console.error(`❌ 第${Math.floor(i / BATCH_SIZE) + 1}批提交失败:`, e)
      error.value = '第 ' + (i + 1) + ' 批上传失败：' + (e.message || '')
      ingesting.value = false
      return
    }
  }
  success.value = '已提交入库（异步入库中，请等待状态变为就绪）'
  await loadBooks()
  selectBook(book)
  pollUntilIdle(book)
}

function pollUntilIdle(book) {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      await loadBooks()
      await loadDocs()
      const pending = docs.value.some(d => d.book_name === book && d.status === 'processing')
      if (!pending) {
        clearInterval(pollTimer); pollTimer = null
        ingesting.value = false
        success.value = '入库完成'
      }
    } catch { /* 轮询期间失败则继续等待 */ }
  }, 2000)
}

async function loadBooks() {
  try {
    books.value = await listBookNames() || []
    // 若当前选中书已不存在，回退第一本或清空
    if (selectedBook.value && !books.value.some(b => b.book_name === selectedBook.value)) {
      selectedBook.value = books.value.length ? books.value[0].book_name : ''
    } else if (!selectedBook.value && books.value.length) {
      selectedBook.value = books.value[0].book_name
    }
  } catch { books.value = [] }
}

function selectBook(name) {
  if (selectedBook.value === name) return
  selectedBook.value = name
  loadDocs()
}

async function loadDocs() {
  if (!selectedBook.value) { docs.value = []; return }
  try { docs.value = await listDocuments(selectedBook.value) || [] } catch { docs.value = [] }
}

async function removeDoc(d) {
  if (!window.confirm(`确认删除文件「${d.file_name}」？将仅删除该文件及其向量。`)) return
  try {
    await deleteDocument(d.id)
    success.value = '已删除该文件'
    await loadDocs()
    await loadBooks()
  } catch (e) {
    error.value = '删除失败：' + (e.message || '')
  }
}

async function removeBook(b) {
  if (!window.confirm(`确认删除整本书「${b.book_name}」？将删除该书全部文档与向量。`)) return
  try {
    await deleteBook(b.book_name)
    success.value = '已删除整本书'
    if (selectedBook.value === b.book_name) selectedBook.value = ''
    await loadBooks()
    await loadDocs()
  } catch (e) {
    error.value = '删除失败：' + (e.message || '')
  }
}

onMounted(async () => {
  if (!localStorage.getItem('token')) { router.push('/login'); return }
  await loadBooks()   // ① 先触发「📦 书籍」列表（左列）
  await loadDocs()    // ② 依据当前选中（默认第一本）触发「📄 文档」列表（右列）
})

onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.ingest-view { display: flex; flex-direction: column; gap: 16px; padding: 16px; max-width: 880px; margin: 0 auto; width: 100%; overflow-y: auto; }
.ingest-card { background: #fff; border-radius: 12px; padding: 20px; border: 1px solid #e8e8e8; }
.ingest-card h2 { margin-bottom: 4px; }
.ingest-card h3 { margin-bottom: 12px; }
.desc { color: #888; font-size: 13px; margin-bottom: 16px; }
.form-group { margin-bottom: 12px; }
.form-group label { display: block; font-size: 13px; color: #555; margin-bottom: 4px; font-weight: 600; }
/* 解构开关 */
.toggle-row { padding: 10px 12px; background: #f8f9fb; border: 1px solid #e8e8e8; border-radius: 8px; }
.toggle-label { display: flex !important; align-items: center; gap: 8px; font-size: 14px !important; color: #333 !important; font-weight: 600; margin-bottom: 4px; cursor: pointer; }
.toggle-label input { width: 16px; height: 16px; accent-color: #1a73e8; }
.toggle-hint { font-size: 12px; color: #888; margin: 0; }
.form-input { width: 100%; padding: 10px 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 14px; outline: none; }
.form-input:focus { border-color: #1a73e8; }
.btn-primary { width: 100%; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px; background: #1a73e8; color: #fff; border: none; border-radius: 8px; font-size: 15px; cursor: pointer; margin-top: 4px; }
.btn-primary:disabled { opacity: .4; cursor: not-allowed; }
.btn-primary:hover:not(:disabled) { opacity: .85; }
.error { color: #d93025; font-size: 13px; margin-top: 8px; }
.success { color: #188038; font-size: 13px; margin-top: 8px; }
/* 两列布局 */
.kb-layout { display: flex; gap: 16px; }
.kb-col { flex: 1; background: #fff; border-radius: 12px; padding: 16px; border: 1px solid #e8e8e8; min-width: 0; }
.kb-col h3 { margin: 0 0 12px; font-size: 15px; }
.empty { color: #aaa; font-size: 14px; padding: 12px 0; }
.book-item { display: flex; justify-content: space-between; align-items: center; padding: 8px; border-radius: 8px; cursor: pointer; transition: background .15s; }
.book-item:hover { background: #f5f5f5; }
.book-item.active { background: #e8f0fe; }
.book-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.book-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.book-meta { color: #888; font-size: 12px; }
.book-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.status-badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.status-processing { background: #fff3cd; color: #b26a00; }
.status-ready { background: #e6f4ea; color: #188038; }
.status-failed { background: #fce8e6; color: #d93025; }
.del-btn { display: inline-flex; align-items: center; border: 1px solid #ddd; background: #fff; border-radius: 6px; padding: 3px 8px; font-size: 12px; cursor: pointer; color: #666; flex-shrink: 0; }
.del-btn:hover { border-color: #d93025; color: #d93025; }
</style>
