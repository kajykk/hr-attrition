// WebSocket 重连 composable（D05 4.1 + D03 复用清单）
// 心跳 30s，指数退避 1s/2s/4s/8s/16s/30s，自动重连 + 风险事件分发
import { ref, onUnmounted } from 'vue'

const BACKOFFS = [1000, 2000, 4000, 8000, 16000, 30000]

// 风险更新事件载荷（WS /ws/risk 推送）
export interface RiskUpdatePayload {
  type: 'risk_update'
  employee_id: string
  risk_score: number
  risk_level: string
  [key: string]: unknown
}

type Listener<T = unknown> = (payload: T) => void

export interface UseWebSocketOptions {
  // 收到消息时的全局回调
  onMessage?: (data: unknown) => void
  // 连接状态变更回调
  onStatusChange?: (connected: boolean) => void
}

/**
 * 通用 WebSocket 组合式函数
 * - 自动重连（指数退避，最多 6 次，达上限后停止）
 * - 心跳 30s（避免反向代理断开空闲连接）
 * - 监听器模式：外部可订阅特定 type 的事件
 */
export function useWebSocket(url: string, options: UseWebSocketOptions = {}) {
  const connected = ref(false)
  const lastMessage = ref<MessageEvent | null>(null)
  const reconnectAttempts = ref(0)
  let ws: WebSocket | null = null
  let retryIndex = 0
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let manualClose = false
  // 未应答心跳计数：连续 2 个 ping 无 pong → 判定半打开连接，主动断开重连
  let unansweredPings = 0

  // 事件监听器表（按 type 分组）
  const listeners = new Map<string, Set<Listener>>()

  /** 订阅某种 type 的消息 */
  function on<T = unknown>(type: string, listener: Listener<T>): () => void {
    if (!listeners.has(type)) listeners.set(type, new Set())
    listeners.get(type)!.add(listener as Listener)
    return () => listeners.get(type)?.delete(listener as Listener)
  }

  /** 触发某 type 的监听器 */
  function emit(type: string, payload: unknown) {
    listeners.get(type)?.forEach((fn) => {
      try {
        fn(payload)
      } catch (e) {
        console.warn('[useWebSocket] listener error', e)
      }
    })
  }

  function connect() {
    if (manualClose) return
    try {
      ws = new WebSocket(url)
    } catch (e) {
      console.warn('[useWebSocket] new WebSocket failed', e)
      scheduleReconnect()
      return
    }
    ws.onopen = () => {
      connected.value = true
      retryIndex = 0
      reconnectAttempts.value = 0
      unansweredPings = 0
      options.onStatusChange?.(true)
      startHeartbeat()
    }
    ws.onmessage = (event) => {
      lastMessage.value = event
      // 心跳响应过滤
      let parsed: unknown = null
      try {
        parsed = JSON.parse(event.data)
      } catch {
        return
      }
      const obj = parsed as { type?: string } | null
      if (obj?.type === 'pong') {
        unansweredPings = 0
        return
      }
      // 全局回调
      options.onMessage?.(parsed)
      // 按 type 分发
      if (obj?.type) emit(obj.type, parsed)
    }
    ws.onclose = () => {
      connected.value = false
      options.onStatusChange?.(false)
      stopHeartbeat()
      if (!manualClose) scheduleReconnect()
    }
    ws.onerror = () => {
      // 触发 onclose
      ws?.close()
    }
  }

  function startHeartbeat() {
    heartbeatTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        if (unansweredPings >= 2) {
          // 半打开连接（如休眠恢复/网络切换）：主动断开走重连，避免假活
          ws.close()
          return
        }
        // 后端约定裸文本 "ping"（ws.py 按 data === 'ping' 判定），JSON 会被当作未知消息忽略
        ws.send('ping')
        unansweredPings += 1
      }
    }, 30000)
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function scheduleReconnect() {
    if (retryIndex >= BACKOFFS.length) {
      console.warn('[useWebSocket] reached max reconnect attempts')
      return
    }
    // 清理旧定时器：防止极端时序下叠加重连任务
    if (reconnectTimer) clearTimeout(reconnectTimer)
    const delay = BACKOFFS[retryIndex++]
    reconnectAttempts.value = retryIndex
    reconnectTimer = setTimeout(connect, delay)
  }

  function send(data: unknown) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data))
    }
  }

  function close() {
    manualClose = true
    stopHeartbeat()
    if (reconnectTimer) clearTimeout(reconnectTimer)
    ws?.close()
    ws = null
    listeners.clear()
  }

  /** 重置手动关闭标记，允许再次 connect */
  function reset() {
    manualClose = false
    retryIndex = 0
    reconnectAttempts.value = 0
  }

  onUnmounted(close)

  return {
    connected,
    lastMessage,
    reconnectAttempts,
    connect,
    send,
    close,
    reset,
    on,
  }
}
