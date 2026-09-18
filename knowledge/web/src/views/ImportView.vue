<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import {
  CheckCircle2,
  CircleAlert,
  FileText,
  FolderUp,
  Loader2,
  TimerReset,
  Trash2,
  UploadCloud
} from '@lucide/vue';
import { cancelImportTask, fetchImportStatus, uploadKnowledgeFile } from '../services/api';
import type { TaskStatusResponse } from '../types/api';
import { formatClock, formatDuration } from '../utils/answer';

type ImportUiStatus = 'uploading' | 'processing' | 'completed' | 'failed' | 'cancelled';
const IMPORT_TASKS_KEY = 'kb_import_tasks';
const MAX_PERSISTED_IMPORT_TASKS = 30;

interface ImportTask {
  id: string;
  taskId?: string;
  name: string;
  sizeText: string;
  extension: string;
  status: ImportUiStatus;
  progress: number;
  totalNodes: number;
  doneList: string[];
  runningList: string[];
  durations: Record<string, number>;
  logOpen: boolean;
  lastCheckedAt?: Date;
  pollError?: string;
  error?: string;
}

interface PersistedImportTask extends Omit<ImportTask, 'lastCheckedAt'> {
  lastCheckedAt?: string;
}

const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const tasks = ref<ImportTask[]>([]);
const pollers = new Map<string, number>();
const collapseTimers = new Map<string, number>();

const hasTasks = computed(() => tasks.value.length > 0);
const hasFailedTasks = computed(() => tasks.value.some((task) => task.status === 'failed'));

function toPersistedTask(task: ImportTask): PersistedImportTask {
  return {
    ...task,
    lastCheckedAt: task.lastCheckedAt?.toISOString()
  };
}

function fromPersistedTask(task: PersistedImportTask): ImportTask {
  const restored: ImportTask = {
    id: task.id || makeId(),
    taskId: task.taskId,
    name: task.name || '未知文件',
    sizeText: task.sizeText || '',
    extension: task.extension || 'FILE',
    status: task.status || 'processing',
    progress: Number.isFinite(task.progress) ? task.progress : 0,
    totalNodes: Number.isFinite(task.totalNodes) ? task.totalNodes : 8,
    doneList: Array.isArray(task.doneList) ? task.doneList : [],
    runningList: Array.isArray(task.runningList) ? task.runningList : [],
    durations: task.durations || {},
    logOpen: task.logOpen ?? task.status !== 'completed',
    lastCheckedAt: task.lastCheckedAt ? new Date(task.lastCheckedAt) : undefined,
    pollError: task.pollError,
    error: task.error
  };

  if (!restored.taskId && restored.status !== 'completed' && restored.status !== 'failed') {
    restored.status = 'failed';
    restored.progress = 0;
    restored.runningList = [];
    restored.error = restored.error || '上传请求已中断，请重新上传文件';
    restored.logOpen = true;
  }

  return restored;
}

function persistImportTasks(): void {
  try {
    const payload = tasks.value.slice(0, MAX_PERSISTED_IMPORT_TASKS).map(toPersistedTask);
    localStorage.setItem(IMPORT_TASKS_KEY, JSON.stringify(payload));
  } catch {
    // Persistence is a UI convenience; upload and polling should not fail because localStorage is unavailable.
  }
}

