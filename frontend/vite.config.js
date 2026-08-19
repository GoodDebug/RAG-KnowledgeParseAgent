import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import AutoImport from 'unplugin-auto-import/vite'
import { NaiveUiResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    // P1：Naive UI 按需引入（tree-shake）——组件（n-tabs/n-button 等）+ 组合式（useMessage 等）自动导入，
    // 仅限 P1/P2 新页面与工作台 tab 容器使用；不自动导入 vue/vue-router（既有代码保持显式 import 风格）。
    Components({ resolvers: [NaiveUiResolver()] }),
    AutoImport({ resolvers: [NaiveUiResolver()] }),
  ],
  // P1：体积控制——naive-ui / relation-graph / vue 拆分独立 chunk（主包瘦身，vendor 可缓存）
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'naive': ['naive-ui'],
          'graph': ['@relation-graph/vue'],
          'vue-vendor': ['vue', 'vue-router'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        timeout: 120000,
        proxyTimeout: 120000,
      }
    }
  }
})
