// RAG 知识库 API：文档管理（multipart 上传）+ 问答（同步 JSON / SSE 流式）
import { apiClient, buildAuthHeaders, extractApiError } from '@/api/client'

export interface KbDocument {
  id: string
  title: string
  file_type: string
  status: 'processing' | 'ready' | 'failed'
  chunk_count: number
  pii_hits: number
  error_message?: string | null
  created_at?: string | null
}

export interface KbCitation {
  index: number
  title: string
  heading_path?: string
  snippet: string
}

export interface KbQueryResult {
  answer: string
  citations: KbCitation[]
  refused: boolean
  latency_ms: number
}

export async function uploadDocument(file: File): Promise<{ document_id: string; deduplicated: boolean }> {
  const form = new FormData()
  form.append('file', file)
  try {
    const { data } = await apiClient.post('/api/v1/kb/documents', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  } catch (e) {
    throw new Error(extractApiError(e, '上传失败'))
  }
}

export async function listDocuments(): Promise<KbDocument[]> {
  try {
    const { data } = await apiClient.get<{ documents: KbDocument[] }>('/api/v1/kb/documents')
    return data.documents
  } catch (e) {
    throw new Error(extractApiError(e, '文档列表加载失败'))
  }
}

export async function deleteDocument(id: string): Promise<void> {
  try {
    await apiClient.delete(`/api/v1/kb/documents/${id}`)
  } catch (e) {
    throw new Error(extractApiError(e, '删除失败'))
  }
}

export async function getDocumentStatus(id: string): Promise<KbDocument> {
  try {
    const { data } = await apiClient.get<KbDocument>(`/api/v1/kb/documents/${id}`)
    return data
  } catch (e) {
    throw new Error(extractApiError(e, '状态查询失败'))
  }
}

export async function queryKnowledgeBase(question: string): Promise<KbQueryResult> {
  try {
    const { data } = await apiClient.post<KbQueryResult>('/api/v1/kb/query', { question })
    return data
  } catch (e) {
    throw new Error(extractApiError(e, '问答失败'))
  }
}

// SSE 流式问答：onToken 逐段回调；resolve 于 done 帧
export function streamKnowledgeBase(
  question: string,
  onToken: (text: string) => void,
): Promise<KbQueryResult> {
  return new Promise((resolve, reject) => {
    void (async () => {
      try {
        const resp = await fetch('/api/v1/kb/query/stream', {
          method: 'POST',
          headers: buildAuthHeaders(),
          body: JSON.stringify({ question }),
        })
        if (!resp.ok || !resp.body) {
          const detail = await resp.json().catch(() => null)
          reject(new Error(detail?.detail || `流式请求失败(${resp.status})`))
          return
        }

        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let result: KbQueryResult | null = null

        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          // SSE 帧以空行分隔
          let sep: number
          while ((sep = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, sep)
            buffer = buffer.slice(sep + 2)
            let event = 'message'
            let payload = ''
            for (const line of frame.split('\n')) {
              if (line.startsWith('event:')) event = line.slice(6).trim()
              else if (line.startsWith('data:')) payload += line.slice(5).trim()
            }
            if (!payload) continue
            const parsed = JSON.parse(payload) as Record<string, unknown>
            if (event === 'token') onToken(parsed.text as string)
            else if (event === 'done')
              result = parsed as unknown as KbQueryResult
            else if (event === 'error')
              reject(new Error((parsed.detail as string) || '生成中断'))
          }
        }
        if (result) resolve(result)
        else reject(new Error('流式响应未返回结果'))
      } catch (e) {
        reject(e instanceof Error ? e : new Error('流式请求异常'))
      }
    })()
  })
}
