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

export interface NodeMetric {
  node_name: string
  started_at: string
  duration_ms: number
  model_call_count: number
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
}

export interface AgentState {
  topic_direction: string
  generated_topics?: string[]
  selected_topic?: string
  article_content?: string
  final_content?: string
  /** 审核通过前端提交的修改意见；服务端兼容旧的 review_feedback。 */
  human_feedback?: string
  review_feedback?: string
  visual_points?: string[]
  image_prompts?: string[]
  image_urls?: string[]
  status: WorkflowStatus
  review_decision?: 'approved' | 'rejected'
  /** 每次图节点执行后由服务端追加的真实性能与用量记录。 */
  node_metrics?: NodeMetric[]
}

export interface WorkflowSnapshot {
  thread_id: string
  status: WorkflowStatus
  next: string[]
  interrupted: boolean
  awaiting_human_input?: boolean
  state: AgentState
  message?: string
  generated_topics?: string[]
}

export interface HistoryEntry extends WorkflowSnapshot {
  checkpoint_id?: string
  /** 空字符串表示主图；选题子图快照包含其持久化命名空间。 */
  checkpoint_ns?: string
  created_at: string
  metadata: Record<string, unknown>
}

export interface WorkflowThreadSummary {
  thread_id: string
  topic_direction: string
  selected_topic?: string
  status: WorkflowStatus
  next: string[]
  interrupted: boolean
  awaiting_human_input?: boolean
  updated_at: string
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

export function getWorkflowThreads() {
  return request<{ threads: WorkflowThreadSummary[] }>('/workflow/threads')
}

export function deleteWorkflowThread(threadId: string) {
  return request<{ thread_id: string; message: string }>(`/workflow/threads/${threadId}`, {
    method: 'DELETE',
  })
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

export function continueWorkflow(threadId: string) {
  return request<WorkflowSnapshot>(`/workflow/continue/${threadId}`, {
    method: 'POST',
  })
}
