export const API = import.meta.env.VITE_API_URL || '';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function endpointFor(path: string): string {
  // Blitz exposes the API under /api/v1.
  if (path === '/health' || path === '/healthz' || path === '/readyz') {
    return `${API}/api/v1${path}`;
  }
  return `${API}/api/v1${path}`;
}

export async function request<T>(path: string, init: RequestInit = {}, csrf?: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  if (csrf && init.method && !['GET', 'HEAD'].includes(init.method)) headers.set('X-CSRF-Token', csrf);
  const response = await fetch(endpointFor(path), {...init, credentials: 'include', headers});
  const json = await parseJson(response);
  if (!response.ok) {
    const detail = Array.isArray(json.detail)
      ? json.detail.map((error: {msg: string}) => error.msg).join('; ')
      : typeof json.detail === 'string' ? json.detail : `HTTP ${response.status}`;
    throw new ApiError(response.status, detail);
  }
  return json as T;
}

async function parseJson(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new ApiError(response.status, `HTTP ${response.status}`);
  }
}

export async function upload<T>(path: string, file: File, csrf?: string): Promise<T> {
  const headers = new Headers({'Content-Type': 'application/octet-stream'});
  if (csrf) headers.set('X-CSRF-Token', csrf);
  const response = await fetch(`${API}/api/v1${path}`, {
    method: 'POST', body: file, credentials: 'include', headers,
  });
  const json = await parseJson(response);
  if (!response.ok) {
    const detail = Array.isArray(json.detail)
      ? json.detail.map((error: {msg: string}) => error.msg).join('; ')
      : typeof json.detail === 'string' ? json.detail : `HTTP ${response.status}`;
    throw new ApiError(response.status, detail);
  }
  return json as T;
}
