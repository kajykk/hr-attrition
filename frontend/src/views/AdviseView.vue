<script setup lang="ts">
// AI 保留建议视图 - SSE 流式接收 + 打字动画
import { ref, onMounted, nextTick, useTemplateRef } from 'vue'
import { apiClient } from '@/api/client'
import type { WarningOut, Paginated, AdviseMetadata } from '@/api/types'

const warningId = ref('')
const predictionId = ref('')
const streaming = ref(false)
const done = ref(false)
const adviceText = ref('')
const metadata = ref<AdviseMetadata | null>(null)
const errorMsg = ref('')

// 预警下拉选项
const warningOptions = ref<Array<{ id: string; label: string }>>([])

// 输出区引用（用于自动滚动）
const outputRef = useTemplateRef<HTMLElement>('outputRef')

async function loadWarningOptions() {
  try {
    const { data } = await apiClient.get<Paginated<WarningOut>>('/api/v1/warnings', {
      params: { page: 1, page_size: 50 },
    })
    warningOptions.value = data.items.map((w) => ({
      id: w.id,
      label: `${w.id} · ${w.level} · 员工 ${w.employee_id} · 分 ${w.risk_score}`,
    }))
  } catch {
    // 接口不可用时填演示选项
    warningOptions.value = [
      { id: 'w-001', label: 'w-001 · P0 · 员工 emp-001 · 分 87' },
      { id: 'w-002', label: 'w-002 · P1 · 员工 emp-042 · 分 64' },
      { id: 'w-003', label: 'w-003 · P2 · 员工 emp-118 · 分 48' },
    ]
  }
}

function onWarningSelect() {
  // 选中后自动填充
}

