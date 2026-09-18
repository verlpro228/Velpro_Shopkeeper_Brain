import type {
  CancelImportTaskResponse,
  HistoryResponse,
  QueryRequestPayload,
  QueryResponse,
  StreamSubmitResponse,
  TaskStatusResponse,
  UploadResponse
} from '../types/api';
import { authFetch, getAuthToken } from './auth';

function trimBaseUrl(url: string): string {
  return url.replace(/\/+$/, '');
}

function isHttpPage(): boolean {
  return location.protocol === 'http:' || location.protocol === 'https:';
}

export function getQueryApiBase(): string {
  const fromEnv = import.meta.env.VITE_QUERY_API_BASE;
  if (fromEnv) return trimBaseUrl(fromEnv);
  if (isHttpPage() && location.port === '8001') return location.origin;
  return 'http://127.0.0.1:8001';
}

export function getImportApiBase(): string {
  const fromEnv = import.meta.env.VITE_IMPORT_API_BASE;
  if (fromEnv) return trimBaseUrl(fromEnv);
  if (isHttpPage() && location.port === '8000') return location.origin;
  return 'http://127.0.0.1:8000';
}

async function readError(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) return `HTTP ${response.status}`;
  try {
    const payload = JSON.parse(text) as { detail?: string; message?: string };
    return payload.detail || payload.message || text;
  } catch {
    return text;
  }
}

export async function submitQuery(
  payload: QueryRequestPayload
): Promise<QueryResponse | StreamSubmitResponse> {
  const response = await authFetch(`${getQueryApiBase()}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export function createQueryStream(taskId: string): EventSource {
  const token = getAuthToken();
  if (!token) throw new Error('登录状态已失效，请重新登录');
  const streamUrl = new URL(`${getQueryApiBase()}/stream/${encodeURIComponent(taskId)}`);
  streamUrl.searchParams.set('access_token', token);
  return new EventSource(streamUrl.toString());
}

export async function fetchHistory(sessionId: string): Promise<HistoryResponse> {
  const response = await authFetch(`${getQueryApiBase()}/history/${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function clearHistory(sessionId: string): Promise<void> {
  const response = await authFetch(`${getQueryApiBase()}/history/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE'
  });
  if (!response.ok) throw new Error(await readError(response));
}

export async function uploadKnowledgeFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await authFetch(`${getImportApiBase()}/upload`, {
    method: 'POST',
    body: formData
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function fetchImportStatus(taskId: string): Promise<TaskStatusResponse> {
  const response = await authFetch(`${getImportApiBase()}/status/${encodeURIComponent(taskId)}`);
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function cancelImportTask(taskId: string): Promise<CancelImportTaskResponse> {
  const response = await authFetch(`${getImportApiBase()}/cancel/${encodeURIComponent(taskId)}`, {
    method: 'POST'
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}
