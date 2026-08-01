/// <reference types="vite/client" />

// Vue SFC 类型声明
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- Vue 官方 shim 惯例
  const component: DefineComponent<{}, {}, any>
  export default component
}