async function generate() {
  if (!warningId.value) {
    errorMsg.value = '请输入或选择预警 ID'
    return
  }
  errorMsg.value = ''
  streaming.value = true
  done.value = false
  adviceText.value = ''
  metadata.value = null

  const token = localStorage.getItem('hra_token') || ''
  const userStr = localStorage.getItem('hra_user')
  let tenantId = ''
  if (userStr) {
    try {
      tenantId = JSON.parse(userStr)?.tenant_id || ''
    } catch {
      /* ignore */
    }
  }

  // 构造 query
  const qs = new URLSearchParams()
  qs.set('warning_id', warningId.value)
  if (predictionId.value) qs.set('prediction_id', predictionId.value)

  try {
    // 用 fetch + ReadableStream 解析 SSE（不用 EventSource，因为要 POST）
    const resp = await fetch(`/api/v1/advise/stream?${qs.toString()}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        'X-Tenant-Id': tenantId,
      },
    })
    if (!resp.ok || !resp.body) {
      throw new Error(`SSE 请求失败（${resp.status}）`)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done: readerDone, value } = await reader.read()
      if (readerDone) break
      buffer += decoder.decode(value, { stream: true })
      // SSE 事件以双换行分隔
      const events = buffer.split('\n\n')
      buffer = events.pop() || ''
      for (const evt of events) {
        // 提取 data: 行
        const dataLines = evt
          .split('\n')
          .filter((l) => l.startsWith('data:'))
          .map((l) => l.replace(/^data:\s*/, ''))
        const dataStr = dataLines.join('\n').trim()
        if (!dataStr) continue
        if (dataStr === '[DONE]') {
          done.value = true
          continue
        }
        try {
          const obj = JSON.parse(dataStr) as { chunk?: string; metadata?: AdviseMetadata }
          if (obj.chunk) {
            adviceText.value += obj.chunk
            scrollToBottom()
          }
          if (obj.metadata) {
            metadata.value = obj.metadata
          }
        } catch {
          // 非 JSON 文本，直接当 chunk
          adviceText.value += dataStr
          scrollToBottom()
        }
      }
    }
    // 流结束但未收到 [DONE] 也标记完成
    done.value = true
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err?.message || '生成失败，使用演示建议'
    // 演示模式：模拟流式输出
    await simulateStreaming()
  } finally {
    streaming.value = false
  }
}

// 演示模式：模拟 SSE 流式输出
async function simulateStreaming() {
  const demoText = `【保留建议 - 演示数据】

针对该员工的高离职风险，建议从以下维度制定保留方案：

1. **薪酬调整**：当前薪酬竞争力低于行业 25 分位，建议在下次调薪窗口提升 8%-12%，或设置季度绩效奖金。

2. **工作负荷优化**：近 30 天加班时长显著高于部门均值（约 1.8 倍），建议：
   - 重新评估项目排期
   - 增加协同人力或调整任务分配
   - 强制执行年假计划

3. **职业发展通道**：与员工进行 1:1 沟通，明确未来 6-12 个月的晋升路径与培训计划，安排 mentor 辅导。

4. **管理者干预**：建议直属上级本周内完成一次深度沟通，关注员工近期情绪与诉求，HRBP 协同跟进。

5. **风险监控**：将预警状态推进至 fixing，每 2 周复评一次风险分，若 4 周内无改善需升级至 P0。`
  adviceText.value = ''
  metadata.value = {
    model: 'qwen-max-demo',
    tokens_used: 487,
    latency_ms: 1842,
  }
  // 逐字输出
  for (const ch of demoText) {
    if (!streaming.value) break
    adviceText.value += ch
    scrollToBottom()
    await new Promise((r) => setTimeout(r, 12))
  }
  done.value = true
}

async function scrollToBottom() {
  await nextTick()
  if (outputRef.value) {
    outputRef.value.scrollTop = outputRef.value.scrollHeight
  }
}

function clearOutput() {
  adviceText.value = ''
  metadata.value = null
  done.value = false
  errorMsg.value = ''
}

onMounted(loadWarningOptions)
</script>

<template>
  <div class="page">
    <h2 class="page-title">AI 保留建议</h2>
    <p class="page-desc">通义千问 Max SSE 流式生成（PII 脱敏后调用，D03 4.4 + ADR-003）</p>

    <div v-if="errorMsg" class="banner warning">⚠ {{ errorMsg }}</div>

    <div class="card input-card">
      <div class="form-row">
        <div class="field">
          <label>选择预警</label>
          <select v-model="warningId" @change="onWarningSelect">
            <option value="">-- 请选择 --</option>
            <option v-for="o in warningOptions" :key="o.id" :value="o.id">{{ o.label }}</option>
          </select>
        </div>
        <div class="field">
          <label>预测 ID（可选）</label>
          <input v-model="predictionId" placeholder="如 pred-001" />
        </div>
        <div class="field-actions">
          <button @click="generate" :disabled="streaming || !warningId">
            {{ streaming ? '生成中...' : '生成建议' }}
          </button>
          <button class="secondary" @click="clearOutput" :disabled="streaming">清空</button>
        </div>
      </div>
      <p class="tip">也可手动输入预警 ID 后点击"生成建议"</p>
    </div>

    <div class="card output-card">
      <div class="output-head">
        <h3 class="card-title">保留建议</h3>
        <div v-if="streaming" class="typing-indicator">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          生成中
        </div>
        <span v-else-if="done" class="done-tag">[DONE]</span>
      </div>

      <div v-if="!adviceText && !streaming" class="empty">点击"生成建议"开始</div>

      <div v-else ref="outputRef" class="output-body">
        <pre>{{ adviceText }}<span v-if="streaming" class="cursor">▋</span></pre>
      </div>

      <div v-if="metadata" class="metadata">
        模型：<strong>{{ metadata.model || '-' }}</strong>
        | tokens：<strong>{{ metadata.tokens_used ?? '-' }}</strong>
        | 延迟：<strong>{{ metadata.latency_ms ?? '-' }}ms</strong>
      </div>
    </div>
  </div>
</template>

<style scoped>
.input-card {
  margin-bottom: 16px;
}
.form-row {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  flex-wrap: wrap;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 200px;
}
.field label {
  font-size: 13px;
  color: var(--color-text-muted);
}
.field select,
.field input {
  width: 100%;
}
.field-actions {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.tip {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-top: 8px;
}

.output-card {
  min-height: 320px;
  display: flex;
  flex-direction: column;
}
.output-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
}
.typing-indicator {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--color-primary);
}
.typing-indicator .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-primary);
  animation: blink 1.4s infinite both;
}
.typing-indicator .dot:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator .dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 80%, 100% { opacity: 0.3; }
  40% { opacity: 1; }
}
.done-tag {
  font-size: 12px;
  background: #f0fdf4;
  color: #166534;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid #bbf7d0;
}
.output-body {
  flex: 1;
  overflow-y: auto;
  max-height: 60vh;
}
.output-body pre {
  white-space: pre-wrap;
  word-wrap: break-word;
  background: #f8fafc;
  padding: 16px;
  border-radius: 6px;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.7;
  margin: 0;
}
.cursor {
  animation: blink 1s infinite;
  color: var(--color-primary);
}
.metadata {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--color-border);
  font-size: 12px;
  color: var(--color-text-muted);
}
.metadata strong {
  color: var(--color-text);
  font-weight: 600;
}

@media (max-width: 640px) {
  .form-row { flex-direction: column; align-items: stretch; }
  .field-actions { width: 100%; }
  .field-actions button { flex: 1; }
}
</style>
