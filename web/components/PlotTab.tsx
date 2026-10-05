import { type ProjectDetail } from '@/lib/api'

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-6 border-b border-line-soft py-2 last:border-b-0">
      <span className="text-[12px] text-text-lo">{label}</span>
      <span className="text-[12px] text-text-hi">{value}</span>
    </div>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-ink-700 p-5">
      <h2 className="mb-3 text-[12px] font-medium uppercase tracking-wide text-text-lo">
        {title}
      </h2>
      {children}
    </section>
  )
}

export function PlotTab({ project }: { project: ProjectDetail }) {
  const c = project.counts
  return (
    <div className="grid gap-5 p-6 lg:grid-cols-2">
      <Card title="Source documents">
        {project.sources.length === 0 ? (
          <p className="text-[12px] text-text-lo">Nothing uploaded.</p>
        ) : (
          <ul className="space-y-2">
            {project.sources.map((source) => (
              <li
                key={source.id}
                className="flex items-center justify-between rounded-lg border border-line-soft bg-ink-600 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-[12px] text-text-hi">{source.filename}</p>
                  <p className="text-[11px] text-text-lo">
                    {source.kind} · {source.chars.toLocaleString()} characters
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="What the pipeline found">
        <div className="grid grid-cols-3 gap-3">
          {[
            ['Episodes', c.episodes],
            ['Scenes', c.scenes],
            ['Characters', c.characters],
            ['Sets', c.sub_areas],
            ['Props', c.props],
            ['Builds', c.builds],
          ].map(([label, value]) => (
            <div
              key={label as string}
              className="rounded-lg border border-line-soft bg-ink-600 px-3 py-2.5"
            >
              <p className="text-[18px] font-medium tabular-nums text-text-hi">
                {(value as number) ?? 0}
              </p>
              <p className="text-[11px] text-text-lo">{label as string}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Project settings">
        <Row label="Format" value={project.format.replace('_', '-')} />
        <Row label="Delivery aspect" value={project.aspect_delivery} />
        <Row label="Episodes target" value={project.episodes_target ?? '—'} />
        <Row
          label="Runtime target"
          value={project.runtime_target_s ? `${project.runtime_target_s}s / episode` : '—'}
        />
        <Row label="Era" value={project.era ?? 'not set'} />
        <Row label="Model tier" value={project.model_tier} />
        <Row label="Candidates per asset" value={project.candidates_per_asset} />
        <Row label="Attempt budget" value={project.attempt_budget} />
      </Card>

      <Card title="Pipeline">
        <div className="space-y-2">
          {[
            { stage: 'Ingest', state: 'done', detail: `${c.scenes} scenes parsed` },
            { stage: 'Breakdown', state: 'done', detail: 'per-scene extraction' },
            { stage: 'Entity resolution', state: 'done', detail: `${c.characters} characters` },
            { stage: 'Story-days', state: 'done', detail: 'proposed, awaiting G1' },
            { stage: 'Design order', state: 'done', detail: `${c.builds} builds` },
            { stage: 'Character sheets', state: 'pending', detail: 'a later stage' },
            { stage: 'Location plates', state: 'pending', detail: 'a later stage' },
            { stage: 'Keyframes', state: 'pending', detail: 'a later stage' },
          ].map((step) => (
            <div
              key={step.stage}
              className="flex items-center justify-between rounded-lg border border-line-soft bg-ink-600 px-3 py-2"
            >
              <div className="flex items-center gap-2.5">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    step.state === 'done' ? 'bg-accent' : 'bg-text-lo/40'
                  }`}
                />
                <span
                  className={`text-[12px] ${
                    step.state === 'done' ? 'text-text-hi' : 'text-text-lo'
                  }`}
                >
                  {step.stage}
                </span>
              </div>
              <span className="text-[11px] text-text-lo">{step.detail}</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-text-lo">
          Running the pipeline from here arrives with a later stage — today it is a command
          line step.
        </p>
      </Card>
    </div>
  )
}
