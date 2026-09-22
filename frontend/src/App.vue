<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  continueWorkflow,
  deleteWorkflowThread,
  getWorkflowHistory,
  getWorkflowState,
  getWorkflowThreads,
  resumeWorkflow,
  startWorkflow,
  type HistoryEntry,
  type NodeMetric,
  type WorkflowSnapshot,
  type WorkflowStatus,
  type WorkflowThreadSummary,
} from './api/workflow'

type StepState = 'completed' | 'current' | 'pending'

interface FlowStep {
  key: string
  title: string
  description: string
  state: StepState
  snapshot: HistoryEntry | null
}

const STORAGE_KEY = 'content-agent-thread-id'
const WORKFLOW_ITEM_COUNT = 5
const topicDirection = ref('')
const workflow = ref<WorkflowSnapshot | null>(null)
const history = ref<HistoryEntry[]>([])
const threads = ref<WorkflowThreadSummary[]>([])
const loading = ref(false)
const refreshing = ref(false)
const actionLoading = ref(false)
const threadsLoading = ref(false)
const switchingThreadId = ref('')
const deletingThreadId = ref('')
const errorMessage = ref('')
const notice = ref('')
const feedback = ref('')
const showHistory = ref(true)
const showThreadPanel = ref(true)
const viewedStepKey = ref<string | null>(null)
const viewedSnapshot = ref<HistoryEntry | null>(null)
let pollTimer: number | undefined
let latestLoadToken = 0

const currentState = computed(() => workflow.value?.state)
const state = computed(() => viewedSnapshot.value?.state ?? currentState.value)
const threadId = computed(() => workflow.value?.thread_id || '')
const generatedTopics = computed(() => currentState.value?.generated_topics || workflow.value?.generated_topics || [])
const visualPoints = computed(() => currentState.value?.visual_points || [])
const imageUrls = computed(() => currentState.value?.image_urls || [])
const viewedGeneratedTopics = computed(() => state.value?.generated_topics || [])
const viewedVisualPoints = computed(() => state.value?.visual_points || [])
const viewedImageUrls = computed(() => state.value?.image_urls || [])
const isWaitingTopic = computed(() => workflow.value?.next.includes('human_selection_node') ?? false)
const isWaitingReview = computed(() => workflow.value?.next.includes('human_review_node') ?? false)
const isCompleted = computed(() => workflow.value?.status === 'completed')
const isViewingStep = computed(() => viewedSnapshot.value !== null)
const canContinue = computed(() => Boolean(
  workflow.value?.next.length && !isWaitingTopic.value && !isWaitingReview.value,
))
const isExpectedCount = (items: unknown[]) => items.length === WORKFLOW_ITEM_COUNT

interface NodeMetricRow extends NodeMetric {
  runNumber: number
}

const nodeMetricRows = computed<NodeMetricRow[]>(() => {
  const runCounts = new Map<string, number>()
  return (state.value?.node_metrics || []).map((metric) => {
    const runNumber = (runCounts.get(metric.node_name) || 0) + 1
    runCounts.set(metric.node_name, runNumber)
    return { ...metric, runNumber }
  })
})

function getNodeLabel(nodeName: string) {
  const labels: Record<string, string> = {
    plan_topics: '生成选题',
    human_selection_node: '确认选题',
    write_draft: '撰写草稿',
    human_review_node: '审核内容',
    extract_visual_points: '生成 Prompt',
    extract_visuals: '生成 Prompt（兼容节点）',
    generate_images: '生成视觉素材',
  }
  return labels[nodeName] || nodeName
}

function formatDuration(durationMs: number) {
  if (durationMs < 1000) return `${durationMs} ms`
  const seconds = durationMs / 1000
  return `${seconds.toFixed(seconds >= 10 ? 1 : 2)} s`
}

function formatTokens(tokens: number | null) {
  return tokens === null ? '未返回' : tokens.toLocaleString('zh-CN')
}

