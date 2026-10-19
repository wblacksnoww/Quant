/** 策略参数与风控预设的共用小工具（详情页与纸盘页语义一致）。 */

import type { RiskPresetRule, RiskPresets, Strategy } from '../api/types'

/** 策略默认参数。 */
export function defaultsFor(strategy?: Strategy): Record<string, unknown> {
  if (!strategy) return {}
  const values: Record<string, unknown> = {}
  for (const param of strategy.params) values[param.name] = param.default
  return values
}

/** 过滤空值（json 类型由调用方单独处理）。 */
export function cleanParams(
  strategy: Strategy | undefined,
  values: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const spec of strategy?.params ?? []) {
    if (spec.type === 'json') continue
    const value = values[spec.name]
    if (value === '' || value === undefined || value === null) continue
    out[spec.name] = value
  }
  return out
}

/** 风控预设 → 提交给引擎的规则列表。 */
export function riskRulesFor(presets: RiskPresets | null, key: string): RiskPresetRule[] | null {
  const rules = presets?.presets?.[key]
  if (!rules) return null
  return rules.map((rule) => ({
    id: rule.id,
    enabled: rule.enabled ?? true,
    limit: rule.limit,
    max_orders: rule.max_orders,
    window_seconds: rule.window_seconds,
    symbols: rule.symbols,
  }))
}
