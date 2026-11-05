export type CandidateProfile = {
  profile_id: string;
  name: string;
  direction_id: string;
  role_id: string;
  is_active: boolean;
  version: number;
  preferences: {
    seniority?: string[];
    stacks?: string[];
    primary_language?: string;
    required_skills?: string[];
    excluded_skills?: string[];
    delivery_mode?: 'immediate' | 'hourly' | 'manual';
  };
};

export type CandidateVacancy = {
  vacancy_id: string | null;
  id?: string;
  title: string;
  company: string;
  summary: string;
  post_link: string;
  published_at: string | null;
  grade: string;
  salary: string;
  location: string;
  format: string;
  tags: string[];
  saved: boolean;
};

export type CandidateRun = {
  id: string;
  status: string;
  created_at: string;
  query: string;
  confirmed_count: number;
  source_states: Record<string, { status: string; reason?: string | null }>;
};

export type CandidateTemplate = { id: string; label: string; query: string };
export type CandidateMe = {
  user: { id: number; first_name: string; username: string };
  profiles: CandidateProfile[];
  active_profile_id: string | null;
  profile_state: 'none' | 'single' | 'ambiguous';
  premium_enabled: boolean;
};
export type ProfilePayload = {
  name: string;
  direction_id: string;
  role_id: string;
  seniority: string[];
  stacks: string[];
  required_skills: string[];
  excluded_skills: string[];
  delivery_mode: 'immediate' | 'hourly' | 'manual';
};

export class CandidateApi {
  constructor(private readonly initData: string, private readonly csrfToken = '') {}

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`/api/v1/candidate${path}`, {
      ...options,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
        ...(this.initData ? { 'X-Telegram-Init-Data': this.initData } : {}),
        ...(this.csrfToken && options.method && options.method !== 'GET' ? { 'X-Candidate-CSRF': this.csrfToken } : {}),
      },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = typeof body.detail === 'string' ? body.detail : 'Не удалось выполнить запрос';
      throw new Error(detail);
    }
    return response.status === 204 ? undefined as T : response.json() as Promise<T>;
  }

  me() { return this.request<CandidateMe>('/me'); }
  feed(bucket: 'all' | 'new' | 'older' | 'undated', offset = 0, limit = 20) {
    return this.request<{ items: CandidateVacancy[]; total: number }>(`/feed?bucket=${bucket}&offset=${offset}&limit=${limit}`);
  }
  feedSettings() { return this.request<{ older_days: number; max_older_days: number }>('/feed/settings'); }
  setOlderDays(older_days: number) { return this.request<{ older_days: number; max_older_days: number }>('/feed/settings', { method: 'PUT', body: JSON.stringify({ older_days }) }); }
  saved() { return this.request<{ items: CandidateVacancy[] }>('/saved'); }
  save(vacancyId: string) { return this.request<{ saved: boolean }>(`/saved/${encodeURIComponent(vacancyId)}`, { method: 'PUT' }); }
  unsave(vacancyId: string) { return this.request<{ saved: boolean }>(`/saved/${encodeURIComponent(vacancyId)}`, { method: 'DELETE' }); }
  createProfile(body: ProfilePayload) { return this.request<CandidateProfile>('/profiles', { method: 'POST', body: JSON.stringify(body) }); }
  updateProfile(id: string, body: ProfilePayload & { version: number }) { return this.request<CandidateProfile>(`/profiles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) }); }
  activateProfile(id: string) { return this.request<CandidateProfile>(`/profiles/${encodeURIComponent(id)}/activate`, { method: 'POST' }); }
  deleteProfile(id: string) { return this.request<void>(`/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' }); }
  templates() { return this.request<{ items: CandidateTemplate[] }>('/premium/templates'); }
  runs() { return this.request<{ items: CandidateRun[] }>('/premium/runs'); }
  startRun(template_id: string, client_request_id: string) { return this.request<CandidateRun>('/premium/runs', { method: 'POST', body: JSON.stringify({ template_id, client_request_id }) }); }
  run(id: string) { return this.request<CandidateRun>(`/premium/runs/${encodeURIComponent(id)}`); }
  results(id: string) { return this.request<{ items: CandidateVacancy[]; total: number }>(`/premium/runs/${encodeURIComponent(id)}/results`); }
  cancelRun(id: string) { return this.request<CandidateRun>(`/premium/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }); }
  savePremium(runId: string, resultId: string) { return this.request<{ saved: boolean }>(`/premium/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(resultId)}/saved`, { method: 'PUT' }); }
}

export async function browserSession(): Promise<{ authenticated: boolean; csrf_token?: string }> {
  const response = await fetch('/api/v1/candidate/auth/session', { cache: 'no-store', credentials: 'same-origin' });
  if (!response.ok) throw new Error('Не удалось проверить вход. Попробуйте обновить страницу.');
  return response.json();
}

export async function browserLogin(code: string): Promise<string> {
  const response = await fetch('/api/v1/candidate/auth/code', {
    method: 'POST', cache: 'no-store', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось войти');
  return data.csrf_token;
}

export async function browserLogout(csrfToken: string): Promise<void> {
  const response = await fetch('/api/v1/candidate/auth/logout', {
    method: 'POST', cache: 'no-store', credentials: 'same-origin',
    headers: { 'X-Candidate-CSRF': csrfToken },
  });
  if (!response.ok) throw new Error('Не удалось выйти. Попробуйте ещё раз.');
}
