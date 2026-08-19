<template>
  <div class="login-view">
    <div class="login-card">
      <h2>AI 客服系统</h2>
      <p class="sub">{{ mode === 'login' ? '登录后开始对话' : '注册一个新账号' }}</p>
      <input v-model="account" placeholder="手机号或邮箱" class="field" :disabled="loading" />
      <input v-model="password" type="password" placeholder="密码（≥6 位）" class="field" :disabled="loading"
             @keyup.enter="submit" />
      <div class="row">
        <button class="btn primary" :disabled="loading" @click="submit">
          {{ loading ? '处理中…' : (mode === 'login' ? '登录' : '注册') }}
        </button>
        <button class="btn" :disabled="loading" @click="toggleMode">{{ mode === 'login' ? '去注册' : '去登录' }}</button>
      </div>
      <p v-if="msg" :class="['msg', { ok: msgType === 'ok' }]">{{ msg }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { login, register } from '../api'

const router = useRouter()
const route = useRoute()
const mode = ref('login')      // 'login' | 'register'
const account = ref('')
const password = ref('')
const loading = ref(false)
const msg = ref('')
const msgType = ref('err')

function toggleMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  msg.value = ''
}

async function submit() {
  msg.value = ''
  if (!account.value || !password.value) { msg.value = '请填写账号与密码'; return }
  if (mode.value === 'register' && password.value.length < 6) { msg.value = '密码至少 6 位'; return }
  loading.value = true
  try {
    if (mode.value === 'login') {
      const data = await login(account.value, password.value)
      localStorage.setItem('token', data.access_token)
      const redirect = (route.query.redirect) || '/chat'
      router.replace(redirect)
    } else {
      // 注册：按账号内容区分 phone/email
      await register({
        phone: /^\d+$/.test(account.value) ? account.value : null,
        email: /@/.test(account.value) ? account.value : null,
        password: password.value,
      })
      msgType.value = 'ok'
      msg.value = '注册成功，请登录'
      mode.value = 'login'
    }
  } catch (e) {
    msgType.value = 'err'
    msg.value = e.message || '操作失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-view { flex: 1; display: flex; align-items: center; justify-content: center; background: #f5f5f5; }
.login-card { width: 320px; background: #fff; border-radius: 12px; padding: 28px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
.login-card h2 { margin: 0 0 4px; font-size: 20px; }
.sub { color: #999; font-size: 12px; margin: 0 0 16px; }
.field { width: 100%; padding: 10px 12px; margin-bottom: 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; box-sizing: border-box; }
.row { display: flex; gap: 8px; }
.btn { flex: 1; padding: 10px 0; border: 1px solid #ddd; border-radius: 8px; background: #fff; cursor: pointer; font-size: 14px; }
.btn.primary { background: #1a73e8; color: #fff; border-color: #1a73e8; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.msg { font-size: 13px; margin: 12px 0 0; }
.msg.err { color: #d32f2f; }
.msg.ok { color: #188038; }
</style>