function getStatusLabel(status?: WorkflowStatus) {
  const labels: Record<string, string> = {
    awaiting_topic_selection: '等待选择选题',
    topic_selected: '已确认选题',
    writing_draft: '正在撰写草稿',
    awaiting_review: '等待人工审核',
    review_submitted: '已提交审核意见',
    review_approved: '审核已通过',
    rewriting_draft: '正在按意见重写',
    generating_images: '正在生成视觉素材',
    completed: '工作流已完成',
  }
  return labels[status || ''] || '准备开始'
}

const statusLabel = computed(() => getStatusLabel(workflow.value?.status))

function findStepSnapshot(stepKey: string): HistoryEntry | null {
  switch (stepKey) {
    case 'topics':
      return history.value.find((item) => Boolean(item.state.generated_topics?.length)) || null
    case 'selection':
      return history.value.find((item) => Boolean(item.state.selected_topic)) || null
    case 'draft':
      return history.value.find((item) => Boolean(item.state.article_content)) || null
    case 'review':
      return history.value.find((item) => item.next.includes('human_review_node')) || null
    case 'prompts':
      return history.value.find((item) => Boolean(item.state.visual_points?.length)) || null
    case 'images':
      return history.value.find((item) => Boolean(item.state.image_urls?.length)) || null
    default:
      return null
  }
}

const flowSteps = computed<FlowStep[]>(() => {
  const current = workflow.value
  const agentState = currentState.value
  const hasTopics = isExpectedCount(generatedTopics.value)
  const hasSelected = Boolean(agentState?.selected_topic)
  const hasDraft = Boolean(agentState?.article_content)
  const hasDecision = Boolean(agentState?.review_decision)
  const hasVisuals = isExpectedCount(visualPoints.value)
  const hasImages = isExpectedCount(imageUrls.value)
  const currentNode = current?.next[0]

  return [
    { key: 'topics', title: '生成选题', description: '规划 Agent', state: hasTopics ? 'completed' : current ? 'current' : 'pending', snapshot: findStepSnapshot('topics') },
    { key: 'selection', title: '确认选题', description: '人工决策', state: hasSelected ? 'completed' : currentNode === 'human_selection_node' ? 'current' : 'pending', snapshot: findStepSnapshot('selection') },
    { key: 'draft', title: '撰写草稿', description: '写作 Agent', state: hasDraft ? 'completed' : hasSelected ? 'current' : 'pending', snapshot: findStepSnapshot('draft') },
    { key: 'review', title: '审核内容', description: '人工决策', state: hasDecision && !isWaitingReview.value ? 'completed' : currentNode === 'human_review_node' ? 'current' : 'pending', snapshot: findStepSnapshot('review') },
    { key: 'prompts', title: '生成 Prompt', description: '视觉规划 Agent', state: hasVisuals ? 'completed' : hasDecision && agentState?.review_decision === 'approved' ? 'current' : 'pending', snapshot: findStepSnapshot('prompts') },
    { key: 'images', title: '生成视觉素材', description: '素材生成 Agent', state: hasImages ? 'completed' : hasVisuals ? 'current' : 'pending', snapshot: findStepSnapshot('images') },
  ]
})

const viewedStep = computed(() => flowSteps.value.find((step) => step.key === viewedStepKey.value) || null)

