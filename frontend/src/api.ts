export const API = import.meta.env.VITE_API_URL || 'http://localhost:8001';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function request<T>(path: string, init: RequestInit = {}, csrf?: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  if (csrf && init.method && !['GET', 'HEAD'].includes(init.method)) headers.set('X-CSRF-Token', csrf);
  const response = await fetch(`${API}/api/v1${path}`, {...init, credentials: 'include', headers});
  const json = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(json.detail)
      ? json.detail.map((error: {msg: string}) => error.msg).join('; ')
      : json.detail || `HTTP ${response.status}`;
    throw new ApiError(response.status, detail);
  }
  return json as T;
}

export async function upload<T>(path: string, file: File, csrf?: string): Promise<T> {
  const headers = new Headers({'Content-Type': 'application/octet-stream'});
  if (csrf) headers.set('X-CSRF-Token', csrf);
  const response = await fetch(`${API}/api/v1${path}`, {
    method: 'POST', body: file, credentials: 'include', headers,
  });
  const json = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(json.detail)
      ? json.detail.map((error: {msg: string}) => error.msg).join('; ')
      : json.detail || `HTTP ${response.status}`;
    throw new ApiError(response.status, detail);
  }
  return json as T;
}
