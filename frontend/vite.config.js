var _a;
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import AutoImport from 'unplugin-auto-import/vite';
import Components from 'unplugin-vue-components/vite';
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers';
import { VitePWA } from 'vite-plugin-pwa';
import { compression } from 'vite-plugin-compression2';
import { fileURLToPath, URL } from 'node:url';
// Vite 配置 - 代理 /api → 后端（D03 2.2 前后端联调）
// 可用环境变量覆盖：VITE_DEV_HOST（默认 localhost，不暴露局域网）、
// API_PROXY_TARGET（默认 http://127.0.0.1:8000）
var apiTarget = (_a = process.env.API_PROXY_TARGET) !== null && _a !== void 0 ? _a : 'http://127.0.0.1:8000';
var wsTarget = apiTarget.replace(/^http/, 'ws');
export default defineConfig(function (_a) {
    var _b;
    var command = _a.command;
    return ({
        plugins: [
            vue(),
            // 按需自动导入 Vue 组合式 API / 路由 / Pinia，减少手动 import 模板代码
            AutoImport({
                imports: ['vue', 'vue-router', 'pinia'],
                resolvers: [ElementPlusResolver()],
                dts: 'src/auto-imports.d.ts',
            }),
            // 按需自动注册 Element Plus 组件（配合 elementPlus.ts 中的按需注册）
            Components({
                dts: 'src/components.d.ts',
                resolvers: [ElementPlusResolver()],
                dirs: ['src/components'],
            }),
            // PWA 支持：离线缓存静态资源 / 图片 / 字体
            VitePWA({
                registerType: 'autoUpdate',
                injectRegister: 'auto',
                manifest: {
                    name: 'HRA - 离职风险预警系统',
                    short_name: 'HRA',
                    description: '员工离职风险预测与预警管理平台',
                    theme_color: '#0f766e',
                    background_color: '#ffffff',
                    display: 'standalone',
                    start_url: '/',
                    scope: '/',
                    lang: 'zh-CN',
                    icons: [
                        {
                            src: '/pwa-icon.svg',
                            sizes: 'any',
                            type: 'image/svg+xml',
                            purpose: 'any',
                        },
                        {
                            src: '/pwa-icon.svg',
                            sizes: 'any',
                            type: 'image/svg+xml',
                            purpose: 'maskable',
                        },
                    ],
                },
                workbox: {
                    globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2}'],
                    navigateFallback: '/index.html',
                    navigateFallbackDenylist: [/^\/api\//, /^\/ws\//],
                    runtimeCaching: [
                        {
                            // API 请求保持网络直连（敏感业务数据不落盘到 Cache Storage）
                            urlPattern: /^https?:\/\/[^/]+\/api\/.*$/,
                            handler: 'NetworkOnly',
                            options: {},
                        },
                        {
                            urlPattern: /\.(?:png|jpg|jpeg|svg|gif)$/,
                            handler: 'StaleWhileRevalidate',
                            options: {
                                cacheName: 'image-cache',
                                expiration: {
                                    maxEntries: 50,
                                    maxAgeSeconds: 60 * 60 * 24 * 7,
                                },
                            },
                        },
                        {
                            urlPattern: /\.(?:woff2|woff)$/,
                            handler: 'CacheFirst',
                            options: {
                                cacheName: 'font-cache',
                                expiration: {
                                    maxEntries: 20,
                                    maxAgeSeconds: 60 * 60 * 24 * 365,
                                },
                            },
                        },
                    ],
                    skipWaiting: true,
                    clientsClaim: true,
                    cleanupOutdatedCaches: true,
                },
                devOptions: { enabled: false },
            }),
            // Brotli 预压缩（生产环境）：比 gzip 小 15-25%
            command === 'build' &&
                compression({
                    algorithms: ['brotliCompress'],
                    exclude: [/\.png$/, /\.jpg$/, /\.jpeg$/, /\.gif$/, /\.webp$/, /\.ico$/, /\.woff2$/],
                    threshold: 1024,
                }),
        ],
        resolve: {
            alias: {
                '@': fileURLToPath(new URL('./src', import.meta.url)),
            },
        },
        server: {
            port: 5173,
            host: (_b = process.env.VITE_DEV_HOST) !== null && _b !== void 0 ? _b : 'localhost',
            proxy: {
                // 用 127.0.0.1 而非 localhost：Node 可能将 localhost 解析为 IPv6 ::1，
                // 而 api 容器只绑定 IPv4 回环，会导致代理 503
                '/api': {
                    target: apiTarget,
                    changeOrigin: true,
                },
                '/ws': {
                    target: wsTarget,
                    ws: true,
                    changeOrigin: true,
                },
            },
        },
        build: {
            // 压缩：保留 esbuild 默认（快），CSS 代码分割
            cssCodeSplit: true,
            sourcemap: false,
            // 生产环境 drop console/debugger（dev 保留）
            esbuild: {
                drop: command === 'build' ? ['console', 'debugger'] : undefined,
            },
            // 对压缩率较高的产物启用 minify，JS 用默认 esbuild
            target: 'es2020',
            // Bundle 体积告警阈值：暴露需要关注的大 chunk
            chunkSizeWarningLimit: 500,
            rollupOptions: {
                output: {
                    // 将入口 chunk 名称保持可读（vs hash-only）
                    entryFileNames: 'assets/[name]-[hash].js',
                    chunkFileNames: 'assets/[name]-[hash].js',
                    // 图片归入独立目录
                    assetFileNames: function (assetInfo) {
                        var info = assetInfo.name || '';
                        if (/\.(png|jpe?g|gif|svg)$/.test(info))
                            return 'assets/images/[name]-[hash][extname]';
                        if (info.endsWith('.css'))
                            return 'assets/[name]-[hash][extname]';
                        return 'assets/[name]-[hash][extname]';
                    },
                    // 代码分割：按功能分组 vendor 依赖
                    manualChunks: function (id) {
                        if (!id.includes('node_modules'))
                            return undefined;
                        // Element Plus（必须先于 vue 匹配，因为其含 @vue 模块）
                        if (id.includes('element-plus') || id.includes('@element-plus/icons-vue'))
                            return 'element-plus';
                        // Vue 生态核心
                        if (id.includes('vue-router'))
                            return 'router';
                        if (id.includes('pinia'))
                            return 'state';
                        if (/[\\/]node_modules[\\/](@vue|vue)[\\/]/.test(id))
                            return 'vue-core';
                        // HTTP 客户端
                        if (id.includes('axios'))
                            return 'http';
                        // 其余依赖
                        return 'vendor';
                    },
                },
            },
        },
    });
});
