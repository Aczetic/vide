import Link from 'next/link'

/**
 * The app shell from the wireframe: sidebar of sections, a header band for
 * global information, a tab bar sitting on the content card, content below.
 *
 * Sections and tabs come from a registry rather than being hardcoded, because
 * requires adding one to be a config change rather than a rewrite.
 */

export type Section = { key: string; label: string; href: string; enabled: boolean }
export type Tab = { key: string; label: string; href: string; count?: number | null }

export const SECTIONS = (projectId?: string): Section[] => [
  { key: 'watchlist', label: 'Watchlist', href: '/watchlist', enabled: true },
  {
    key: 'project',
    label: 'Project',
    href: projectId ? `/projects/${projectId}/plot` : '/watchlist',
    enabled: Boolean(projectId),
  },
  // Settings stays disabled until there are users to manage.
  { key: 'review', label: 'Review Queue', href: '/review', enabled: true },
  { key: 'library', label: 'Library', href: '/library', enabled: true },
  { key: 'reports', label: 'Reports', href: '/reports', enabled: true },
  { key: 'settings', label: 'Settings', href: '#', enabled: false },
]

export const PROJECT_TABS = (id: string): Tab[] => [
  { key: 'plot', label: 'Plot', href: `/projects/${id}/plot` },
  { key: 'story', label: 'Story', href: `/projects/${id}/story` },
  { key: 'characters', label: 'Characters', href: `/projects/${id}/characters` },
  { key: 'scenes', label: 'Scenes', href: `/projects/${id}/scenes` },
  { key: 'shots', label: 'Shots', href: `/projects/${id}/shots` },
]

export function Shell({
  activeSection,
  projectId,
  header,
  tabs,
  activeTab,
  children,
}: {
  activeSection: string
  projectId?: string
  header: React.ReactNode
  tabs?: Tab[]
  activeTab?: string
  children: React.ReactNode
}) {
  const sections = SECTIONS(projectId)

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <nav className="w-52 shrink-0 border-r border-line-soft bg-ink-900 px-3 py-4">
        <div className="px-2 pb-5">
          <span className="text-[13px] font-semibold tracking-tight text-text-hi">vide</span>
          <span className="ml-2 text-[11px] text-text-lo">pre-production</span>
        </div>
        <ul className="space-y-0.5">
          {sections.map((section) => {
            const active = section.key === activeSection
            const base =
              'block rounded-lg px-3 py-[7px] text-[13px] transition-colors'
            if (!section.enabled) {
              return (
                <li key={section.key}>
                  <span className={`${base} cursor-default text-text-lo/60`}>
                    {section.label}
                    <span className="ml-1.5 text-[10px] text-text-lo/50">soon</span>
                  </span>
                </li>
              )
            }
            return (
              <li key={section.key}>
                <Link
                  href={section.href}
                  className={
                    active
                      ? `${base} border border-line bg-ink-700 text-text-hi`
                      : `${base} border border-transparent text-text-mid hover:bg-ink-800 hover:text-text-hi`
                  }
                >
                  {section.label}
                </Link>
              </li>
            )
          })}
        </ul>
      </nav>

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-line-soft px-6">
          {header}
        </header>

        <div className="min-h-0 flex-1 overflow-hidden px-6 pb-6 pt-4">
          <div className="flex h-full flex-col">
            {tabs && (
              <div className="flex shrink-0 overflow-hidden rounded-t-xl border border-b-0 border-line">
                {tabs.map((tab) => {
                  const active = tab.key === activeTab
                  return (
                    <Link
                      key={tab.key}
                      href={tab.href}
                      className={`flex-1 border-r border-line px-4 py-2.5 text-center text-[13px] last:border-r-0 transition-colors ${
                        active
                          ? 'bg-ink-700 font-medium text-text-hi'
                          : 'bg-ink-900 text-text-mid hover:bg-ink-800 hover:text-text-hi'
                      }`}
                    >
                      {tab.label}
                      {tab.count != null && (
                        <span className="ml-1.5 text-[11px] text-text-lo">{tab.count}</span>
                      )}
                    </Link>
                  )
                })}
              </div>
            )}
            <div
              className={`min-h-0 flex-1 overflow-auto border border-line bg-ink-800 ${
                tabs ? 'rounded-b-xl' : 'rounded-xl'
              }`}
            >
              {children}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
