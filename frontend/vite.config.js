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
            '/api': {
                target: 'http://localhost:8001',
                changeOrigin: true,
            },
            '/ws': {
                target: 'ws://localhost:8001',
                ws: true,
                changeOrigin: true,
            },
        },
    },
});
