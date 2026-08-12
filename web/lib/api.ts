export type Settings = {
  revision: number;
  telegram: { folder_name: string; monitoring_enabled: boolean; history_days: number; notify_enabled: boolean; notify_target: string; additional_channels: string[]; channels: {token:string;label:string;enabled:boolean;kind:string}[] };
  filters: { keywords: string[]; exclude_keywords: string[]; text_hash_ttl_days:number; queue_maxsize:number; workers:number };
  mistral: { model:string; temperature:number; max_attempts:number };
  sheets: { output_timezone:string; full_title:string; short_title:string };
};

function csrf(): string { return document.cookie.split('; ').find((item) => item.startsWith('admin_csrf='))?.split('=')[1] ?? ''; }
async function request<T>(path:string, init:RequestInit = {}):Promise<T> { const response = await fetch(path, { credentials:'same-origin', ...init, headers:{ 'Content-Type':'application/json', ...init.headers } }); if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || 'Не удалось выполнить запрос'); } return response.json(); }
export const api = {
  status: () => request<{configured:boolean;authenticated:boolean}>('/api/v1/auth/status'),
  login: (password:string) => request('/api/v1/auth/login', {method:'POST', body:JSON.stringify({password})}),
  logout: () => request('/api/v1/auth/logout', {method:'POST', headers:{'X-CSRF-Token':csrf()}}),
  dashboard: () => request<any>('/api/v1/dashboard'),
  settings: () => request<Settings>('/api/v1/settings'),
  save: (settings:Partial<Settings>) => request<Settings>('/api/v1/settings', {method:'PUT', headers:{'X-CSRF-Token':csrf()}, body:JSON.stringify(settings)}),
  action: (action:string) => request('/api/v1/actions', {method:'POST', headers:{'X-CSRF-Token':csrf()}, body:JSON.stringify({action, confirmed:true})}),
};
