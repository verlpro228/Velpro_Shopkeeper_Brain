<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue';
import { Eraser, Loader2, SendHorizontal, Waves } from '@lucide/vue';
import { clearHistory, createQueryStream, fetchHistory, submitQuery } from '../services/api';
import type {
  HistoryItem,
  QueryResponse,
  SseDeltaPayload,
  SseFinalPayload,
  SseProgressPayload,
  StreamSubmitResponse,
  TaskStatus
} from '../types/api';
import { formatClock, renderAnswer, renderMarkdownToHtml } from '../utils/answer';

interface ProgressState {
  status: TaskStatus;
  doneList: string[];
  runningList: string[];
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  time: Date;
  metaText?: string;
  status?: 'idle' | 'pending' | 'streaming' | 'completed' | 'failed';
  progress?: ProgressState;
  progressOpen?: boolean;
  imageUrls?: string[];
}

const SESSION_KEY = 'kb_session_id';
const streamMode = ref(true);
const inputText = ref('');
const sending = ref(false);
const chatBody = ref<HTMLElement | null>(null);
const inputRef = ref<HTMLTextAreaElement | null>(null);
const sessionId = ref(readSessionId());
const messages = ref<ChatMessage[]>([createWelcomeMessage()]);
let activeStream: EventSource | null = null;

function makeId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

