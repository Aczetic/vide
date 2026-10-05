/** Typed access to the API. Everything is server-side fetched. */

const BASE = process.env.API_BASE ?? 'http://127.0.0.1:8000'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${path} → ${res.status}`)
  return res.json() as Promise<T>
}

export type ProjectTile = {
  id: string
  name: string
  code: string
  client_name: string | null
  format: string
  status: string
  episodes: number
  scenes: number
  cover_image_url: string | null
  progress: Record<string, { planned: number; approved: number; total: number }>
  awaiting_decision: number
  last_activity: string | null
}

export type ProjectDetail = {
  id: string
  name: string
  code: string
  client_name: string | null
  format: string
  aspect_delivery: string
  episodes_target: number | null
  runtime_target_s: number | null
  era: string | null
  model_tier: string
  candidates_per_asset: number
  attempt_budget: number
  status: string
  sources: { id: string; kind: string; filename: string; chars: number }[]
  counts: Record<string, number>
}

export type SceneSummary = {
  id: string
  number: number
  number_label: string
  heading: string | null
  int_ext: string | null
  time_of_day: string
  story_day: string | null
  location_raw: string | null
  sub_area_raw: string | null
  intercut_group: string | null
  character_count: number
  dialogue_count: number
  gap_count: number
}

export type EpisodeSummary = {
  id: string
  number: number
  label: string | null
  logline: string | null
  scenes: SceneSummary[]
}

export type SceneDetail = {
  id: string
  episode_number: number
  number_label: string
  heading: string | null
  int_ext: string | null
  time_of_day: string
  story_day: string | null
  story_day_hint: string | null
  location_raw: string | null
  sub_area_raw: string | null
  intercut_group: string | null
  action_text: string | null
  summary: string | null
  emotional_beat: string | null
  transition_out: string | null
  source_span: Record<string, unknown>
  characters: { id: string; name: string; tier: string | null; speaks: boolean }[]
  dialogue: {
    order: number
    speaker: string
    line: string
    delivery_action: string | null
    delivery_emotion: string | null
  }[]
  breakdown: Record<string, any>
  gaps: string[]
}

export type QuestionOut = {
  id: string
  entity_type: string
  question: string
  candidates: string[]
  recommendation: string | null
  resolved: boolean
  decision: string | null
}

export type GapOut = {
  scene_id: string | null
  scene_label: string
  episode: number
  text: string
  kind: string
}

export const api = {
  projects: () => get<ProjectTile[]>('/projects'),
  project: (id: string) => get<ProjectDetail>(`/projects/${id}`),
  episodes: (id: string) => get<EpisodeSummary[]>(`/projects/${id}/episodes`),
  scene: (id: string) => get<SceneDetail>(`/scenes/${id}`),
  questions: (id: string) => get<QuestionOut[]>(`/projects/${id}/questions`),
  gaps: (id: string) => get<GapOut[]>(`/projects/${id}/gaps`),
}

export type CandidateOut = {
  generation_id: string
  url: string
  width: number | null
  height: number | null
  status: string
  verdict: string | null
  reason: string | null
  category: string | null
  chosen: boolean
}

export type AssetOut = {
  id: string
  entity_type: string
  entity_name: string
  variant_label: string | null
  kind: string
  tag: string
  status: string
  gaps: string[]
  scene_count: number
  candidates: CandidateOut[]
  spec: Record<string, any>
  prompt: string | null
  prompt_sections: Record<string, any>
  model: string | null
}

export async function getAssets(projectId: string, entityType: string) {
  return get<AssetOut[]>(`/projects/${projectId}/assets?entity_type=${entityType}`)
}

export async function approveCandidate(assetId: string, generationId: string) {
  const res = await fetch('/api/gates/decisions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      gate: 'G2',
      target_type: 'asset',
      target_id: assetId,
      chosen_generation_id: generationId,
      decision: 'approved',
    }),
  })
  if (!res.ok) throw new Error(`approve failed: ${res.status}`)
  return res.json()
}

export async function overrideReview(generationId: string) {
  const res = await fetch(`/api/reviews/${generationId}/override`, { method: 'POST' })
  if (!res.ok) throw new Error(`override failed: ${res.status}`)
  return res.json()
}

export type ShotOut = {
  id: string
  letter: string
  order: number
  duration_s: number | null
  complexity: string | null
  is_master_wide: boolean
  card: Record<string, any>
  status: string
  keyframes: CandidateOut[]
}

export type SceneShotsOut = {
  scene_id: string
  episode: number
  scene_label: string
  heading: string | null
  spatial_map: string | null
  shots: ShotOut[]
}

export async function getShots(projectId: string) {
  return get<SceneShotsOut[]>(`/projects/${projectId}/shots`)
}

export type QueueItem = {
  id: string
  project_id: string
  project_name: string
  kind: string
  gate: string
  title: string
  subtitle: string | null
  candidates: number
  thumbnail: string | null
  waiting_since: string | null
}

export type ReportsOut = {
  total_generations: number
  by_status: Record<string, number>
  by_agent: Record<string, { calls: number; cost: number; tokens: number }>
  first_pass_rate: number | null
  attempts_to_approval: number | null
  cost_total: number
  cost_by_target_type: Record<string, number>
  cost_per_approved_element: number | null
  top_failure_categories: { category: string; count: number }[]
  tokens_in: number
  tokens_out: number
  unpriced_generations: number
}

export type SkillOut = {
  id: string
  name: string
  version: number
  kind: string | null
  source: string
  description: string | null
  active: boolean
  chars: number
  content_hash: string
}

export const getQueue = () => get<QueueItem[]>('/queue')
export const getReports = () => get<ReportsOut>('/reports')
export const getSkills = () => get<SkillOut[]>('/skills')
