import { Shell } from '@/components/Shell'
import { getReports, type ReportsOut } from '@/lib/api'

export const dynamic = 'force-dynamic'

/**
 * Reports — 's numbers, computed from the generation log.
 *
 * The honesty rule here: where a number cannot be computed, say so rather than
 * showing a zero. Image spend is not reported by the provider, so the total is
 * labelled as LLM-only and the unpriced count is stated. A dashboard that
 * quietly under-reports is worse than one that admits a gap.
 */

function Stat({
  label,
  value,
  hint,
}: {
  label: string
  value: React.ReactNode
  hint?: string
}) {
  return (
    <div className="rounded-xl border border-line bg-ink-700 px-4 py-3">
      <p className="text-[20px] font-medium tabular-nums text-text-hi">{value}</p>
      <p className="text-[11px] text-text-mid">{label}</p>
      {hint && <p className="mt-1 text-[10px] leading-snug text-text-lo">{hint}</p>}
    </div>
  )
}

function Bar({ label, value, max }: { label: string; value: number; max: number }) {
  return (
    <div className="flex items-center gap-3 py-1">
      <span className="w-44 shrink-0 truncate text-[11px] text-text-mid">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-500">
        <div
          className="h-full bg-accent"
          style={{ width: `${max ? (value / max) * 100 : 0}%` }}
        />
      </div>
      <span className="w-20 shrink-0 text-right text-[11px] tabular-nums text-text-lo">
        ${value.toFixed(4)}
      </span>
    </div>
  )
}

export default async function ReportsPage() {
  let data: ReportsOut | null = null
  try {
    data = await getReports()
  } catch {
    /* handled below */
  }

  if (!data) {
    return (
      <Shell
        activeSection="reports"
        header={<h1 className="text-[13px] font-medium text-text-hi">Reports</h1>}
      >
        <p className="p-6 text-[13px] text-text-mid">Could not reach the API.</p>
      </Shell>
    )
  }

  const agents = Object.entries(data.by_agent).sort((a, b) => b[1].cost - a[1].cost)
  const maxCost = Math.max(...agents.map(([, v]) => v.cost), 0.0001)

  return (
    <Shell
      activeSection="reports"
      header={
        <>
          <div>
            <h1 className="text-[13px] font-medium text-text-hi">Reports</h1>
            <p className="text-[11px] text-text-lo">
              from the generation log · {data.total_generations} calls
            </p>
          </div>
          <span className="rounded-md border border-line bg-ink-700 px-2 py-1 text-[11px] text-text-lo">
            admin
          </span>
        </>
      }
    >
      <div className="space-y-6 p-6">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Stat
            label="LLM spend"
            value={`$${data.cost_total.toFixed(2)}`}
            hint={
              data.unpriced_generations
                ? `${data.unpriced_generations} image calls are unpriced — the provider does not report cost, so this is text only`
                : undefined
            }
          />
          <Stat
            label="First-pass approval"
            value={
              data.first_pass_rate === null
                ? '—'
                : `${Math.round(data.first_pass_rate * 100)}%`
            }
            hint="of reviewed elements that passed on attempt 1"
          />
          <Stat
            label="Attempts to approval"
            value={data.attempts_to_approval?.toFixed(1) ?? '—'}
            hint="mean, across assets"
          />
          <Stat
            label="Tokens"
            value={`${((data.tokens_in + data.tokens_out) / 1000).toFixed(0)}k`}
            hint={`${data.tokens_in.toLocaleString()} in / ${data.tokens_out.toLocaleString()} out`}
          />
        </div>

        <section className="rounded-xl border border-line bg-ink-700 p-5">
          <h2 className="mb-3 text-[12px] font-medium uppercase tracking-wide text-text-lo">
            Spend by agent
          </h2>
          {agents.map(([agent, v]) => (
            <Bar key={agent} label={`${agent} (${v.calls})`} value={v.cost} max={maxCost} />
          ))}
        </section>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-xl border border-line bg-ink-700 p-5">
            <h2 className="mb-3 text-[12px] font-medium uppercase tracking-wide text-text-lo">
              Why the agent rejected things
            </h2>
            {data.top_failure_categories.length === 0 ? (
              <p className="text-[11px] text-text-lo">Nothing rejected yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {data.top_failure_categories.map((row) => (
                  <li key={row.category} className="flex justify-between text-[12px]">
                    <span className="text-text-mid">
                      {row.category.replace(/_/g, ' ')}
                    </span>
                    <span className="tabular-nums text-text-hi">{row.count}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-[10px] leading-snug text-text-lo">
              The category decides what gets changed — a prompt patch, a simpler
              shot, or a rebuilt asset.
            </p>
          </section>

          <section className="rounded-xl border border-line bg-ink-700 p-5">
            <h2 className="mb-3 text-[12px] font-medium uppercase tracking-wide text-text-lo">
              Spend by stage
            </h2>
            <ul className="space-y-1.5">
              {Object.entries(data.cost_by_target_type)
                .sort((a, b) => b[1] - a[1])
                .map(([kind, cost]) => (
                  <li key={kind} className="flex justify-between text-[12px]">
                    <span className="text-text-mid">{kind}</span>
                    <span className="tabular-nums text-text-hi">
                      ${cost.toFixed(4)}
                    </span>
                  </li>
                ))}
            </ul>
            <div className="mt-3 flex gap-3 border-t border-line-soft pt-3 text-[11px]">
              {Object.entries(data.by_status).map(([status, n]) => (
                <span key={status} className="text-text-lo">
                  {status}: <span className="text-text-mid">{n}</span>
                </span>
              ))}
            </div>
          </section>
        </div>
      </div>
    </Shell>
  )
}
