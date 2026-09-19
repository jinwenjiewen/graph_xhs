export type WorkflowStatus =
  | 'planning_topics'
  | 'awaiting_topic_selection'
  | 'topic_selected'
  | 'writing_draft'
  | 'awaiting_review'
  | 'review_submitted'
  | 'review_approved'
  | 'rewriting_draft'
  | 'generating_images'
  | 'completed'
  | string

export interface AgentState {
  topic_direction: string
  generated_topics?: string[]
  selected_topic?: string
  article_content?: string
  review_feedback?: string
  visual_points?: string[]
  image_urls?: string[]
  status: WorkflowStatus
  review_decision?: 'approved' | 'rejected'
}

export interface WorkflowSnapshot {
  thread_id: string
  status: WorkflowStatus
  next: string[]
  interrupted: boolean
  state: AgentState
  message?: string
  generated_topics?: string[]
}

export interface HistoryEntry extends WorkflowSnapshot {
  checkpoint_id?: string
  created_at: string
  metadata: Record<string, unknown>
}

const apiBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })

  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.detail || `请求失败（${response.status}）`)
  }
  return body as T
}

export function startWorkflow(topicDirection: string) {
  return request<WorkflowSnapshot>('/workflow/start', {
    method: 'POST',
    body: JSON.stringify({ topic_direction: topicDirection }),
  })
}

export function getWorkflowState(threadId: string) {
  return request<WorkflowSnapshot>(`/workflow/state/${threadId}`)
}

export function getWorkflowHistory(threadId: string) {
  return request<{ thread_id: string; history: HistoryEntry[] }>(`/workflow/history/${threadId}`)
}

export function resumeWorkflow(
  threadId: string,
  action: 'select_topic' | 'approve' | 'reject',
  data: Record<string, string> = {},
) {
  return request<WorkflowSnapshot>(`/workflow/resume/${threadId}`, {
    method: 'POST',
    body: JSON.stringify({ action, data }),
  })
}
