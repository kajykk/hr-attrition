<script setup lang="ts">
// RAG 知识库视图 - 文档管理（上传/进度/删除）+ 制度问答（SSE 流式 + 引用溯源）
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import {
  deleteDocument,
  getDocumentStatus,
  listDocuments,
  streamKnowledgeBase,
  uploadDocument,
  type KbCitation,
  type KbDocument,
} from '@/api/kb'

const auth = useAuthStore()
const canManage = computed(() =>
  ['admin', 'hr_manager'].includes(auth.user?.role ?? ''),
)

// ===== 文档管理 =====
const documents = ref<KbDocument[]>([])
const uploading = ref(false)
const loadingDocs = ref(false)
const fileInput = useTemplateRef<HTMLInputElement>('fileInput')
let pollTimer: ReturnType<typeof setInterval> | null = null

async function refreshDocs() {
  loadingDocs.value = true
  try {
    documents.value = await listDocuments()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loadingDocs.value = false
  }
}

function pickFile() {
  fileInput.value?.click()
}

async function onFileChosen(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.warning('文件超过 20MB 上限')
    return
  }
  uploading.value = true
  try {
    const { document_id, deduplicated } = await uploadDocument(file)
    ElMessage.success(deduplicated ? '文档已存在，无需重复上传' : '已开始解析索引')
    await refreshDocs()
    startPolling(document_id)
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '上传失败')
  } finally {
    uploading.value = false
  }
}

// processing 状态轮询（当前为轮询通道；WS 推送为后续增强）
function startPolling(documentId: string) {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const doc = await getDocumentStatus(documentId)
      const idx = documents.value.findIndex((d) => d.id === documentId)
      if (idx >= 0) documents.value[idx] = doc
      if (doc.status !== 'processing') stopPolling()
      if (doc.status === 'ready') ElMessage.success(`《${doc.title}》索引完成`)
      if (doc.status === 'failed') ElMessage.error(`《${doc.title}》解析失败：${doc.error_message ?? ''}`)
    } catch {
      stopPolling()
    }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