function readSessionId(): string {
  const saved = localStorage.getItem(SESSION_KEY);
  if (saved) return saved;
  const next = `sess-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  localStorage.setItem(SESSION_KEY, next);
  return next;
}

function persistSessionId(value: string): void {
  if (!value || value === sessionId.value) return;
  sessionId.value = value;
  localStorage.setItem(SESSION_KEY, value);
}

function createWelcomeMessage(): ChatMessage {
  return {
    id: 'welcome',
    role: 'assistant',
    content: '你好，我是知识库助手。可以继续使用流式输出，也可以切换为一次性返回。',
    time: new Date(),
    metaText: '提示：可以问“如何使用万用表测量电压？”',
    status: 'completed'
  };
}

function scrollToBottom(): void {
  nextTick(() => {
    if (!chatBody.value) return;
    chatBody.value.scrollTo({ top: chatBody.value.scrollHeight, behavior: 'smooth' });
  });
}

function toMessage(item: HistoryItem): ChatMessage {
  return {
    id: item._id || item.id || makeId('history'),
    role: item.role === 'user' ? 'user' : 'assistant',
    content: item.text || '',
    time: item.ts ? new Date(item.ts * 1000) : new Date(),
    status: 'completed',
    imageUrls: item.image_urls || []
  };
}

function historySortTime(item: HistoryItem): number {
  return typeof item.ts === 'number' && Number.isFinite(item.ts) ? item.ts : 0;
}

function historySortId(item: HistoryItem): string {
  return item._id || item.id || '';
}

function historyRoleRank(item: HistoryItem): number {
  if (item.role === 'user') return 0;
  if (item.role === 'assistant') return 1;
  return 2;
}

function sortHistoryItems(items: HistoryItem[]): HistoryItem[] {
  return [...items].sort((a, b) => {
    const byTime = historySortTime(a) - historySortTime(b);
    if (byTime !== 0) return byTime;

    const byId = historySortId(a).localeCompare(historySortId(b));
    if (byId !== 0) return byId;

    return historyRoleRank(a) - historyRoleRank(b);
  });
}

async function loadHistory(): Promise<void> {
  try {
    const history = await fetchHistory(sessionId.value);
    const items = Array.isArray(history.items) ? history.items : [];
    if (!items.length) return;
    messages.value = [createWelcomeMessage(), ...sortHistoryItems(items).map(toMessage)];
    scrollToBottom();
  } catch {
    // The old page also treats history loading as best effort.
  }
}

function isStreamSubmit(data: QueryResponse | StreamSubmitResponse): data is StreamSubmitResponse {
  return typeof (data as StreamSubmitResponse).task_id === 'string';
}

function parseEventPayload<T>(event: MessageEvent): T {
  return JSON.parse(event.data || '{}') as T;
}

function updateProgress(message: ChatMessage, payload: SseProgressPayload): void {
  message.progress = {
    status: payload.status || 'processing',
    doneList: Array.isArray(payload.done_list) ? payload.done_list : [],
    runningList: Array.isArray(payload.running_list) ? payload.running_list : []
  };
}

function forceCompleteProgress(message: ChatMessage): void {
  const done = new Set(message.progress?.doneList || []);
  for (const item of message.progress?.runningList || []) done.add(item);
  message.progress = {
    status: 'completed',
    doneList: Array.from(done),
    runningList: []
  };
}

function progressTitle(progress: ProgressState): string {
  const done = progress.doneList.length;
  const running = progress.runningList.length;
  if (progress.status === 'completed') return `处理完成 · 已完成 ${done}`;
  if (progress.status === 'failed') return '处理失败';
  return `处理中 · 已完成 ${done} · 进行中 ${running}`;
}

function progressLines(progress: ProgressState): string[] {
  const lines = [
    ...progress.doneList.map((item) => `✓ ${item}`),
    ...progress.runningList.map((item) => `… ${item}`)
  ];
  return lines.length ? lines : ['暂无进度'];
}

function answerParts(message: ChatMessage) {
  return renderAnswer(message.content, message.imageUrls || []);
}

function answerText(message: ChatMessage): string {
  const text = answerParts(message).text;
  if (text.trim()) return text;
  if (message.role === 'assistant' && message.status === 'completed') return '（已完成，但未返回答案）';
  return '';
}

function answerHtml(message: ChatMessage): string {
  return renderMarkdownToHtml(answerText(message));
}

function setProgressOpen(message: ChatMessage, event: Event): void {
  message.progressOpen = (event.currentTarget as HTMLDetailsElement).open;
}

function hideFailedImage(event: Event): void {
  (event.currentTarget as HTMLImageElement).style.display = 'none';
}

async function sendMessage(): Promise<void> {
  const text = inputText.value.trim();
  if (!text || sending.value) return;

  inputText.value = '';
  messages.value.push({
    id: makeId('user'),
    role: 'user',
    content: text,
    time: new Date(),
    status: 'completed'
  });

  const assistant: ChatMessage = {
    id: makeId('assistant'),
    role: 'assistant',
    content: '',
    time: new Date(),
    status: 'pending',
    progress: { status: 'pending', doneList: [], runningList: [] },
    progressOpen: true
  };
  messages.value.push(assistant);
  sending.value = true;
  scrollToBottom();

  try {
    const data = await submitQuery({
      query: text,
      session_id: sessionId.value,
      is_stream: streamMode.value
    });
    persistSessionId(data.session_id);

    if (!streamMode.value) {
      const response = data as QueryResponse;
      assistant.status = response.error ? 'failed' : 'completed';
      assistant.content = response.error ? `抱歉，本次处理失败：\n${response.error}` : response.answer || '';
      assistant.imageUrls = response.image_urls || [];
      assistant.progress = { status: assistant.status, doneList: response.done_list || [], runningList: [] };
      assistant.progressOpen = false;
      return;
    }

    if (!isStreamSubmit(data)) throw new Error('后端未返回流式任务 ID');
    await consumeStream(data.task_id, assistant);
  } catch (error) {
    assistant.status = 'failed';
    assistant.progress = { status: 'failed', doneList: assistant.progress?.doneList || [], runningList: [] };
    assistant.progressOpen = false;
    assistant.content = `抱歉，本次处理失败：\n${error instanceof Error ? error.message : String(error)}`;
  } finally {
    sending.value = false;
    scrollToBottom();
    nextTick(() => inputRef.value?.focus());
  }
}

function consumeStream(taskId: string, message: ChatMessage): Promise<void> {
  return new Promise((resolve) => {
    let rawAnswer = '';
    let closed = false;
    let finalFallbackTimer: number | undefined;
    activeStream = createQueryStream(taskId);
    message.status = 'streaming';

    const close = (status: ChatMessage['status']) => {
      if (closed) return;
      closed = true;
      if (finalFallbackTimer !== undefined) window.clearTimeout(finalFallbackTimer);
      message.status = status;
      activeStream?.close();
      activeStream = null;
      scrollToBottom();
      resolve();
    };

    activeStream.addEventListener('progress', (event) => {
      try {
        const payload = parseEventPayload<SseProgressPayload>(event);
        updateProgress(message, payload);
        if (payload.status === 'completed') {
          forceCompleteProgress(message);
          finalFallbackTimer =
            finalFallbackTimer ??
            window.setTimeout(() => {
              if (closed) return;
              message.content = message.content || rawAnswer;
              message.progressOpen = false;
              close('completed');
            }, 3000);
        }
      } catch {
        return;
      }
    });

    activeStream.addEventListener('delta', (event) => {
      try {
        const payload = parseEventPayload<SseDeltaPayload>(event);
        if (!payload.delta) return;
        rawAnswer += payload.delta;
        message.content = rawAnswer;
        scrollToBottom();
      } catch {
        return;
      }
    });

    const handleFinal = (event: MessageEvent) => {
      try {
        const payload = parseEventPayload<SseFinalPayload>(event);
        const finalAnswer = payload.answer?.trim() ? payload.answer : rawAnswer || message.content;
        message.content = payload.error ? `抱歉，本次处理失败：\n${payload.error}` : finalAnswer;
        message.imageUrls = payload.image_urls || [];
        forceCompleteProgress(message);
        message.progressOpen = false;
        close(payload.error ? 'failed' : 'completed');
      } catch {
        forceCompleteProgress(message);
        message.progressOpen = false;
        close('completed');
      }
    };

    activeStream.addEventListener('final', handleFinal);
    activeStream.addEventListener('final_answer', handleFinal);

    activeStream.addEventListener('error', () => {
      if (closed) return;
      message.content = rawAnswer
        ? `${rawAnswer}\n\n（错误：SSE 连接中断/失败）`
        : '抱歉，SSE 连接中断/失败';
      message.progress = { status: 'failed', doneList: message.progress?.doneList || [], runningList: [] };
      message.progressOpen = false;
      close('failed');
    });
  });
}

async function clearConversation(): Promise<void> {
  if (!confirm('确定要清空当前会话的历史记录吗？这将无法恢复。')) return;
  try {
    await clearHistory(sessionId.value);
  } catch {
    alert('服务端清空失败，仅清空本地显示');
  } finally {
    messages.value = [createWelcomeMessage()];
    scrollToBottom();
  }
}

onMounted(() => {
  loadHistory();
  nextTick(() => inputRef.value?.focus());
});

onUnmounted(() => {
  activeStream?.close();
});
</script>

<template>
  <div class="view-shell chat-view">
    <div class="view-header">
      <div>
        <h3>知识库对话</h3>
        <p>保持原有 session、历史记录、流式输出和答案图片展示逻辑。</p>
      </div>
      <div class="chat-actions">
        <label class="switch glass-panel">
          <input v-model="streamMode" type="checkbox" />
          <span class="track"></span>
          <span class="thumb"></span>
          <span>流式输出</span>
        </label>
        <button class="ghost-button" type="button" @click="clearConversation">
          <Eraser :size="17" />
          清空对话
        </button>
      </div>
    </div>

    <div class="chat-stage">
      <div ref="chatBody" class="message-list">
        <article
          v-for="message in messages"
          :key="message.id"
          class="message-row"
          :class="message.role"
        >
          <div class="avatar" :class="message.role">{{ message.role === 'user' ? 'Me' : 'Bot' }}</div>
          <div class="message-stack">
            <div class="message-bubble" :class="{ waiting: message.status === 'pending' && !message.content }">
              <div v-if="message.status === 'pending' && !message.content" class="typing-row">
                <Loader2 :size="17" class="spin" />
                <span>正在建立任务...</span>
              </div>
              <template v-else>
                <div class="answer-text markdown-body" v-html="answerHtml(message)"></div>
                <div v-if="answerParts(message).images.length" class="answer-images">
                  <a
                    v-for="url in answerParts(message).images"
                    :key="url"
                    :href="url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <img
                      :src="url"
                      alt="参考图片"
                      loading="lazy"
                      referrerpolicy="no-referrer"
                      @error="hideFailedImage"
                    />
                    <span>{{ url }}</span>
                  </a>
                </div>
              </template>

              <details
                v-if="message.progress"
                class="progress-box"
                :open="message.progressOpen ?? true"
                @toggle="setProgressOpen(message, $event)"
              >
                <summary>
                  <Waves :size="15" />
                  {{ progressTitle(message.progress) }}
                </summary>
                <ul>
                  <li v-for="line in progressLines(message.progress)" :key="line">{{ line }}</li>
                </ul>
              </details>
            </div>
            <time>{{ message.metaText || formatClock(message.time) }}</time>
          </div>
        </article>
      </div>

      <form class="composer glass-panel" @submit.prevent="sendMessage">
        <textarea
          ref="inputRef"
          v-model="inputText"
          :disabled="sending"
          placeholder="请输入问题，Enter 发送，Shift + Enter 换行"
          rows="1"
          @keydown.enter.exact.prevent="sendMessage"
        ></textarea>
        <button class="primary-button" type="submit" :disabled="sending || !inputText.trim()">
          <SendHorizontal :size="17" />
          发送
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}

.chat-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.chat-stage {
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  gap: 14px;
  min-height: 0;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 8px 10px 4px;
}

.message-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 86%;
}

.message-row.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.avatar {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  border-radius: 15px;
  color: #fff;
  background: #111827;
  box-shadow: 0 12px 24px rgba(17, 24, 39, 0.16);
  font-size: 13px;
  font-weight: 800;
}

.avatar.assistant {
  color: #0f172a;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(255, 255, 255, 0.92);
}

.message-stack {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.message-row.user .message-stack {
  justify-items: end;
}

.message-bubble {
  min-width: 180px;
  max-width: min(720px, 100%);
  padding: 15px 16px;
  border-radius: 22px;
  color: #172033;
  background: rgba(255, 255, 255, 0.74);
  border: 1px solid rgba(255, 255, 255, 0.88);
  box-shadow: 0 14px 38px rgba(42, 55, 78, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.96);
}

.message-row.user .message-bubble {
  color: #fff;
  background: #111827;
  border-color: rgba(17, 24, 39, 0.24);
}

.message-bubble.waiting {
  color: #405066;
}

.answer-text {
  margin: 0;
  word-break: break-word;
  line-height: 1.72;
}

.answer-text :deep(p) {
  margin: 0 0 10px;
}

.answer-text :deep(p:last-child),
.answer-text :deep(ul:last-child),
.answer-text :deep(ol:last-child),
.answer-text :deep(blockquote:last-child),
.answer-text :deep(pre:last-child) {
  margin-bottom: 0;
}

.answer-text :deep(h3),
.answer-text :deep(h4),
.answer-text :deep(h5) {
  margin: 14px 0 8px;
  color: #0f172a;
  font-size: 15px;
  line-height: 1.45;
}

.answer-text :deep(h3:first-child),
.answer-text :deep(h4:first-child),
.answer-text :deep(h5:first-child) {
  margin-top: 0;
}

.answer-text :deep(ul),
.answer-text :deep(ol) {
  display: grid;
  gap: 7px;
  margin: 8px 0 12px;
  padding-left: 1.35em;
}

.answer-text :deep(li) {
  padding-left: 2px;
}

.answer-text :deep(strong) {
  color: #0f172a;
  font-weight: 850;
}

.answer-text :deep(em) {
  color: #334155;
  font-style: normal;
}

.answer-text :deep(code) {
  padding: 2px 6px;
  border-radius: 7px;
  color: #0f172a;
  background: rgba(226, 232, 240, 0.8);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 0.92em;
}

.answer-text :deep(pre) {
  margin: 10px 0 12px;
  overflow-x: auto;
  padding: 12px;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.92);
  color: #e5e7eb;
}

.answer-text :deep(pre code) {
  padding: 0;
  color: inherit;
  background: transparent;
}

.answer-text :deep(blockquote) {
  margin: 10px 0 12px;
  padding: 9px 12px;
  border-left: 3px solid rgba(37, 99, 235, 0.42);
  border-radius: 12px;
  color: #475569;
  background: rgba(239, 246, 255, 0.68);
}

.answer-text :deep(a) {
  color: #2563eb;
  text-decoration: none;
  border-bottom: 1px solid rgba(37, 99, 235, 0.28);
}

.answer-text :deep(.md-image-alt) {
  color: #64748b;
}

.message-row.user .answer-text :deep(h3),
.message-row.user .answer-text :deep(h4),
.message-row.user .answer-text :deep(h5),
.message-row.user .answer-text :deep(strong),
.message-row.user .answer-text :deep(em),
.message-row.user .answer-text :deep(a) {
  color: #fff;
}

.message-row.user .answer-text :deep(code) {
  color: #fff;
  background: rgba(255, 255, 255, 0.14);
}

.answer-images {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.answer-images a {
  display: grid;
  gap: 6px;
  color: #2563eb;
  font-size: 12px;
  word-break: break-all;
}

.answer-images img {
  max-width: min(520px, 100%);
  max-height: 320px;
  object-fit: contain;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.9);
}

.message-stack time {
  color: var(--soft);
  font-size: 12px;
}

.typing-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.spin {
  animation: spin 1s linear infinite;
}

.progress-box {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid rgba(125, 144, 168, 0.2);
}

.progress-box summary {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #526174;
  cursor: pointer;
  font-size: 13px;
  font-weight: 800;
}

.progress-box ul {
  display: grid;
  gap: 5px;
  margin: 9px 0 0;
  padding: 0;
  color: #657386;
  list-style: none;
  font-size: 13px;
}

.composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: end;
  padding: 12px;
  border-radius: 24px;
}

.composer textarea {
  width: 100%;
  max-height: 180px;
  min-height: 48px;
  resize: vertical;
  border: 0;
  outline: none;
  padding: 13px 14px;
  color: #152033;
  background: rgba(255, 255, 255, 0.68);
  border-radius: 17px;
  box-shadow: inset 0 1px 4px rgba(54, 68, 88, 0.08);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 760px) {
  .message-row {
    max-width: 100%;
  }

  .composer {
    grid-template-columns: 1fr;
  }
}
</style>
