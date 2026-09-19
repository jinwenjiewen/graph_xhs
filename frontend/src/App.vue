<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  getWorkflowHistory,
  getWorkflowState,
  resumeWorkflow,
  startWorkflow,
  type HistoryEntry,
  type WorkflowSnapshot,
} from './api/workflow'

type StepState = 'completed' | 'current' | 'pending'

interface FlowStep {
  key: string
  title: string
  description: string
  state: StepState
}

const STORAGE_KEY = 'content-agent-thread-id'
const topicDirection = ref('')
const workflow = ref<WorkflowSnapshot | null>(null)
const history = ref<HistoryEntry[]>([])
const loading = ref(false)
const refreshing = ref(false)
const actionLoading = ref(false)
const errorMessage = ref('')
const notice = ref('')
const feedback = ref('')
const showHistory = ref(true)
let pollTimer: number | undefined

const state = computed(() => workflow.value?.state)
const threadId = computed(() => workflow.value?.thread_id || '')
const generatedTopics = computed(() => state.value?.generated_topics || workflow.value?.generated_topics || [])
const isWaitingTopic = computed(() => workflow.value?.next.includes('human_selection_node') ?? false)
const isWaitingReview = computed(() => workflow.value?.next.includes('human_review_node') ?? false)
const isCompleted = computed(() => workflow.value?.status === 'completed')
const statusLabel = computed(() => {
  const labels: Record<string, string> = {
    awaiting_topic_selection: '等待选择选题',
    topic_selected: '已确认选题',
    writing_draft: '正在撰写草稿',
    awaiting_review: '等待人工审核',
    review_submitted: '已提交审核意见',
    review_approved: '审核已通过',
    rewriting_draft: '正在按意见重写',
    generating_images: '正在生成配图',
    completed: '工作流已完成',
  }
  return labels[workflow.value?.status || ''] || '准备开始'
})

const flowSteps = computed<FlowStep[]>(() => {
  const current = workflow.value
  const agentState = current?.state
  const hasTopics = Boolean(agentState?.generated_topics?.length)
  const hasSelected = Boolean(agentState?.selected_topic)
  const hasDraft = Boolean(agentState?.article_content)
  const hasDecision = Boolean(agentState?.review_decision)
  const hasVisuals = Boolean(agentState?.visual_points?.length)
  const hasImages = Boolean(agentState?.image_urls?.length)
  const currentNode = current?.next[0]

  return [
    { key: 'topics', title: '生成选题', description: '规划 Agent', state: hasTopics ? 'completed' : current ? 'current' : 'pending' },
    { key: 'selection', title: '确认选题', description: '人工决策', state: hasSelected ? 'completed' : currentNode === 'human_selection_node' ? 'current' : 'pending' },
    { key: 'draft', title: '撰写草稿', description: '写作 Agent', state: hasDraft ? 'completed' : hasSelected ? 'current' : 'pending' },
    { key: 'review', title: '审核内容', description: '人工决策', state: hasDecision && !isWaitingReview.value ? 'completed' : currentNode === 'human_review_node' ? 'current' : 'pending' },
    { key: 'visuals', title: '提炼配图', description: '视觉 Agent', state: hasVisuals ? 'completed' : hasDecision && agentState?.review_decision === 'approved' ? 'current' : 'pending' },
    { key: 'images', title: '生成配图', description: '图像 Agent', state: hasImages || isCompleted.value ? 'completed' : hasVisuals ? 'current' : 'pending' },
  ]
})

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
    generating_images: '正在提炼视觉要点并生成配图',
    completed: '文章与配图已生成，工作流完成',
  }
  return map[item.status] || `状态更新为 ${item.status}`
}

function applySnapshot(snapshot: WorkflowSnapshot) {
  workflow.value = snapshot
  if (snapshot.thread_id) localStorage.setItem(STORAGE_KEY, snapshot.thread_id)
  if (snapshot.state.topic_direction) topicDirection.value = snapshot.state.topic_direction
}

