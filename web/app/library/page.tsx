import { Shell } from '@/components/Shell'
import { getSkills, type SkillOut } from '@/lib/api'

export const dynamic = 'force-dynamic'

/**
 * Library — the skill registry.
 *
 * Skills are data, not code: an agent asks for a name and the registry hands
 * back the active version. Showing which version is active, and the hash of its
 * content, is what makes a generation's recorded skill_versions meaningful —
 * without it, "produced by image-prompt v1" names nothing verifiable.
 */
export default async function LibraryPage() {
  let skills: SkillOut[] = []
  let error: string | null = null
  try {
    skills = await getSkills()
  } catch {
    error = 'Could not reach the API.'
  }

  const grouped = skills.reduce<Record<string, SkillOut[]>>((acc, skill) => {
    ;(acc[skill.name] ??= []).push(skill)
    return acc
  }, {})

  return (
    <Shell
      activeSection="library"
      header={
        <>
          <div>
            <h1 className="text-[13px] font-medium text-text-hi">Library</h1>
            <p className="text-[11px] text-text-lo">
              {Object.keys(grouped).length} skills · agents resolve by name to the
              active version
            </p>
          </div>
          <span className="rounded-md border border-line bg-ink-700 px-2 py-1 text-[11px] text-text-lo">
            admin
          </span>
        </>
      }
    >
      <div className="p-6">
        {error && <p className="text-[13px] text-text-mid">{error}</p>}

        <div className="space-y-3">
          {Object.entries(grouped).map(([name, versions]) => {
            const active = versions.find((v) => v.active) ?? versions[0]
            return (
              <section
                key={name}
                className="overflow-hidden rounded-xl border border-line bg-ink-700"
              >
                <div className="flex items-baseline gap-3 border-b border-line-soft px-4 py-2.5">
                  <h2 className="font-mono text-[12px] text-text-hi">{name}</h2>
                  <span className="rounded border border-line bg-ink-600 px-1.5 py-0.5 text-[10px] text-text-mid">
                    v{active.version} active
                  </span>
                  <span className="text-[10px] text-text-lo">{active.kind}</span>
                  <span
                    className={`text-[10px] ${
                      active.source === 'external-reference'
                        ? 'text-accent'
                        : 'text-text-lo'
                    }`}
                  >
                    {active.source}
                  </span>
                  <span className="ml-auto font-mono text-[10px] text-text-lo">
                    {active.content_hash.slice(0, 8)} ·{' '}
                    {(active.chars / 1000).toFixed(1)}k chars
                  </span>
                </div>
                {active.description && (
                  <p className="px-4 py-2.5 text-[11px] leading-relaxed text-text-mid">
                    {active.description}
                  </p>
                )}
                {versions.length > 1 && (
                  <p className="border-t border-line-soft px-4 py-1.5 text-[10px] text-text-lo">
                    {versions.length} versions loaded · pinning a project to one is
                    how an A/B comparison runs
                  </p>
                )}
              </section>
            )
          })}
        </div>

        <p className="mt-6 text-[11px] leading-relaxed text-text-lo">
          Glossary and rules are declared in the schema and not yet populated —
          the glossary fills from fixes that worked, so it grows from the
          generation log rather than being written up front.
        </p>
      </div>
    </Shell>
  )
}
