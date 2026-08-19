import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import ChatView from './views/ChatView.vue'
import IngestView from './views/IngestView.vue'
import CrawlerView from './views/CrawlerView.vue'
import LoginView from './views/LoginView.vue'
// 解构统一工作台（左书列 + 右三栏）；三个旧独立页面路由改 redirect 兼容旧链接
import NovelWorkspaceView from './views/NovelWorkspaceView.vue'

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', component: ChatView, meta: { title: '对话' } },
  { path: '/ingest', component: IngestView, meta: { title: '入库' } },
  { path: '/crawler', component: CrawlerView, meta: { title: '爬虫' } },
  { path: '/deconstruct', component: NovelWorkspaceView, meta: { title: '解构工作台' } },
  // 旧路由重定向（函数形式：字符串 redirect 的 :param 只作用于 path，query 需函数注入）
  // P1：统一落到 /deconstruct 工作台「解构」tab（?tab=deconstruct）
  { path: '/books/:book_id', redirect: (to) => ({ path: '/deconstruct', query: { book_id: to.params.book_id, tab: 'deconstruct' } }) },
  { path: '/books/:book_id/jobs', redirect: (to) => ({ path: '/deconstruct', query: { book_id: to.params.book_id, tab: 'deconstruct' } }) },
  { path: '/jobs/:job_id', redirect: (to) => ({ path: '/deconstruct', query: { job_id: to.params.job_id, tab: 'deconstruct' } }) },
  { path: '/login', component: LoginView, meta: { title: '登录' } },
]

const router = createRouter({ history: createWebHistory(), routes })

// Spec-E：全局登录守卫（无 token → /login?redirect=原路径；已登录访问 /login → /chat）
router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.path === '/login') return token ? '/chat' : true
  if (!token) return { path: '/login', query: { redirect: to.fullPath } }
  return true
})
const app = createApp(App)
app.use(router)
app.mount('#app')
