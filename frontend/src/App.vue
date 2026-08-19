<template>
  <!-- P1：Naive UI 主题注入点（NConfigProvider 是组件，包在 App 最外层；themeOverrides 对齐风格基线 #1a73e8） -->
  <n-config-provider :theme-overrides="themeOverrides">
  <div class="app-container">
    <nav class="nav-bar">
      <router-link to="/chat" class="nav-link" active-class="active"><AppIcon name="MessageSquare" :size="16" /> 对话</router-link>
      <router-link to="/ingest" class="nav-link" active-class="active"><AppIcon name="FolderOpen" :size="16" /> 知识库</router-link>
      <router-link to="/crawler" class="nav-link" active-class="active"><AppIcon name="Bug" :size="16" /> 网络爬虫</router-link>
      <router-link to="/deconstruct" class="nav-link" active-class="active"><AppIcon name="Book" :size="16" /> 解构</router-link>
      <router-link v-if="!isLoggedIn" to="/login" class="nav-link nav-right" active-class="active"><AppIcon name="KeyRound" :size="16" /> 登录/注册</router-link>
      <a v-else href="#" class="nav-link nav-right" @click.prevent="logout"><AppIcon name="LogOut" :size="16" /> 退出</a>
    </nav>
    <main class="main-content">
      <router-view />
    </main>
  </div>
  </n-config-provider>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from './components/AppIcon.vue'

const router = useRouter()
// Naive UI 主题 overrides：映射前端风格基线 token（主色 #1a73e8、圆角、字体），P1/P2 新页面视觉一致
const themeOverrides = {
  common: {
    primaryColor: '#1a73e8',
    primaryColorHover: '#1765cc',
    primaryColorPressed: '#0d5bbd',
    primaryColorSuppl: '#1a73e8',
    borderRadius: '8px',
    fontSize: '14px',
    fontFamily: "-apple-system, 'Segoe UI', 'Noto Sans SC', sans-serif",
  },
}
const isLoggedIn = ref(!!localStorage.getItem('token'))
// 登录/退出后刷新导航状态
router.afterEach(() => { isLoggedIn.value = !!localStorage.getItem('token') })

function logout() {
  localStorage.removeItem('token')
  isLoggedIn.value = false
  router.push('/login')
}
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', 'Noto Sans SC', sans-serif; background: #f5f5f5; color: #333; }
.app-container { display: flex; flex-direction: column; height: 100vh; }
.nav-bar {
  display: flex; gap: 4px; padding: 8px 16px; background: #fff;
  border-bottom: 1px solid #e0e0e0; flex-shrink: 0;
}
.nav-link {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 6px 16px; text-decoration: none; color: #666;
  border-radius: 6px; font-size: 14px; transition: all .15s;
}
.nav-link:hover { background: #f0f0f0; }
.nav-link.active { background: #e8f0fe; color: #1a73e8; font-weight: 600; }
.main-content { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
</style>
