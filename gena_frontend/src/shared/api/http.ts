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

export async function httpFetch(
  baseUrl: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (!headers.has('Content-Type') && init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(buildUrl(baseUrl, path), {
    ...init,
    headers,
  });

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
    throw new HttpError(response.status, `HTTP ${response.status}`, body);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const DATASET_BASE = import.meta.env.VITE_DATASET_API_URL ?? '';
export const AGENT_BASE = import.meta.env.VITE_AGENT_API_URL ?? '';
export const CHUNKER_BASE = import.meta.env.VITE_CHUNKER_URL ?? '';

export function datasetFetch(path: string, init?: RequestInit) {
  return httpFetch(DATASET_BASE, path, init);
}

export function datasetJson<T>(path: string, init?: RequestInit) {
  return httpJson<T>(DATASET_BASE, path, init);
}

export function agentJson<T>(path: string, init?: RequestInit) {
  return httpJson<T>(AGENT_BASE, path, init);
}

export function chunkerFetch(path: string, init?: RequestInit) {
  return httpFetch(CHUNKER_BASE, path, init);
}
