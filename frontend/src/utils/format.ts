/** 展示层格式化工具。只做"看得懂"，不做精度改写。 */

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

export function fmtNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** 金额：大额自动切换到万/亿，保持卡片不溢出。 */
export function fmtMoney(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(digits)} 亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(digits)} 万`
  return value.toFixed(digits)
}

export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function fmtSignedPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const text = `${(value * 100).toFixed(digits)}%`
  return value > 0 ? `+${text}` : text
}

export function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)}万`
  return value.toFixed(0)
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—'
  return value.slice(0, 10)
}

export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const text = value.replace('T', ' ')
  return text.slice(0, 19)
}

export function todayISO(offsetDays = 0): string {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)
  return date.toISOString().slice(0, 10)
}

export function yearsAgoISO(years = 3): string {
  const date = new Date()
  date.setFullYear(date.getFullYear() - years)
  return date.toISOString().slice(0, 10)
}

export function durationText(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes} 分 ${rest} 秒`
}

export function classForSign(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return 'num'
  return value > 0 ? 'num up' : 'num down'
}

export function parseSymbols(text: string): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const raw of text.split(/[\s,，;；]+/)) {
    const id = normalizeInstrumentId(raw)
    if (!id || seen.has(id)) continue
    seen.add(id)
    result.push(id)
  }
  return result
}

// --------------------------------------------------------------------------- //
// 多市场标的（与后端 quant/core/markets.py 的解析启发式保持一致）
// --------------------------------------------------------------------------- //

const MARKET_ALIASES: Record<string, string> = {
  CN: 'CN',
  SH: 'CN',
  SZ: 'CN',
  BJ: 'CN',
  SS: 'CN',
  US: 'US',
  NASDAQ: 'US',
  NYSE: 'US',
  HK: 'HK',
  HKG: 'HK',
  CRYPTO: 'CRYPTO',
  CC: 'CRYPTO',
  FUND: 'FUND',
  OF: 'FUND',
}

const MARKET_LABELS: Record<string, string> = {
  CN: 'A股',
  US: '美股',
  HK: '港股',
  CRYPTO: '虚拟货币',
  FUND: '基金',
}
const MARKET_TONES: Record<string, 'accent' | 'warn' | 'success' | 'neutral'> = {
  CN: 'accent',
  US: 'success',
  HK: 'warn',
  CRYPTO: 'warn',
  FUND: 'neutral',
}
const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: '¥',
  USD: '$',
  HKD: 'HK$',
  USDT: 'USDT ',
}

/** 市场标签（A股/美股/港股），未知市场原样返回。 */
export function marketLabel(market: string | null | undefined): string {
  if (!market) return ''
  return MARKET_LABELS[market.toUpperCase()] ?? market
}

/** 市场对应的徽标色调。 */
export function marketTone(market: string | null | undefined): 'accent' | 'warn' | 'success' | 'neutral' {
  if (!market) return 'neutral'
  return MARKET_TONES[market.toUpperCase()] ?? 'neutral'
}

/** 货币符号（¥/$/HK$），未知货币原样返回。 */
export function currencySymbol(currency: string | null | undefined): string {
  if (!currency) return ''
  return CURRENCY_SYMBOLS[currency.toUpperCase()] ?? currency
}

/** 价格展示精度：港股 3 位；虚拟货币按价格量级自适应；基金净值 4 位；其余 2 位。 */
export function priceDigits(market: string | null | undefined, price?: number | null): number {
  const key = (market ?? '').toUpperCase()
  if (key === 'CRYPTO') {
    const abs = Math.abs(price ?? 0)
    if (abs >= 1) return 2
    if (abs >= 0.01) return 4
    if (abs >= 0.0001) return 6
    return 8
  }
  if (key === 'FUND') return 4
  return key === 'HK' ? 3 : 2
}

/**
 * 把用户输入的任意写法归一化成规范标的 id（``MARKET:CODE``）：
 * ``600519`` → ``CN:600519``；``aapl`` → ``US:AAPL``；``700`` → ``HK:00700``；
 * ``CRYPTO:BTC/USDT`` → 同上；``FUND:007005`` → 同上；已带前缀的原样通过；
 * 无法识别返回 ``null``。
 */
export function normalizeInstrumentId(text: string): string | null {
  const raw = (text ?? '').trim().toUpperCase()
  if (!raw) return null

  // 虚拟货币交易对：必须显式前缀（裸 BASE/QUOTE 会被误判为别的东西）
  const cryptoPair = raw.match(/^(?:CRYPTO|CC)[:.]([A-Z0-9]{1,20})\/([A-Z0-9]{1,20})$/)
  if (cryptoPair) return `CRYPTO:${cryptoPair[1]}/${cryptoPair[2]}`

  // 显式前缀：MARKET:CODE / MARKET.CODE / MARKET+CODE（无分隔符时要求代码 ≥2 位，
  // 避免把 "USX" 这类美股代码误拆成 US:X）
  const prefixed = raw.match(/^([A-Z]+)([:.])?([A-Z0-9.\-]+)$/)
  if (prefixed) {
    const [, aliasRaw, sep, code] = prefixed
    const market = MARKET_ALIASES[aliasRaw]
    if (market && (sep || code.length >= 2)) {
      if (market === 'CN' && /^\d{1,6}$/.test(code)) return `CN:${code.padStart(6, '0')}`
      if (market === 'HK' && /^\d{1,5}$/.test(code)) return `HK:${code.padStart(5, '0')}`
      if (market === 'US' && /^[A-Z][A-Z0-9.\-]{0,11}$/.test(code)) return `US:${stripUsSuffix(code)}`
      if (market === 'FUND' && /^\d{1,6}$/.test(code)) return `FUND:${code.padStart(6, '0')}`
    }
  }

  // 后缀形式：600519.SH / AAPL.US / 007005.FUND
  const suffixed = raw.match(/^([A-Z0-9.\-]+)\.([A-Z]+)$/)
  if (suffixed) {
    const market = MARKET_ALIASES[suffixed[2]]
    if (market) {
      const code = suffixed[1]
      if (market === 'CN' && /^\d{1,6}$/.test(code)) return `CN:${code.padStart(6, '0')}`
      if (market === 'HK' && /^\d{1,5}$/.test(code)) return `HK:${code.padStart(5, '0')}`
      if (market === 'US' && /^[A-Z][A-Z0-9.\-]{0,11}$/.test(code)) return `US:${stripUsSuffix(code)}`
      if (market === 'FUND' && /^\d{1,6}$/.test(code)) return `FUND:${code.padStart(6, '0')}`
    }
  }

  // 启发式：6 位数字 → A 股；1~5 位 → 港股；字母 → 美股
  if (/^\d{6}$/.test(raw)) return `CN:${raw}`
  if (/^\d{1,5}$/.test(raw)) return `HK:${raw.padStart(5, '0')}`
  if (/^[A-Z][A-Z0-9.\-]{0,11}$/.test(raw)) return `US:${stripUsSuffix(raw)}`
  return null
}

const US_EXCHANGE_SUFFIXES = new Set(['OQ', 'N', 'AM', 'PS', 'NY', 'OB', 'PK', 'OP'])

function stripUsSuffix(code: string): string {
  const parts = code.split('.')
  while (parts.length > 1 && US_EXCHANGE_SUFFIXES.has(parts[parts.length - 1])) parts.pop()
  return parts.join('.')
}

/** 带货币符号的价格展示（``$343.46`` / ``HK$417.80`` / 无货币信息时纯数字）。 */
export function fmtPrice(
  value: number | null | undefined,
  currency?: string | null,
  digits = 2,
): string {
  const text = fmtNumber(value, digits)
  const symbol = currencySymbol(currency)
  return symbol ? `${symbol}${text}` : text
}