async function refresh({ quiet = false }: { quiet?: boolean } = {}) {
  if (!threadId.value || refreshing.value) return
  refreshing.value = true
  if (!quiet) errorMessage.value = ''
  try {
    const [latest, historyData] = await Promise.all([
      getWorkflowState(threadId.value),
      getWorkflowHistory(threadId.value),
    ])
    applySnapshot(latest)
    history.value = historyData.history
  } catch (error) {
    const message = error instanceof Error ? error.message : '读取工作流状态失败'
    if (message.includes('未找到该 thread_id')) {
      localStorage.removeItem(STORAGE_KEY)
      workflow.value = null
      history.value = []
    }
    if (!quiet) errorMessage.value = message
  } finally {
    refreshing.value = false
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

function resetWorkspace() {
  localStorage.removeItem(STORAGE_KEY)
  workflow.value = null
  history.value = []
  topicDirection.value = ''
  feedback.value = ''
  errorMessage.value = ''
  notice.value = ''
}

onMounted(async () => {
  const savedThreadId = localStorage.getItem(STORAGE_KEY)
  if (savedThreadId) {
    workflow.value = { thread_id: savedThreadId, status: 'planning_topics', next: [], interrupted: false, state: { topic_direction: '', status: 'planning_topics' } }
    await refresh({ quiet: true })
  }
  pollTimer = window.setInterval(() => refresh({ quiet: true }), 5000)
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
        <p class="hero-copy">从选题到配图，每个关键节点都清晰可见，并在需要时由你做最终决定。</p>
      </div>
      <div v-if="workflow" class="status-chip" :class="{ done: isCompleted }">
        <span class="pulse"></span>{{ statusLabel }}
      </div>
    </section>

    <section v-if="!workflow" class="start-panel card">
      <div class="section-heading">
        <div>
          <p class="eyebrow">01 · 创建任务</p>
          <h2>你想写什么内容？</h2>
        </div>
        <span class="subtle">规划 Agent 会给出多个可选选题</span>
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
        </div>
        <div class="flow-track" aria-label="Agent 流程状态">
          <template v-for="(step, index) in flowSteps" :key="step.key">
            <div class="flow-step" :class="step.state">
              <span class="step-icon">{{ step.state === 'completed' ? '✓' : index + 1 }}</span>
              <div>
                <strong>{{ step.title }}</strong>
                <small>{{ step.description }}</small>
              </div>
            </div>
            <div v-if="index < flowSteps.length - 1" class="flow-line" :class="{ filled: step.state === 'completed' }"></div>
          </template>
        </div>
      </section>

      <div class="workspace-grid">
        <section class="workspace-main">
          <article v-if="isWaitingTopic" class="action-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">需要你的决策</p>
                <h2>选择一个内容选题</h2>
              </div>
              <span class="waiting-label">已暂停</span>
            </div>
            <p class="card-copy">规划 Agent 已完成分析。选定方向后，写作 Agent 会继续生成文章草稿。</p>
            <div class="topic-list">
              <button v-for="topic in generatedTopics" :key="topic" class="topic-option" type="button" :disabled="actionLoading" @click="takeAction('select_topic', topic)">
                <span class="topic-number">{{ generatedTopics.indexOf(topic) + 1 }}</span>
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
              <span v-if="isWaitingReview" class="waiting-label">待审核</span>
              <span v-else class="neutral-label">{{ isCompleted ? '已完成' : '处理中' }}</span>
            </div>
            <div class="draft-content">{{ state.article_content }}</div>
          </article>

          <article v-if="isWaitingReview" class="review-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">需要你的决策</p>
                <h2>审核这篇内容</h2>
              </div>
              <span class="waiting-label">已暂停</span>
            </div>
            <p class="card-copy">通过后将自动提炼视觉要点并生成配图；驳回时会将你的意见交给写作 Agent 重写。</p>
            <label class="feedback-label" for="feedback">修改意见 <em>驳回时必填</em></label>
            <textarea id="feedback" v-model="feedback" maxlength="1000" placeholder="例如：语气更轻松一些，并补充一个具体案例" :disabled="actionLoading"></textarea>
            <div class="review-actions">
              <button class="secondary-button" type="button" :disabled="actionLoading" @click="takeAction('reject')">{{ actionLoading ? '正在处理…' : '驳回并重写' }}</button>
              <button class="primary-button" type="button" :disabled="actionLoading" @click="takeAction('approve')">{{ actionLoading ? '正在处理…' : '审核通过' }}</button>
            </div>
          </article>

          <article v-if="state?.visual_points?.length" class="visual-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">视觉规划</p>
                <h2>已提炼配图要点</h2>
              </div>
            </div>
            <div class="tag-list"><span v-for="point in state.visual_points" :key="point" class="tag">{{ point }}</span></div>
          </article>

          <article v-if="state?.image_urls?.length" class="images-card card">
            <div class="card-title-row">
              <div>
                <p class="eyebrow">生成结果</p>
                <h2>文章配图</h2>
              </div>
              <span class="complete-label">已完成</span>
            </div>
            <div class="image-grid">
              <a v-for="(url, index) in state.image_urls" :key="url" :href="url" target="_blank" rel="noreferrer" :aria-label="`查看配图 ${index + 1}`">
                <img :src="url" :alt="`文章配图 ${index + 1}`" />
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

    <p v-if="notice" class="message success-message" role="status">{{ notice }}</p>
    <p v-if="errorMessage" class="message error-message" role="alert">{{ errorMessage }}</p>
  </main>
</template>