async function removeDoc(doc: KbDocument) {
  try {
    await ElMessageBox.confirm(`确认删除《${doc.title}》及其全部切片？`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return // 用户取消
  }
  try {
    await deleteDocument(doc.id)
    ElMessage.success('已删除')
    await refreshDocs()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  }
}

const statusLabel: Record<string, string> = {
  processing: '解析中',
  ready: '已就绪',
  failed: '失败',
}
const statusType: Record<string, 'warning' | 'success' | 'danger'> = {
  processing: 'warning',
  ready: 'success',
  failed: 'danger',
}

// ===== 知识库问答 =====
interface ChatTurn {
  role: 'user' | 'assistant'
  text: string
  citations?: KbCitation[]
  refused?: boolean
  latencyMs?: number
}

const question = ref('')
const answering = ref(false)
const turns = ref<ChatTurn[]>([])
const chatRef = useTemplateRef<HTMLDivElement>('chatRef')

function scrollBottom() {
  void nextTickSafe()
}

async function nextTickSafe() {
  const { nextTick } = await import('vue')
  await nextTick()
  const el = chatRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function ask() {
  const q = question.value.trim()
  if (!q || answering.value) return
  question.value = ''
  turns.value.push({ role: 'user', text: q })
  scrollBottom()

  const reply: ChatTurn = { role: 'assistant', text: '' }
  turns.value.push(reply)
  answering.value = true
  try {
    const result = await streamKnowledgeBase(q, (token) => {
      reply.text += token
      scrollBottom()
    })
    reply.text = result.answer || reply.text
    reply.citations = result.citations
    reply.refused = result.refused
    reply.latencyMs = result.latency_ms
  } catch (e) {
    reply.text = e instanceof Error ? e.message : '生成失败，请重试'
  } finally {
    answering.value = false
    scrollBottom()
  }
}

onMounted(() => {
  if (canManage.value) void refreshDocs()
})
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="kb-view">
    <!-- 左栏：制度文档库 -->
    <section class="panel docs-panel">
      <header class="panel-head">
        <h2>制度文档库</h2>
        <template v-if="canManage">
          <el-button type="primary" size="small" :loading="uploading" @click="pickFile">
            上传文档
          </el-button>
          <input
            ref="fileInput"
            type="file"
            accept=".pdf,.docx,.md,.txt"
            class="hidden-input"
            @change="onFileChosen"
          />
        </template>
      </header>
      <p v-if="!canManage" class="hint">仅管理员 / HR 经理可管理制度文档</p>
      <el-table
        v-if="canManage"
        v-loading="loadingDocs"
        :data="documents"
        size="small"
        height="100%"
      >
        <el-table-column prop="title" label="标题" min-width="140" show-overflow-tooltip />
        <el-table-column label="状态" width="80">
          <template #default="scope">
            <el-tag v-if="scope?.row" :type="statusType[scope.row.status]" size="small">
              {{ statusLabel[scope.row.status] ?? scope.row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="切片" width="60" />
        <el-table-column label="" width="50">
          <template #default="scope">
            <el-button
              v-if="scope?.row"
              link
              type="danger"
              size="small"
              @click="removeDoc(scope.row)"
              >删</el-button
            >
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 右栏：制度问答 -->
    <section class="panel chat-panel">
      <header class="panel-head">
        <h2>制度智能问答</h2>
        <span class="hint">答案附引用溯源 · 无依据自动拒答</span>
      </header>
      <div ref="chatRef" class="chat-body">
        <p v-if="turns.length === 0" class="empty-hint">
          例如：年假可以跨年结转吗？/ 试用期离职需要提前几天？
        </p>
        <div
          v-for="(turn, i) in turns"
          :key="i"
          class="bubble"
          :class="turn.role"
        >
          <div class="bubble-text">{{ turn.text }}<span v-if="answering && turn.role === 'assistant' && i === turns.length - 1" class="cursor">▌</span></div>
          <div v-if="turn.citations?.length" class="citations">
            <span class="cite-title">引用来源：</span>
            <span v-for="c in turn.citations" :key="c.index" class="cite-item" :title="c.snippet">
              [{{ c.index }}] {{ c.title }}{{ c.heading_path ? ` · ${c.heading_path}` : '' }}
            </span>
          </div>
          <div v-if="turn.latencyMs" class="meta">{{ turn.latencyMs }}ms</div>
        </div>
      </div>
      <footer class="chat-foot">
        <el-input
          v-model="question"
          placeholder="向知识库提问…"
          maxlength="500"
          :disabled="answering"
          @keyup.enter="ask"
        />
        <el-button type="primary" :loading="answering" @click="ask">提问</el-button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.kb-view { display: flex; gap: 16px; height: calc(100vh - 130px); min-height: 420px; }
.panel { background: var(--el-bg-color); border-radius: 10px; border: 1px solid var(--el-border-color-lighter); display: flex; flex-direction: column; overflow: hidden; }
.docs-panel { flex: 0 0 380px; padding: 14px; }
.chat-panel { flex: 1; padding: 14px; }
.panel-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.panel-head h2 { font-size: 15px; margin: 0; }
.hidden-input { display: none; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); margin: 4px 0; }
.chat-body { flex: 1; overflow-y: auto; padding: 6px 4px; }
.empty-hint { color: var(--el-text-color-secondary); font-size: 13px; text-align: center; margin-top: 40px; }
.bubble { max-width: 82%; margin-bottom: 12px; padding: 8px 12px; border-radius: 10px; font-size: 13.5px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.bubble.user { margin-left: auto; background: var(--el-color-primary-light-9); }
.bubble.assistant { background: var(--el-fill-color-light); }
.cursor { animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0; } }
.citations { margin-top: 8px; font-size: 11.5px; color: var(--el-text-color-secondary); display: flex; flex-direction: column; gap: 2px; }
.cite-title { font-weight: 600; }
.meta { margin-top: 4px; font-size: 11px; color: var(--el-text-color-placeholder); text-align: right; }
.chat-foot { display: flex; gap: 10px; padding-top: 10px; border-top: 1px solid var(--el-border-color-lighter); }
</style>
