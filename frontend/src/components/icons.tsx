/** 内联图标（避免引入额外依赖）。统一 24x24 视图框、1.7 描边。 */

import type { ReactNode, SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

function Base({ children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  )
}

export const IconDashboard = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="3" width="7" height="9" rx="1.5" />
    <rect x="14" y="3" width="7" height="5" rx="1.5" />
    <rect x="14" y="12" width="7" height="9" rx="1.5" />
    <rect x="3" y="16" width="7" height="5" rx="1.5" />
  </Base>
)

export const IconCandles = (p: IconProps) => (
  <Base {...p}>
    <path d="M7 3v4M7 17v4M7 7h0M17 2v6M17 14v6" />
    <rect x="4.5" y="7" width="5" height="10" rx="1" />
    <rect x="14.5" y="8" width="5" height="6" rx="1" />
  </Base>
)

export const IconFlask = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 3h6M10 3v5.2L4.7 17a2.5 2.5 0 0 0 2.2 3.7h10.2a2.5 2.5 0 0 0 2.2-3.7L14 8.2V3" />
    <path d="M7.3 14h9.4" />
  </Base>
)

export const IconPlay = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M10 8.8v6.4l5-3.2z" />
  </Base>
)

export const IconLayers = (p: IconProps) => (
  <Base {...p}>
    <path d="m12 3 9 5-9 5-9-5 9-5Z" />
    <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
  </Base>
)

export const IconShield = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3 5 6v5c0 4.6 3 8.4 7 10 4-1.6 7-5.4 7-10V6l-7-3Z" />
    <path d="m9 12 2 2 4-4" />
  </Base>
)

export const IconSettings = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.56V3a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 15 4.6a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.1.38.36.7.72.9.27.15.57.23.88.23H21a2 2 0 1 1 0 4h-.09c-.31 0-.61.08-.88.23-.36.2-.62.52-.72.9Z" />
  </Base>
)

export const IconRefresh = (p: IconProps) => (
  <Base {...p}>
    <path d="M20 11a8 8 0 1 0-.6 4" />
    <path d="M20 4v7h-7" />
  </Base>
)

export const IconPlus = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 5v14M5 12h14" />
  </Base>
)

export const IconTrash = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6" />
  </Base>
)

export const IconStop = (p: IconProps) => (
  <Base {...p}>
    <rect x="6" y="6" width="12" height="12" rx="2" />
  </Base>
)

export const IconLogout = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
  </Base>
)

export const IconCheck = (p: IconProps) => (
  <Base {...p}>
    <path d="m5 12 5 5L20 6" />
  </Base>
)

export const IconAlert = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3 2.5 20h19L12 3Z" />
    <path d="M12 9v5M12 17.5h.01" />
  </Base>
)

export const IconLock = (p: IconProps) => (
  <Base {...p}>
    <rect x="4" y="10" width="16" height="11" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 8 0v3" />
  </Base>
)

export const IconKey = (p: IconProps) => (
  <Base {...p}>
    <circle cx="8" cy="15" r="4" />
    <path d="m11 12 9-9M17 6l3 3M14 9l3 3" />
  </Base>
)

export const IconDatabase = (p: IconProps) => (
  <Base {...p}>
    <ellipse cx="12" cy="5.5" rx="8" ry="3" />
    <path d="M4 5.5v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6M4 11.5v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
  </Base>
)

export const IconSearch = (p: IconProps) => (
  <Base {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.2-3.2" />
  </Base>
)

export const IconDownload = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3v12M7 10l5 5 5-5M4 21h16" />
  </Base>
)

export const IconX = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Base>
)

export const IconWifi = (p: IconProps) => (
  <Base {...p}>
    <path d="M2 8.5a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0M8.5 15.5a6 6 0 0 1 7 0" />
    <path d="M12 19h.01" />
  </Base>
)

export const IconNews = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 4h11l3 3v13H5z" />
    <path d="M8 9h8M8 13h8M8 17h5" />
  </Base>
)

export const IconMessage = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 5h16v11H8l-4 4z" />
    <path d="M8 9h8M8 12h5" />
  </Base>
)

export const IconGateway = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 8h16M4 16h16" />
    <circle cx="9" cy="8" r="2" />
    <circle cx="15" cy="16" r="2" />
  </Base>
)

export const IconTarget = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8" />
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
  </Base>
)
