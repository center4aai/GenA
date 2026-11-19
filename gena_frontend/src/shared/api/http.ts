import { AUTH_ENABLED } from '@/shared/config/auth';

const TOKEN_KEY = 'gena_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function buildUrl(base: string, path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  const normalizedBase = (base || '').replace(/\/$/, '');
  return `${normalizedBase}/${path.replace(/^\//, '')}`;
}

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: string,
  ) {
    super(message);
    this.name = 'HttpError';
  }
}

function formatErrorDetail(detail: unknown): string | null {
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object' && 'msg' in item) {
        return String((item as { msg: unknown }).msg);
      }
      return JSON.stringify(item);
    });
    const joined = parts.filter(Boolean).join('; ');
    return joined || null;
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  if (detail != null) return String(detail);
  return null;
}

export function parseErrorBody(status: number, body: string): string {
  const trimmed = body.trim();
  if (!trimmed) return `HTTP ${status}`;
  try {
    const json = JSON.parse(trimmed) as { detail?: unknown; error?: unknown; message?: unknown };
    const msg = formatErrorDetail(json.detail ?? json.error ?? json.message);
    if (msg) return msg;
  } catch {
    // plain text body
  }
  return trimmed.length > 300 ? `${trimmed.slice(0, 300)}…` : trimmed;
}

function handleUnauthorized(): void {
  if (!AUTH_ENABLED) return;
  onUnauthorized?.();
  if (!window.location.pathname.includes('/login')) {
    window.location.assign('/login');
  }
}

let onUnauthorized: (() => void) | null = null;

export function registerUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

function isAuthEndpoint(path: string): boolean {
  const normalized = path.replace(/^\//, '');
  return normalized === 'auth/login' || normalized.startsWith('auth/');
}

const REQUEST_ID_KEY = 'gena_request_id';

function getOrCreateRequestId(): string {
  let id = sessionStorage.getItem(REQUEST_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(REQUEST_ID_KEY, id);
  }
  return id;
}

export async function httpFetch(
  baseUrl: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  const sentToken = AUTH_ENABLED && token ? token : null;
  if (sentToken) {
    headers.set('Authorization', `Bearer ${sentToken}`);
  }
  if (!headers.has('Content-Type') && init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('X-Request-ID', getOrCreateRequestId());

  const { signal, ...rest } = init;
  const response = await fetch(buildUrl(baseUrl, path), {
    ...rest,
    headers,
    signal,
  });

  if (
    response.status === 401 &&
    AUTH_ENABLED &&
    baseUrl === DATASET_BASE &&
    !isAuthEndpoint(path) &&
    (sentToken === null || getToken() === sentToken)
  ) {
    handleUnauthorized();
  }

  return response;
}

export async function httpJson<T>(
  baseUrl: string,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await httpFetch(baseUrl, path, init);
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new HttpError(response.status, parseErrorBody(response.status, body), body);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const DATASET_BASE = import.meta.env.VITE_DATASET_API_URL || '/api/dataset';
export const AGENT_BASE = import.meta.env.VITE_AGENT_API_URL || '/api/agent';

export function datasetFetch(path: string, init?: RequestInit) {
  return httpFetch(DATASET_BASE, path, init);
}

export function datasetJson<T>(path: string, init?: RequestInit) {
  return httpJson<T>(DATASET_BASE, path, init);
}

export function agentJson<T>(path: string, init?: RequestInit) {
  return httpJson<T>(AGENT_BASE, path, init);
}