const timeline = computed(() => {
  const seen = new Set<string>()
  return history.value.filter((item) => {
    const key = `${item.status}-${item.next.join(',')}-${item.state.review_decision || ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})

function displayTime(value: string) {
  if (!value) return '刚刚'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function timelineText(item: HistoryEntry) {
  const map: Record<string, string> = {
    planning_topics: '开始规划内容方向',
    awaiting_topic_selection: '已生成候选选题，等待人工选择',
    topic_selected: '已确认选题',
    writing_draft: '写作 Agent 开始撰写',
    awaiting_review: '草稿已生成，等待人工审核',
    review_submitted: '已提交审核决定',
    review_approved: '审核通过，进入配图阶段',
    rewriting_draft: '根据审核意见重写草稿',
    generating_images: 'Prompt 已生成，正在生成视觉素材',
    completed: '文章与视觉素材已生成，工作流完成',
  }
  return map[item.status] || `状态更新为 ${item.status}`
}

function imageLabel(index: number) {
  return `视觉素材 ${index + 1}`
}

function clearStepPreview() {
  viewedStepKey.value = null
  viewedSnapshot.value = null
}

async function viewStep(step: FlowStep) {
  if (!step.snapshot) {
    notice.value = `「${step.title}」暂时没有可查看的历史记录。`
    return
  }
  viewedStepKey.value = step.key
  viewedSnapshot.value = step.snapshot
  showHistory.value = true
  await nextTick()
  document.getElementById('workflow-snapshot')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function applySnapshot(snapshot: WorkflowSnapshot) {
  workflow.value = snapshot
  if (snapshot.thread_id) localStorage.setItem(STORAGE_KEY, snapshot.thread_id)
  if (snapshot.state.topic_direction) topicDirection.value = snapshot.state.topic_direction
}

async function loadThreads({ quiet = false }: { quiet?: boolean } = {}) {
  if (threadsLoading.value) return
  threadsLoading.value = true
  if (!quiet) errorMessage.value = ''
  try {
    const data = await getWorkflowThreads()
    threads.value = data.threads
  } catch (error) {
    if (!quiet) errorMessage.value = error instanceof Error ? error.message : '读取历史会话失败'
  } finally {
    threadsLoading.value = false
  }
}

async function loadWorkflow(threadIdToLoad: string, { quiet = false, clearMissing = false }: { quiet?: boolean; clearMissing?: boolean } = {}) {
  const loadToken = ++latestLoadToken
  if (!quiet) errorMessage.value = ''
  try {
    const [latest, historyData] = await Promise.all([
      getWorkflowState(threadIdToLoad),
      getWorkflowHistory(threadIdToLoad),
    ])
    if (loadToken !== latestLoadToken) return false
    const isThreadChanged = workflow.value?.thread_id !== latest.thread_id
    applySnapshot(latest)
    history.value = historyData.history
    if (isThreadChanged) clearStepPreview()
    else if (viewedSnapshot.value?.checkpoint_id) {
      viewedSnapshot.value = historyData.history.find(
        (item) => item.checkpoint_id === viewedSnapshot.value?.checkpoint_id,
      ) || null
    }
    return true
  } catch (error) {
    if (loadToken !== latestLoadToken) return false
    const message = error instanceof Error ? error.message : '读取工作流状态失败'
    if (clearMissing && message.includes('未找到该 thread_id')) resetWorkspace()
    if (!quiet) errorMessage.value = message
    return false
  }
}

async function refresh({ quiet = false }: { quiet?: boolean } = {}) {
  // 切换历史会话时，不能让定时轮询用旧 thread_id 覆盖正在加载的新会话。
  if (!threadId.value || refreshing.value || switchingThreadId.value) return
  refreshing.value = true
  try {
    await loadWorkflow(threadId.value, { quiet, clearMissing: true })
    await loadThreads({ quiet: true })
  } finally {
    refreshing.value = false
  }
}

async function switchThread(threadIdToLoad: string) {
  if (!threadIdToLoad || threadIdToLoad === threadId.value) {
    return
  }
  switchingThreadId.value = threadIdToLoad
  notice.value = ''
  const switched = await loadWorkflow(threadIdToLoad)
  if (switched) {
    feedback.value = ''
    notice.value = '已切换到历史会话，可继续处理该工作流。'
  }
  switchingThreadId.value = ''
}

async function deleteThread(threadIdToDelete: string) {
  const thread = threads.value.find((item) => item.thread_id === threadIdToDelete)
  const threadTitle = thread?.selected_topic || thread?.topic_direction || threadIdToDelete.slice(0, 8)
  if (!window.confirm(`确认删除「${threadTitle}」吗？该会话的全部历史记录将无法恢复。`)) return

  deletingThreadId.value = threadIdToDelete
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await deleteWorkflowThread(threadIdToDelete)
    const isCurrentThread = threadId.value === threadIdToDelete
    threads.value = threads.value.filter((item) => item.thread_id !== threadIdToDelete)
    if (isCurrentThread) {
      resetWorkspace()
    }
    notice.value = response.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '删除历史会话失败'
  } finally {
    deletingThreadId.value = ''
  }
}

async function createWorkflow() {
  const direction = topicDirection.value.trim()
  if (!direction) {
    errorMessage.value = '请先输入希望创作的内容方向。'
    return
  }
  loading.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const snapshot = await startWorkflow(direction)
    clearStepPreview()
    applySnapshot(snapshot)
    notice.value = snapshot.message || '工作流已创建'
    await refresh({ quiet: true })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '启动工作流失败'
  } finally {
    loading.value = false
  }
}

async function takeAction(action: 'select_topic' | 'approve' | 'reject', value?: string) {
  if (!threadId.value) return
  if (action === 'reject' && !feedback.value.trim()) {
    errorMessage.value = '请填写需要修改的具体意见。'
    return
  }
  actionLoading.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const data: Record<string, string> = {}
    if (action === 'select_topic') data.selected_topic = value || ''
    if (action === 'reject') data.review_feedback = feedback.value.trim()
    const snapshot = await resumeWorkflow(threadId.value, action, data)
    clearStepPreview()
    applySnapshot(snapshot)
    notice.value = snapshot.message || '工作流已更新'
    if (action !== 'select_topic') feedback.value = ''
    await refresh({ quiet: true })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交操作失败'
  } finally {
    actionLoading.value = false
  }
}

async function continueFromCheckpoint() {
  if (!threadId.value || !canContinue.value) return
  actionLoading.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const snapshot = await continueWorkflow(threadId.value)
    clearStepPreview()
    applySnapshot(snapshot)
    notice.value = snapshot.message || '工作流已继续执行'
    await refresh({ quiet: true })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '继续执行工作流失败'
  } finally {
    actionLoading.value = false
  }
}

function resetWorkspace() {
  latestLoadToken += 1
  clearStepPreview()
  localStorage.removeItem(STORAGE_KEY)
  workflow.value = null
  history.value = []
  topicDirection.value = ''
  feedback.value = ''
  errorMessage.value = ''
  notice.value = ''
}

onMounted(async () => {
  await loadThreads({ quiet: true })
  const savedThreadId = localStorage.getItem(STORAGE_KEY)
  if (savedThreadId) {
    await loadWorkflow(savedThreadId, { quiet: true, clearMissing: true })
  }
  pollTimer = window.setInterval(() => {
    refresh({ quiet: true })
    loadThreads({ quiet: true })
  }, 5000)
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <main class="page-shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">A</span>
        <div>
          <strong>内容 Agent</strong>
          <span>工作流控制台</span>
        </div>
      </div>
      <div class="topbar-actions">
        <span v-if="workflow" class="thread-id" :title="threadId">会话 {{ threadId.slice(0, 8) }}</span>
        <button v-if="workflow" class="text-button" type="button" @click="resetWorkspace">新建会话</button>
      </div>
    </header>

    <section class="hero">
      <div>
        <p class="eyebrow">AI CONTENT PIPELINE</p>
        <h1>把内容创作，交给协作中的 Agent。</h1>
        <p class="hero-copy">从选题到视觉素材，每个关键节点都清晰可见，并在需要时由你做最终决定。</p>
      </div>
      <div v-if="workflow" class="status-chip" :class="{ done: isCompleted }">
        <span class="pulse"></span>{{ statusLabel }}
      </div>
    </section>

    <div class="app-workspace" :class="{ 'sessions-collapsed': !showThreadPanel }">
      <aside class="thread-panel card" :class="{ collapsed: !showThreadPanel }">
        <button
          class="history-heading"
          type="button"
          :aria-label="showThreadPanel ? '收起历史会话' : '展开历史会话'"
          :aria-expanded="showThreadPanel"
          @click="showThreadPanel = !showThreadPanel"
        >
          <span><span class="eyebrow">SESSIONS</span><strong>历史会话</strong></span>
          <span class="chevron" :class="{ open: showThreadPanel }">⌄</span>
        </button>
        <div v-if="showThreadPanel" class="thread-panel-content">
          <div class="thread-panel-toolbar">
            <span>{{ threads.length }} 条会话</span>
            <button class="refresh-button" type="button" :disabled="threadsLoading" @click="loadThreads()">刷新</button>
          </div>
          <p v-if="threadsLoading" class="thread-picker-empty">正在读取会话…</p>
          <div v-else-if="threads.length" class="thread-options">
            <div v-for="item in threads" :key="item.thread_id" class="thread-option">
              <button
                class="thread-select"
                :class="{ active: item.thread_id === threadId }"
                type="button"
                :disabled="Boolean(switchingThreadId) || Boolean(deletingThreadId) || actionLoading"
                @click="switchThread(item.thread_id)"
              >
                <span>
                  <strong>{{ item.selected_topic || item.topic_direction || '未命名内容任务' }}</strong>
                  <small>{{ item.thread_id.slice(0, 8) }} · {{ displayTime(item.updated_at) }}</small>
                </span>
                <em>{{ switchingThreadId === item.thread_id ? '加载中…' : getStatusLabel(item.status) }}</em>
              </button>
              <button
                class="thread-delete-button"
                type="button"
                :disabled="Boolean(switchingThreadId) || Boolean(deletingThreadId) || actionLoading"
                :aria-label="`删除历史会话 ${item.thread_id}`"
                @click="deleteThread(item.thread_id)"
              >
                {{ deletingThreadId === item.thread_id ? '删除中…' : '删除' }}
              </button>
            </div>
          </div>
          <p v-else class="thread-picker-empty">还没有可恢复的历史会话。</p>
        </div>
      </aside>

      <div class="app-workspace-main">
    <section v-if="!workflow" class="start-panel card">
      <div class="section-heading">
        <div>
          <p class="eyebrow">01 · 创建任务</p>
          <h2>你想写什么内容？</h2>
        </div>
        <span class="subtle">规划 Agent 会给出 {{ WORKFLOW_ITEM_COUNT }} 个可选选题</span>
      </div>
      <form class="task-form" @submit.prevent="createWorkflow">
        <label for="topic-direction">内容方向</label>
        <div class="input-row">
          <input id="topic-direction" v-model="topicDirection" maxlength="500" placeholder="例如：面向创业者的 AI 效率工具分享" :disabled="loading" />
          <button class="primary-button" type="submit" :disabled="loading">
            {{ loading ? '正在规划…' : '开始生成' }}
          </button>
        </div>
      </form>
      <p class="hint">流程会在「确认选题」与「审核内容」两个节点暂停，等待你的操作。</p>
    </section>

    <template v-else>
      <section class="flow-card card">
        <div class="section-heading">
          <div>
            <p class="eyebrow">AGENT FLOW</p>
            <h2>流程流转状态</h2>
          </div>
          <button class="refresh-button" type="button" :disabled="refreshing" @click="refresh()">
            <span class="refresh-icon" :class="{ spinning: refreshing }">↻</span> 刷新状态
          </button>
          <button v-if="canContinue" class="refresh-button" type="button" :disabled="actionLoading" @click="continueFromCheckpoint()">
            {{ actionLoading ? '正在继续…' : '继续执行' }}
          </button>
        </div>
        <div class="flow-track" aria-label="Agent 流程状态">
          <template v-for="(step, index) in flowSteps" :key="step.key">
            <button
              class="flow-step"
              :class="[step.state, { viewing: viewedStepKey === step.key }]"
              type="button"
              :aria-label="`查看${step.title}的历史记录`"
              :aria-pressed="viewedStepKey === step.key"
              @click="viewStep(step)"
            >
              <span class="step-icon">{{ step.state === 'completed' ? '✓' : index + 1 }}</span>
              <div>
                <strong>{{ step.title }}</strong>
                <small>{{ step.description }}</small>
              </div>
            </button>
            <div v-if="index < flowSteps.length - 1" class="flow-line" :class="{ filled: step.state === 'completed' }"></div>
          </template>
        </div>
      </section>

      <section class="metrics-card card" aria-labelledby="node-metrics-heading">
        <div class="section-heading">
          <div>
            <p class="eyebrow">NODE METRICS</p>
            <h2 id="node-metrics-heading">节点耗时与 Token</h2>
          </div>
          <span class="neutral-label">{{ nodeMetricRows.length }} 次执行</span>
        </div>
        <div v-if="nodeMetricRows.length" class="metrics-table-wrap">
          <table class="metrics-table">
            <thead>
              <tr>
                <th scope="col">节点</th>
                <th scope="col">耗时</th>
                <th scope="col">Token 用量</th>
                <th scope="col">模型调用</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="metric in nodeMetricRows" :key="`${metric.node_name}-${metric.started_at}-${metric.runNumber}`">
                <td>
                  <strong>{{ getNodeLabel(metric.node_name) }}</strong>
                  <small>第 {{ metric.runNumber }} 次 · {{ displayTime(metric.started_at) }}</small>
                </td>
                <td><strong class="metric-duration">{{ formatDuration(metric.duration_ms) }}</strong></td>
                <td>
                  <div class="token-values">
                    <span>输入 {{ formatTokens(metric.input_tokens) }}</span>
                    <span>输出 {{ formatTokens(metric.output_tokens) }}</span>
                    <strong>合计 {{ formatTokens(metric.total_tokens) }}</strong>
                  </div>
                </td>
                <td>{{ metric.model_call_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="metrics-empty">暂无节点指标。历史会话会在升级后再次执行节点时开始记录。</p>
        <p v-if="nodeMetricRows.some((metric) => metric.total_tokens === null)" class="metrics-note">
          “未返回”表示上游模型或图像服务没有提供精确 usage，不会以估算值替代。
        </p>
      </section>

      <div class="workspace-grid">
        <section class="workspace-main">
          <article v-if="viewedSnapshot" id="workflow-snapshot" class="snapshot-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">历史步骤快照</p>
                <h2>{{ viewedStep?.title }}</h2>
              </div>
              <button class="text-button" type="button" @click="clearStepPreview">返回当前状态</button>
            </div>
            <p class="card-copy">这是 {{ displayTime(viewedSnapshot.created_at) }} 保存的状态；查看历史不会改变当前工作流或其续跑位置。</p>
            <dl class="snapshot-meta">
              <div>
                <dt>执行状态</dt>
                <dd>{{ getStatusLabel(viewedSnapshot.status) }}</dd>
              </div>
              <div v-if="state?.topic_direction">
                <dt>内容方向</dt>
                <dd>{{ state.topic_direction }}</dd>
              </div>
              <div v-if="state?.selected_topic">
                <dt>已选选题</dt>
                <dd>{{ state.selected_topic }}</dd>
              </div>
              <div v-if="state?.review_decision">
                <dt>审核决定</dt>
                <dd>{{ state.review_decision === 'approved' ? '已通过' : '已驳回' }}</dd>
              </div>
              <div v-if="state?.review_feedback">
                <dt>审核意见</dt>
                <dd>{{ state.review_feedback }}</dd>
              </div>
            </dl>
          </article>

          <article v-if="isViewingStep && viewedGeneratedTopics.length" class="snapshot-topics-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">历史候选选题</p>
                <h2>该步骤生成的选题</h2>
              </div>
              <span class="neutral-label">{{ viewedGeneratedTopics.length }} 条</span>
            </div>
            <ol class="snapshot-topic-list">
              <li v-for="(topic, index) in viewedGeneratedTopics" :key="`${index}-${topic}`">
                <span>{{ index + 1 }}</span>{{ topic }}
              </li>
            </ol>
          </article>

          <article v-if="!isViewingStep && isWaitingTopic" class="action-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">需要你的决策</p>
                <h2>选择一个内容选题</h2>
              </div>
              <span class="waiting-label">已暂停</span>
            </div>
            <p class="card-copy">规划 Agent 已完成分析，已生成 {{ generatedTopics.length }} / {{ WORKFLOW_ITEM_COUNT }} 个候选。选定方向后，写作 Agent 会继续生成文章草稿。</p>
            <div class="topic-list">
              <button v-for="(topic, index) in generatedTopics" :key="`${index}-${topic}`" class="topic-option" type="button" :disabled="actionLoading" @click="takeAction('select_topic', topic)">
                <span class="topic-number">{{ index + 1 }}</span>
                <span>{{ topic }}</span>
                <span class="arrow">→</span>
              </button>
            </div>
          </article>

          <article v-if="state?.article_content" class="draft-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">文章草稿</p>
                <h2>{{ state.selected_topic }}</h2>
              </div>
              <span v-if="isViewingStep" class="neutral-label">历史快照</span>
              <span v-else-if="isWaitingReview" class="waiting-label">待审核</span>
              <span v-else class="neutral-label">{{ isCompleted ? '已完成' : '处理中' }}</span>
            </div>
            <div class="draft-content">{{ state.article_content }}</div>
          </article>

          <article v-if="!isViewingStep && isWaitingReview" class="review-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">需要你的决策</p>
                <h2>审核这篇内容</h2>
              </div>
              <span class="waiting-label">已暂停</span>
            </div>
            <p class="card-copy">通过后会先生成 5 条视觉 Prompt，再按 Prompt 生成视觉素材；驳回时会将你的意见交给写作 Agent 重写。</p>
            <label class="feedback-label" for="feedback">修改意见 <em>驳回时必填</em></label>
            <textarea id="feedback" v-model="feedback" maxlength="1000" placeholder="例如：语气更轻松一些，并补充一个具体案例" :disabled="actionLoading"></textarea>
            <div class="review-actions">
              <button class="secondary-button" type="button" :disabled="actionLoading" @click="takeAction('reject')">{{ actionLoading ? '正在处理…' : '驳回并重写' }}</button>
              <button class="primary-button" type="button" :disabled="actionLoading" @click="takeAction('approve')">{{ actionLoading ? '正在处理…' : '审核通过' }}</button>
            </div>
          </article>

          <article v-if="viewedVisualPoints.length" class="visual-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">视觉 Prompt</p>
                <h2>已生成视觉素材提示词</h2>
              </div>
              <span class="neutral-label">{{ viewedVisualPoints.length }} / {{ WORKFLOW_ITEM_COUNT }} 条</span>
            </div>
            <ol class="visual-list">
              <li v-for="(point, index) in viewedVisualPoints" :key="`${index}-${point}`">
                <span class="visual-number">{{ index + 1 }}</span>
                <p>{{ point }}</p>
              </li>
            </ol>
          </article>

          <article v-if="viewedImageUrls.length" class="images-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">生成结果</p>
                <h2>文章视觉素材</h2>
              </div>
              <span :class="isExpectedCount(viewedImageUrls) ? 'complete-label' : 'neutral-label'">
                {{ isExpectedCount(viewedImageUrls) ? '已完成' : '已生成' }} {{ viewedImageUrls.length }} / {{ WORKFLOW_ITEM_COUNT }} 张
              </span>
            </div>
            <div class="image-grid">
              <a v-for="(url, index) in viewedImageUrls" :key="`${index}-${url}`" :href="url" target="_blank" rel="noreferrer" :aria-label="`查看${imageLabel(index)}`">
                <img :src="url" :alt="`文章${imageLabel(index)}`" />
                <span>{{ imageLabel(index) }}</span>
              </a>
            </div>
          </article>
        </section>

        <aside class="activity-panel card">
          <button class="history-heading" type="button" @click="showHistory = !showHistory">
            <span><span class="eyebrow">ACTIVITY</span><strong>执行记录</strong></span>
            <span class="chevron" :class="{ open: showHistory }">⌄</span>
          </button>
          <div v-if="showHistory" class="timeline">
            <div v-for="item in timeline" :key="item.checkpoint_id || item.created_at" class="timeline-item" :class="{ current: item.status === workflow?.status }">
              <span class="timeline-dot"></span>
              <div>
                <strong>{{ timelineText(item) }}</strong>
                <small>{{ displayTime(item.created_at) }}</small>
              </div>
            </div>
            <p v-if="!timeline.length" class="empty-history">等待 Agent 产生第一条记录</p>
          </div>
        </aside>
      </div>
    </template>
      </div>
    </div>

    <p v-if="notice" class="message success-message" role="status">{{ notice }}</p>
    <p v-if="errorMessage" class="message error-message" role="alert">{{ errorMessage }}</p>
  </main>
</template>
