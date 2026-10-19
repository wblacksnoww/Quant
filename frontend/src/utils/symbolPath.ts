/** 标的 → URL 路径片段 与 策略表单工具。 */

/** 规范 id → 路径片段：把虚拟货币的 ``/`` 换成 ``~``（避免破坏路由）。 */
export function symbolToSegment(symbol: string): string {
  return symbol.replace(/\//g, '~')
}

/** 路径片段 → 规范 id（React Router 已 decode，这里只还原 ``~``）。 */
export function segmentToSymbol(segment: string): string {
  const text = (segment || '').trim()
  return text.replace(/~/g, '/')
}
