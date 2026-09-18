export interface AuthSession {
  username: string;
  token: string;
  loginAt: number;
  expiresAt: number;
}

export interface LoginPayload {
  username: string;
  password: string;
  agreed: boolean;
}

export interface LoginResult {
  ok: boolean;
  message?: string;
  session?: AuthSession;
}

const AUTH_STORAGE_KEY = 'kb_auth_session';

interface BackendLoginResponse {
  message: string;
  username: string;
  token: string;
  token_type: string;
  login_at: number;
  expires_at: number;
}

function trimBaseUrl(url: string): string {
  return url.replace(/\/+$/, '');
}

function isHttpPage(): boolean {
  return location.protocol === 'http:' || location.protocol === 'https:';
}

function getQueryApiBase(): string {
  const fromEnv = import.meta.env.VITE_QUERY_API_BASE;
  if (fromEnv) return trimBaseUrl(fromEnv);
  if (isHttpPage() && location.port === '8001') return location.origin;
  return 'http://127.0.0.1:8001';
}

function readSession(): AuthSession | null {
  const raw = localStorage.getItem(AUTH_STORAGE_KEY);
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as AuthSession;
    if (!parsed.username || !parsed.token || !parsed.expiresAt) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeSession(session: AuthSession): void {
  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
}

export function clearAuthSession(): void {
  localStorage.removeItem(AUTH_STORAGE_KEY);
}

export function getAuthSession(): AuthSession | null {
  const session = readSession();
  if (!session) return null;
  if (session.expiresAt <= Date.now()) {
    clearAuthSession();
    return null;
  }
  return session;
}

export function isAuthenticated(): boolean {
  return getAuthSession() !== null;
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

export async function login(payload: LoginPayload): Promise<LoginResult> {
  const username = payload.username.trim();
  const password = payload.password;

  if (!username || !password) {
    return { ok: false, message: '请输入账号和密码' };
  }

  if (!payload.agreed) {
    return { ok: false, message: '请先勾选使用协议和须知' };
  }

  const response = await fetch(`${getQueryApiBase()}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username,
      password,
      agreed: payload.agreed
    })
  });

  if (!response.ok) {
    return { ok: false, message: await readError(response) };
  }

  const data = (await response.json()) as BackendLoginResponse;
  const session: AuthSession = {
    username: data.username,
    token: data.token,
    loginAt: data.login_at,
    expiresAt: data.expires_at
  };

  writeSession(session);
  return { ok: true, session };
}

export function logout(): void {
  clearAuthSession();
}

export function authHeaders(): Record<string, string> {
  const session = getAuthSession();
  if (!session) return {};
  return {
    Authorization: `Bearer ${session.token}`,
    'X-Auth-User': session.username
  };
}

export function getAuthToken(): string | null {
  return getAuthSession()?.token || null;
}

export async function authFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const session = getAuthSession();
  if (!session) {
    throw new Error('登录状态已失效，请重新登录');
  }

  const headers = new Headers(init.headers || {});
  for (const [key, value] of Object.entries(authHeaders())) {
    headers.set(key, value);
  }

  const response = await fetch(input, {
    ...init,
    headers
  });

  if (response.status === 401) {
    clearAuthSession();
  }

  return response;
}
