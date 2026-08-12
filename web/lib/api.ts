export type Source = { token:string; label:string; identifier:string|null; enabled:boolean; kind:string; origin:string; added_at:string|null; removable:boolean };
export type Settings = {
  revision: number;
  telegram: { folder_name: string; monitoring_enabled: boolean; history_days: number; notify_enabled: boolean; notify_target: string; additional_channels: string[]; channels: Source[] };
  filters: { keywords: string[]; exclude_keywords: string[]; text_hash_ttl_days:number; queue_maxsize:number; workers:number };
  mistral: { model:string; temperature:number; max_attempts:number };
  sheets: { output_timezone:string; full_title:string; short_title:string };
};
export type Metrics = { date:string; timezone:string; counts:{posts_processed:number;vacancies_added:number;skipped:number;errors:number}; description:string };
export type AttentionError = {id:string;component:string;summary:string;details:string;status:'new'|'repeating'|'resolved';count:number;first_seen_at:string;last_seen_at:string};
export type AppLog = {at:string;level:'INFO'|'WARNING'|'ERROR';component:string;message:string};
export type LogsPage = {items:AppLog[];total:number;offset:number;limit:number};
export type Prompt = {instructions:string;default_instructions:string;is_custom:boolean;restart_required:boolean;variables:string[]};

function csrf(): string { return document.cookie.split('; ').find((item) => item.startsWith('admin_csrf='))?.split('=')[1] ?? ''; }
async function request<T>(path:string, init:RequestInit = {}):Promise<T> { const response = await fetch(path, { credentials:'same-origin', ...init, headers:{ 'Content-Type':'application/json', ...init.headers } }); if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || 'Не удалось выполнить запрос'); } return response.json(); }
const mutate = <T>(path:string, method:string, body?:unknown) => request<T>(path, {method, headers:{'X-CSRF-Token':csrf()}, body:body === undefined ? undefined : JSON.stringify(body)});
export const api = {
  status: () => request<{configured:boolean;authenticated:boolean}>('/api/v1/auth/status'),
  login: (password:string) => request('/api/v1/auth/login', {method:'POST', body:JSON.stringify({password})}),
  logout: () => mutate('/api/v1/auth/logout', 'POST'),
  dashboard: () => request<any>('/api/v1/dashboard'),
  settings: () => request<Settings>('/api/v1/settings'),
  save: (settings:Partial<Settings>) => mutate<Settings>('/api/v1/settings', 'PUT', settings),
  action: (action:string) => mutate('/api/v1/actions', 'POST', {action, confirmed:true}),
  metrics: () => request<Metrics>('/api/v1/metrics/today'),
  errors: () => request<AttentionError[]>('/api/v1/errors'),
  resolveError: (id:string) => mutate<AttentionError>(`/api/v1/errors/${id}/resolve`, 'POST', {confirmed:true}),
  logs: (query:Record<string,string|number>) => request<LogsPage>(`/api/v1/logs?${new URLSearchParams(Object.entries(query).filter(([,value])=>value !== '').map(([key,value])=>[key,String(value)])).toString()}`),
  prompt: () => request<Prompt>('/api/v1/prompt'),
  savePrompt: (instructions:string) => mutate<Prompt>('/api/v1/prompt', 'PUT', {instructions}),
  resetPrompt: () => mutate<Prompt>('/api/v1/prompt/reset', 'POST', {confirmed:true}),
  sources: () => request<{items:Source[];total:number;restart_required:boolean}>('/api/v1/sources'),
  addSource: (identifier:string) => mutate<{item:Source;restart_required:boolean}>('/api/v1/sources', 'POST', {identifier}),
  changeSource: (token:string, enabled:boolean) => mutate<{item:Source;restart_required:boolean}>(`/api/v1/sources/${token}`, 'PATCH', {enabled}),
  deleteSource: (token:string) => mutate<{ok:boolean;restart_required:boolean}>(`/api/v1/sources/${token}`, 'DELETE', {confirmed:true}),
};
