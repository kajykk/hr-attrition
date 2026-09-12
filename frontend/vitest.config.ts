import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Vitest 测试配置：
//   - 纯 TS 模块（SSE 解析器 / 格式化工具）默认 node 环境
//   - 组件/守卫测试用 jsdom（文件内以 // @vitest-environment jsdom 声明）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      // 覆盖率阈值：作为防回退护栏（防止新代码整体拉低覆盖），非目标线。
      // 当前基线约 40%（视图层为 0%），逐步补测后按需上调。
      thresholds: {
        lines: 38,
        functions: 30,
        statements: 38,
        branches: 20,
      },
      // 排除自动生成与入口文件（不真实反映业务覆盖）
      exclude: [
        'src/main.ts',
        'src/auto-imports.d.ts',
        'src/components.d.ts',
        'tests/**',
        'public/**',
        'dist/**',
      ],
    },
  },
})
