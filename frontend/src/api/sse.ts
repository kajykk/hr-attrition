// SSE 流式响应统一解析器（单一实现，供知识库问答与 AI 建议复用）
//
// 规范要点：
//   - 事件帧以空行（\n\n）分隔
//   - event: 字段缺省为 message
//   - 多行 data: 行按规范以 \n 连接（此前两份手写实现均未正确处理）

export interface SSEFrame {
  event: string
  data: string
}

/**
 * 消费 fetch Response 的 SSE 流，逐帧回调 onFrame。
 *
 * @param resp   已发出的 fetch Response（需 ok 且有 body）
 * @param onFrame 每个完整事件帧的回调
 * @param signal 可选中止信号（组件卸载 / 用户停止时 abort，读取中断静默结束）
 */
export async function consumeSSE(
  resp: Response,
  onFrame: (frame: SSEFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!resp.body) throw new Error(`SSE 请求失败（${resp.status}）`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buffer.indexOf('\n\n')) >= 0) {
        const frameText = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const frame = parseFrame(frameText)
        if (frame) onFrame(frame)
      }
    }
  } catch (e) {
    // 主动中止不算错误：调用方（组件卸载/用户停止）预期内中断
    if (signal?.aborted) return
    throw e
  }
}

function parseFrame(frameText: string): SSEFrame | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of frameText.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
  }
  if (!dataLines.length) return null
  return { event, data: dataLines.join('\n') }
}