function restoreImportTasks(): void {
  try {
    const raw = localStorage.getItem(IMPORT_TASKS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as PersistedImportTask[];
    if (!Array.isArray(parsed)) return;

    tasks.value = parsed.map(fromPersistedTask);
    for (const task of tasks.value) {
      if (task.taskId && task.status !== 'completed' && task.status !== 'failed') {
        task.status = 'processing';
        startPolling(task.id);
      }
    }
  } catch {
    localStorage.removeItem(IMPORT_TASKS_KEY);
  }
}

function updateTask(id: string, updater: (task: ImportTask) => void): ImportTask | undefined {
  const task = tasks.value.find((item) => item.id === id);
  if (!task) return undefined;
  updater(task);
  return task;
}

function stopTaskTimers(taskId: string): void {
  const poller = pollers.get(taskId);
  if (poller !== undefined) {
    window.clearInterval(poller);
    pollers.delete(taskId);
  }

  const collapseTimer = collapseTimers.get(taskId);
  if (collapseTimer !== undefined) {
    window.clearTimeout(collapseTimer);
    collapseTimers.delete(taskId);
  }
}

function removeTask(taskId: string): void {
  stopTaskTimers(taskId);
  tasks.value = tasks.value.filter((task) => task.id !== taskId);
}

function clearFailedTasks(): void {
  const failedIds = tasks.value.filter((task) => task.status === 'failed').map((task) => task.id);
  for (const id of failedIds) stopTaskTimers(id);
  tasks.value = tasks.value.filter((task) => task.status !== 'failed');
}

function clearAllTasks(): void {
  if (!tasks.value.length) return;
  if (!confirm('确定要清除所有上传任务显示吗？这不会停止后端已经提交的处理流程。')) return;

  for (const task of tasks.value) stopTaskTimers(task.id);
  tasks.value = [];
}

async function cancelAndRemoveTask(task: ImportTask): Promise<void> {
  const backendTaskId = task.taskId;
  removeTask(task.id);

  if (backendTaskId) {
    try {
      await cancelImportTask(backendTaskId);
    } catch {
      // The card should still disappear from the UI even if the backend task has already ended.
    }
  }
}

function makeId(): string {
  return `file-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

function extensionOf(fileName: string): string {
  const index = fileName.lastIndexOf('.');
  return index >= 0 ? fileName.slice(index + 1).toUpperCase() : 'FILE';
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function totalNodesFor(fileName: string): number {
  return fileName.toLowerCase().endsWith('.pdf') ? 8 : 7;
}

function clampProgress(value: number): number {
  return Math.min(Math.max(value, 0), 100);
}

function triggerPick(): void {
  fileInput.value?.click();
}

function handleInput(event: Event): void {
  const target = event.target as HTMLInputElement;
  handleFiles(target.files);
  target.value = '';
}

function handleFiles(files: FileList | null): void {
  for (const file of Array.from(files || [])) {
    uploadOne(file);
  }
}

function handleDrop(event: DragEvent): void {
  dragging.value = false;
  handleFiles(event.dataTransfer?.files || null);
}

async function uploadOne(file: File): Promise<void> {
  const totalNodes = totalNodesFor(file.name);
  const task: ImportTask = {
    id: makeId(),
    name: file.name,
    sizeText: formatBytes(file.size),
    extension: extensionOf(file.name),
    status: 'uploading',
    progress: clampProgress(Math.min((1 / totalNodes) * 100, 10)),
    totalNodes,
    doneList: [],
    runningList: ['开始上传文件'],
    durations: {},
    logOpen: true
  };
  tasks.value.unshift(task);

  try {
    const result = await uploadKnowledgeFile(file);
    updateTask(task.id, (current) => {
      current.taskId = result.task_id;
      current.status = 'processing';
      current.doneList = ['上传文件'];
      current.runningList = [];
      current.progress = clampProgress((1 / totalNodes) * 100);
      current.error = undefined;
      current.pollError = undefined;
    });
    startPolling(task.id);
  } catch (error) {
    updateTask(task.id, (current) => {
      current.status = 'failed';
      current.progress = 0;
      current.runningList = [];
      current.error = error instanceof Error ? error.message : String(error);
      current.logOpen = true;
    });
  }
}

function applyStatus(task: ImportTask, data: TaskStatusResponse): void {
  task.doneList = Array.isArray(data.done_list) ? data.done_list : [];
  task.runningList = Array.isArray(data.running_list) ? data.running_list : [];
  task.durations = data.durations || {};
  task.lastCheckedAt = new Date();
  task.pollError = undefined;

  if (data.status === 'completed') {
    task.status = 'completed';
    task.progress = 100;
    stopPolling(task);
    scheduleLogCollapse(task.id);
    return;
  }

  if (data.status === 'failed') {
    task.status = 'failed';
    task.progress = calculateProgress(task);
    task.error = '工作流执行失败中止';
    task.logOpen = true;
    stopPolling(task);
    return;
  }

  if (data.status === 'cancelled') {
    task.status = 'cancelled';
    task.progress = calculateProgress(task);
    task.error = '任务已取消';
    task.logOpen = true;
    stopPolling(task);
    return;
  }

  task.status = 'processing';
  task.progress = calculateProgress(task);
}

function calculateProgress(task: ImportTask): number {
  let current = (task.doneList.length / task.totalNodes) * 100;
  if (task.runningList.length > 0) current += (0.5 / task.totalNodes) * 100;
  if (current >= 100) current = 95;
  return clampProgress(current);
}

function startPolling(taskId: string): void {
  if (pollers.has(taskId)) return;

  const current = tasks.value.find((item) => item.id === taskId);
  if (!current?.taskId) return;

  const tick = async () => {
    const latest = tasks.value.find((item) => item.id === taskId);
    if (!latest?.taskId) return;

    try {
      const data = await fetchImportStatus(latest.taskId);
      updateTask(taskId, (task) => applyStatus(task, data));
    } catch (error) {
      updateTask(taskId, (task) => {
        task.pollError = error instanceof Error ? error.message : '状态轮询失败，继续重试';
      });
      return;
    }
  };

  tick();
  const id = window.setInterval(tick, 1500);
  pollers.set(taskId, id);
}

function stopPolling(task: ImportTask): void {
  const id = pollers.get(task.id);
  if (id === undefined) return;
  window.clearInterval(id);
  pollers.delete(task.id);
}

function scheduleLogCollapse(taskId: string): void {
  if (collapseTimers.has(taskId)) return;
  const timer = window.setTimeout(() => {
    updateTask(taskId, (task) => {
      task.logOpen = false;
    });
    collapseTimers.delete(taskId);
  }, 1000);
  collapseTimers.set(taskId, timer);
}

function setLogOpen(task: ImportTask, event: Event): void {
  task.logOpen = (event.currentTarget as HTMLDetailsElement).open;
}

function statusLabel(status: ImportUiStatus): string {
  const labels: Record<ImportUiStatus, string> = {
    uploading: '上传中',
    processing: '处理中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消'
  };
  return labels[status];
}

function statusIcon(status: ImportUiStatus) {
  const icons = {
    uploading: UploadCloud,
    processing: Loader2,
    completed: CheckCircle2,
    failed: CircleAlert,
    cancelled: CircleAlert
  };
  return icons[status];
}

function visibleDoneList(task: ImportTask): string[] {
  return task.doneList.filter((item) => item !== 'upload_file' && item !== '上传文件');
}

function totalDuration(task: ImportTask): string {
  const total = Object.values(task.durations).reduce((sum, value) => sum + value, 0);
  return total > 0 ? formatDuration(total) : '0.0s';
}

onUnmounted(() => {
  for (const id of pollers.values()) window.clearInterval(id);
  pollers.clear();
  for (const id of collapseTimers.values()) window.clearTimeout(id);
  collapseTimers.clear();
});

onMounted(() => {
  restoreImportTasks();
});

watch(tasks, persistImportTasks, { deep: true });
</script>

<template>
  <div class="view-shell import-view">
    <div class="view-header">
      <div>
        <h3>文件导入</h3>
        <p>上传 PDF 或 Markdown 后，实时跟踪导入工作流节点状态。</p>
      </div>
      <div class="import-actions">
        <button class="ghost-button danger" type="button" :disabled="!hasFailedTasks" @click="clearFailedTasks">
          <Trash2 :size="16" />
          清除失败
        </button>
        <button class="ghost-button" type="button" :disabled="!hasTasks" @click="clearAllTasks">
          <Trash2 :size="16" />
          全部清除
        </button>
        <button class="primary-button" type="button" @click="triggerPick">
          <FolderUp :size="17" />
          选择文件
        </button>
      </div>
    </div>

    <input ref="fileInput" class="file-input" type="file" accept=".pdf,.md" @change="handleInput" />

    <section
      class="drop-zone"
      :class="{ dragging }"
      @click="triggerPick"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="handleDrop"
    >
      <div class="drop-icon">
        <UploadCloud :size="34" />
      </div>
      <div>
        <strong>点击或拖拽文件到这里</strong>
        <span>支持 PDF / MD，上传后自动进入导入流程</span>
      </div>
    </section>

    <section class="task-list" :class="{ empty: !hasTasks }">
      <div v-if="!hasTasks" class="empty-state">
        <FileText :size="34" />
        <strong>还没有导入任务</strong>
        <span>选择文件后会在这里显示进度和节点日志。</span>
      </div>

      <article v-for="task in tasks" :key="task.id" class="file-card">
        <div class="file-main">
          <div class="file-type">{{ task.extension }}</div>
          <div class="file-meta">
            <strong>{{ task.name }}</strong>
            <span>{{ task.sizeText }} · {{ task.taskId || '等待任务 ID' }}</span>
          </div>
        </div>

        <div class="task-actions">
          <div class="status-pill" :class="task.status">
            <component :is="statusIcon(task.status)" :size="16" :class="{ spin: task.status === 'processing' }" />
            {{ statusLabel(task.status) }}
          </div>
          <button
            v-if="task.status === 'processing'"
            class="icon-button danger"
            type="button"
            title="取消后端处理并删除该任务"
            aria-label="取消后端处理并删除该任务"
            @click="cancelAndRemoveTask(task)"
          >
            <Trash2 :size="16" />
          </button>
          <button
            v-else-if="task.status === 'failed' || task.status === 'cancelled'"
            class="icon-button danger"
            type="button"
            title="清除该任务"
            aria-label="清除该任务"
            @click="removeTask(task.id)"
          >
            <Trash2 :size="16" />
          </button>
        </div>

        <div class="progress-line">
          <div class="progress-track">
            <span :style="{ width: `${task.progress}%` }"></span>
          </div>
          <b>{{ Math.round(task.progress) }}%</b>
        </div>

        <details class="import-log" :open="task.logOpen" @toggle="setLogOpen(task, $event)">
          <summary>
            <TimerReset :size="15" />
            已完成 {{ task.doneList.length }}，进行中 {{ task.runningList.length }}，共 {{ task.totalNodes }} 步，总耗时
            {{ totalDuration(task) }}
          </summary>
          <div class="poll-meta">
            <span>最后更新：{{ task.lastCheckedAt ? formatClock(task.lastCheckedAt) : '等待首次状态' }}</span>
            <span v-if="task.pollError" class="poll-warning">{{ task.pollError }}</span>
          </div>
          <ul>
            <li v-for="item in visibleDoneList(task)" :key="`done-${item}`" class="done">
              <span>{{ item }} 节点执行完成</span>
              <em v-if="task.durations[item] !== undefined">{{ formatDuration(task.durations[item]) }}</em>
            </li>
            <li v-for="item in task.runningList" :key="`running-${item}`" class="running">
              <span>正在处理 {{ item }} 节点...</span>
            </li>
            <li v-if="task.pollError" class="warning">状态轮询暂时失败，前端会继续重试</li>
            <li v-if="task.error" class="failed">{{ task.error }}</li>
          </ul>
        </details>
      </article>
    </section>
  </div>
</template>

<style scoped>
.import-view {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 16px;
}

.file-input {
  display: none;
}

.import-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.drop-zone {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  align-items: center;
  gap: 15px;
  min-height: 132px;
  padding: 22px;
  border: 1px dashed rgba(85, 107, 135, 0.35);
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.48);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.86);
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.drop-zone:hover,
.drop-zone.dragging {
  transform: translateY(-1px);
  border-color: rgba(37, 99, 235, 0.52);
  background: rgba(255, 255, 255, 0.72);
}

.drop-icon {
  display: grid;
  place-items: center;
  width: 64px;
  height: 64px;
  border-radius: 22px;
  color: var(--accent);
  background: rgba(37, 99, 235, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.82);
}

.drop-zone strong,
.empty-state strong,
.file-meta strong {
  color: #0f172a;
}

.drop-zone span,
.empty-state span,
.file-meta span {
  display: block;
  margin-top: 3px;
  color: var(--muted);
  font-size: 13px;
}

.task-list {
  display: grid;
  align-content: start;
  gap: 12px;
  min-height: 0;
  overflow-y: auto;
  padding-right: 6px;
}

.task-list.empty {
  align-content: stretch;
}

.empty-state {
  display: grid;
  place-items: center;
  align-content: center;
  min-height: 260px;
  color: #7a8798;
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.44);
}

.empty-state svg {
  margin-bottom: 10px;
  color: #748399;
}

.file-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  padding: 16px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.9);
  box-shadow: 0 14px 34px rgba(42, 55, 78, 0.09), inset 0 1px 0 rgba(255, 255, 255, 0.96);
}

.file-main {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.file-type {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 48px;
  height: 48px;
  border-radius: 17px;
  color: #0f172a;
  background: rgba(238, 244, 252, 0.96);
  font-size: 12px;
  font-weight: 900;
}

.file-meta {
  min-width: 0;
}

.file-meta strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-width: 94px;
  height: 38px;
  padding: 0 12px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 800;
}

.task-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 9px;
}

.danger {
  color: var(--red);
  background: rgba(220, 38, 38, 0.08);
}

.status-pill.uploading {
  color: var(--amber);
  background: rgba(252, 211, 77, 0.18);
}

.status-pill.processing {
  color: var(--accent);
  background: var(--accent-soft);
}

.status-pill.completed {
  color: var(--green);
  background: rgba(16, 185, 129, 0.12);
}

.status-pill.failed {
  color: var(--red);
  background: rgba(220, 38, 38, 0.1);
}

.status-pill.cancelled {
  color: var(--amber);
  background: rgba(252, 211, 77, 0.16);
}

.progress-line {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 48px;
  align-items: center;
  gap: 12px;
}

.progress-track {
  height: 10px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(216, 226, 238, 0.9);
}

.progress-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #111827, #2563eb);
  transition: width 0.24s ease;
}

.progress-line b {
  color: #3f4d62;
  font-size: 13px;
  text-align: right;
}

.import-log {
  grid-column: 1 / -1;
  border-top: 1px solid rgba(125, 144, 168, 0.18);
  padding-top: 10px;
}

.import-log summary {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: #526174;
  cursor: pointer;
  font-size: 13px;
  font-weight: 800;
}

.import-log ul {
  display: grid;
  gap: 7px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}

.poll-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 8px;
  color: #7a8798;
  font-size: 12px;
}

.poll-warning {
  color: var(--amber);
}

.import-log li {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 28px;
  color: #5a687c;
  font-size: 13px;
}

.import-log li::before {
  content: "";
  flex: 0 0 auto;
  width: 9px;
  height: 9px;
  border-radius: 999px;
  background: #aab6c5;
}

.import-log li.done::before {
  background: var(--green);
}

.import-log li.running::before {
  background: var(--accent);
  box-shadow: 0 0 0 5px rgba(37, 99, 235, 0.1);
}

.import-log li.failed {
  color: var(--red);
}

.import-log li.warning {
  color: var(--amber);
}

.import-log li.failed::before {
  background: var(--red);
}

.import-log li.warning::before {
  background: var(--amber);
}

.import-log em {
  margin-left: auto;
  padding: 3px 7px;
  border-radius: 999px;
  color: #526174;
  background: rgba(226, 235, 247, 0.78);
  font-style: normal;
  font-size: 12px;
  font-weight: 800;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 720px) {
  .drop-zone,
  .file-card {
    grid-template-columns: 1fr;
  }

  .status-pill {
    justify-self: start;
  }
}
</style>
