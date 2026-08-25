import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';
// Vite 配置 - 代理 /api → http://localhost:8000（D03 2.2 前后端联调）
export default defineConfig({
    plugins: [vue()],
    resolve: {
        alias: {
            '@': fileURLToPath(new URL('./src', import.meta.url)),
        },
    },
    server: {
        port: 5173,
        host: '0.0.0.0',
        proxy: {
            // 用 127.0.0.1 而非 localhost：Node 可能将 localhost 解析为 IPv6 ::1，
            // 而 api 容器只绑定 IPv4 回环，会导致代理 503
            '/api': {
                target: 'http://127.0.0.1:8000',
                changeOrigin: true,
            },
            '/ws': {
                target: 'ws://127.0.0.1:8000',
                ws: true,
                changeOrigin: true,
            },
        },
    },
});
