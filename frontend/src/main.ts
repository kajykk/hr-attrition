import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setupElementPlus } from './plugins/elementPlus'
import { initWebVitals } from './utils/webVitals'
import './styles/main.css'

// 性能监控：Core Web Vitals 采集（LCP/INP/CLS/FCP/TTFB）
// 生产环境通过 reporter 接入 Sentry/埋点，开发环境静默
initWebVitals()

const app = createApp(App)
app.use(createPinia())
app.use(router)
setupElementPlus(app)
app.mount('#app')
