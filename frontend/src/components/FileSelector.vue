<template>
  <div class="file-selector">
    <div class="file-drop" @click="$refs.input.click()" @dragover.prevent @drop.prevent="onDrop">
      <span v-if="!files.length">点击选择或拖拽 .txt / .md / .pdf 文件到此</span>
      <span v-else>已选 {{ files.length }} 个文件</span>
      <input ref="input" type="file" multiple accept=".txt,.md,.pdf" class="file-input" @change="onPick" />
    </div>
    <ul v-if="files.length" class="file-list">
      <li v-for="(f, i) in files" :key="i">{{ f.name }}</li>
    </ul>
  </div>
</template>

<script setup>
const props = defineProps({ files: Array })
const emit = defineEmits(['change'])

function onPick(e) {
  emit('change', Array.from(e.target.files))
}

function onDrop(e) {
  // 拖拽过滤：只允许 txt md pdf
  const allowExt = ['.txt', '.md', '.pdf']
  const filtered = Array.from(e.dataTransfer.files).filter(file => {
    const name = file.name.toLowerCase()
    return allowExt.some(ext => name.endsWith(ext))
  })
  emit('change', filtered)
}
</script>

<style scoped>
.file-selector { display: flex; flex-direction: column; gap: 8px; }
.file-drop { border: 2px dashed #ccc; border-radius: 10px; padding: 24px; text-align: center; color: #888; cursor: pointer; transition: all .15s; }
.file-drop:hover { border-color: #1a73e8; color: #1a73e8; background: #f8faff; }
.file-input { display: none; }
.file-list { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.file-list li { background: #f0f0f0; padding: 4px 10px; border-radius: 6px; font-size: 13px; color: #555; }
</style>