export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled' | string;

export interface QueryRequestPayload {
  query: string;
  session_id: string;
  is_stream: boolean;
}

export interface QueryResponse {
  message: string;
  session_id: string;
  answer: string;
  error?: string;
  image_urls?: string[];
  done_list?: string[];
}

export interface StreamSubmitResponse {
  message: string;
  session_id: string;
  task_id: string;
}

export interface HistoryItem {
  _id?: string;
  id?: string;
  session_id?: string;
  role: 'user' | 'assistant' | string;
  text?: string;
  rewritten_query?: string;
  item_names?: string[];
  image_urls?: string[];
  ts?: number;
}

export interface HistoryResponse {
  session_id: string;
  items: HistoryItem[];
}

export interface UploadResponse {
  message: string;
  task_id: string;
}

export interface CancelImportTaskResponse {
  message: string;
  task_id: string;
  status: TaskStatus;
}

export interface TaskStatusResponse {
  status: TaskStatus;
  done_list: string[];
  running_list: string[];
  durations: Record<string, number>;
}

export interface SseProgressPayload {
  status?: TaskStatus;
  done_list?: string[];
  running_list?: string[];
}

export interface SseDeltaPayload {
  delta?: string;
}

export interface SseFinalPayload {
  answer?: string;
  error?: string;
  image_urls?: string[];
}
