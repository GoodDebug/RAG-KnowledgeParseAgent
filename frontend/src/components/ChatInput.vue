<template>
  <div class="chat-input-wrap">
    <textarea
      ref="textarea"
      v-model="text"
      class="chat-textarea"
      placeholder="输入消息…"
      rows="1"
      @keydown.enter.exact.prevent="submit"
      @input="autoResize"
    />
    <button
      class="rag-toggle"
      :class="{ on: useRag }"
      :title="useRag ? '已开启知识库检索' : '已关闭知识库，用模型自身知识回答'"
      @click="useRag = !useRag"
    ><AppIcon name="Search" :size="14" /> 知识库{{ useRag ? '开' : '关' }}</button>
    <button class="send-btn" :disabled="!text.trim()" @click="submit">发送</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
const emit = defineEmits(['send'])
const text = ref('')
const useRag = ref(true)  // 知识库开关（默认开）
const textarea = ref(null)

function autoResize() {
  if (textarea.value) {
    textarea.value.style.height = 'auto'
    textarea.value.style.height = Math.min(textarea.value.scrollHeight, 200) + 'px'
  }
}
function submit() {
  if (!text.value.trim()) {
    console.log('[ChatInput] ⏸️ 提交跳过：内容为空')
    return
  }
  console.log('[ChatInput] 📤 提交消息 | text=%.80s | useRag=%s', text.value, useRag.value)
  emit('send', text.value, useRag.value)
  text.value = ''
  if (textarea.value) { textarea.value.style.height = 'auto' }
}
</script>

<style scoped>
.chat-input-wrap { display: flex; gap: 8px; padding: 12px 16px; background: #fff; border-top: 1px solid #e0e0e0; align-items: flex-end; }
.chat-textarea {
  flex: 1; padding: 10px 14px; border: 1px solid #d0d0d0; border-radius: 10px;
  font-size: 14px; line-height: 1.5; resize: none; outline: none; font-family: inherit;
  transition: border-color .15s; min-height: 42px; max-height: 200px;
}
.chat-textarea:focus { border-color: #1a73e8; }
.rag-toggle {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 8px 12px; background: #fff; border: 1px solid #d0d0d0; border-radius: 8px;
  font-size: 13px; cursor: pointer; white-space: nowrap; transition: all .15s; color: #888;
}
.rag-toggle.on { border-color: #1a73e8; color: #1a73e8; background: #e8f0fe; }
.send-btn {
  padding: 8px 20px; background: #1a73e8; color: #fff; border: none; border-radius: 8px;
  font-size: 14px; cursor: pointer; transition: opacity .15s; white-space: nowrap;
}
.send-btn:disabled { opacity: .4; cursor: not-allowed; }
.send-btn:hover:not(:disabled) { opacity: .85; }
</style>
