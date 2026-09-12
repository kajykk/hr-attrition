// SSE 解析器单测：帧分割 / 多行 data / 跨 chunk 边界 / 中止语义
import { describe, expect, it } from 'vitest'
import { consumeSSE, type SSEFrame } from '@/api/sse'

function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder()
  // 每个元素作为一个网络 chunk 下发（可故意切断帧边界）
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c))
      controller.close()
    },
  })
  return new Response(stream, { status })
}

describe('consumeSSE', () => {
  it('解析 event+data 帧，缺省 event 为 message', async () => {
    const frames: SSEFrame[] = []
    await consumeSSE(
      sseResponse(['event: token\ndata: {"text":"hi"}\n\n', 'data: plain\n\n']),
      (f) => frames.push(f),
    )
    expect(frames).toEqual([
      { event: 'token', data: '{"text":"hi"}' },
      { event: 'message', data: 'plain' },
    ])
  })

  it('按 SSE 规范将多行 data 以 \\n 连接', async () => {
    const frames: SSEFrame[] = []
    await consumeSSE(sseResponse(['data: line1\ndata: line2\n\n']), (f) => frames.push(f))
    expect(frames).toEqual([{ event: 'message', data: 'line1\nline2' }])
  })

  it('帧被网络分块切断时仍能正确拼装（跨 chunk 边界）', async () => {
    const frames: SSEFrame[] = []
    await consumeSSE(
      sseResponse(['event: done\nda', 'ta: {"answer":"ok"}', '\n\n']),
      (f) => frames.push(f),
    )
    expect(frames).toEqual([{ event: 'done', data: '{"answer":"ok"}' }])
  })

  it('忽略无 data 行的空帧与纯注释帧', async () => {
    const frames: SSEFrame[] = []
    await consumeSSE(
      sseResponse([': keepalive comment\n\n', 'event: token\n\n', 'data: x\n\n']),
      (f) => frames.push(f),
    )
    expect(frames).toEqual([{ event: 'message', data: 'x' }])
  })

  it('abort 后静默结束（不抛错），已收到的帧照常回调', async () => {
    const controller = new AbortController()
    const frames: SSEFrame[] = []
    const encoder = new TextEncoder()
    // 不 close 的持续流；abort 触发后底层读取以 AbortError 失败
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(encoder.encode('data: first\n\n'))
        controller.signal.addEventListener('abort', () =>
          c.error(new DOMException('aborted', 'AbortError')),
        )
      },
    })
    await consumeSSE(
      { status: 200, body: stream } as unknown as Response,
      (f) => {
        frames.push(f)
        if (frames.length === 1) controller.abort()
      },
      controller.signal,
    )
    expect(frames.map((f) => f.data)).toEqual(['first'])
  })

  it('响应无 body 时抛出带状态码的错误', async () => {
    await expect(
      consumeSSE({ status: 502, body: null } as unknown as Response, () => {}),
    ).rejects.toThrow('SSE 请求失败（502）')
  })
})
