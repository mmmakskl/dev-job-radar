'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import catalog from '../../../src/tg_vacancy_bot/data/it_roles.v1.json';
import OrbitMark from './orbit-mark';
import {
  CandidateApi,
  type CandidateMe,
  type CandidateProfile,
  type CandidateRun,
  type CandidateTemplate,
  type CandidateVacancy,
  type ProfilePayload,
  type FeedFilters,
} from '../../lib/candidate-api';

type Screen = 'feed' | 'saved' | 'profiles' | 'premium';
type Bucket = 'all' | 'new' | 'older' | 'undated';
type Editor = { profile_id?: string; version?: number; name: string; direction_id: string; role_id: string; stacks: string[]; grades: string[]; formats: string[]; geography: string[]; vacancyLanguages: string[]; skills: string; excluded: string; delivery: 'immediate' | 'hourly' | 'manual' };

const directions = catalog.directions.map(direction => ({
  id: direction.id,
  label: direction.label,
  roles: direction.specializations.flatMap(s => s.roles.map(role => ({ id: role.id, label: role.label, stacks: role.stacks }))),
}));
const navigation: { id: Screen; label: string; icon: string }[] = [
  { id: 'feed', label: 'Лента вакансий', icon: '▦' },
  { id: 'saved', label: 'Сохранённые', icon: '♧' },
  { id: 'profiles', label: 'Мои профили', icon: '♙' },
  { id: 'premium', label: 'Premium поиск', icon: '✦' },
];
const deliveryLabels = { immediate: 'Сразу', hourly: 'Раз в час', manual: 'Только вручную' };
const filterOptions = { formats: [{value:'remote',label:'Удалённо'},{value:'hybrid',label:'Гибрид'},{value:'office',label:'Офис'}], geography: [{value:'russia',label:'Россия'},{value:'europe',label:'Европа'},{value:'usa',label:'США'},{value:'cis',label:'СНГ кроме России'},{value:'asia',label:'Азия'}], vacancyLanguages: [{value:'ru',label:'Русский'},{value:'en',label:'Английский'},{value:'other',label:'Другой'}] };
const grades = ['Intern', 'Junior', 'Middle', 'Senior', 'Staff', 'Lead', 'Head'];
const languageIds = new Set(['go', 'python', 'java', 'cpp', 'csharp', 'rust', 'php', 'javascript', 'typescript', 'nodejs', 'kotlin', 'swift', 'bsl', 'sql']);
const stackLabels: Record<string, string> = { go: 'Go', python: 'Python', java: 'Java', cpp: 'C++', csharp: 'C#', rust: 'Rust', php: 'PHP', javascript: 'JavaScript', typescript: 'TypeScript', nodejs: 'Node.js', kotlin: 'Kotlin', swift: 'Swift', bsl: '1С (BSL)', sql: 'SQL', postgresql: 'PostgreSQL', api: 'API', react: 'React', vue: 'Vue' };
const stackLabel = (value: string) => stackLabels[value] ?? value.replaceAll('_', ' ');
const premiumReasons: Record<string, string> = {
  premium_required: 'Для поиска нужен Telegram Premium.',
  telegram_auth_required: 'Требуется повторная авторизация Telegram.',
  free_quota_exhausted: 'Бесплатная квота Telegram исчерпана.',
  premium_busy: 'Другой Premium-поиск уже выполняется.',
  telegram_or_search_failure: 'Telegram не выполнил поиск.',
  time_limit_reached: 'Поиск остановлен по лимиту времени; найденные вакансии сохранены.',
  no_recent_candidates: 'По этой роли свежих сообщений не найдено.',
  llm_failed: 'Не удалось завершить анализ вакансий.',
  disabled: 'Поиск временно недоступен.',
  premium_search_failed: 'Поиск завершился с ошибкой.',
};
const premiumPhases: Record<string, string> = { quota_check: 'Проверка квоты', collecting: 'Загрузка сообщений', searching: 'Поиск сообщений', analyzing: 'Анализ вакансий', flood_wait: 'Ожидание Telegram', llm_retry: 'Повтор анализа' };
const initialEditor = (): Editor => ({ name: '', direction_id: 'development', role_id: '', stacks: [], grades: [], formats: [], geography: [], vacancyLanguages: [], skills: '', excluded: '', delivery: 'immediate' });
const directionLabel = (id: string) => directions.find(d => d.id === id)?.label ?? 'Направление';
const roleLabel = (id: string) => directions.flatMap(d => d.roles).find(r => r.id === id)?.label ?? 'Роль не выбрана';
const formatDate = (value: string | null) => {
  if (!value) return 'Без даты';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Без даты' : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
};
const editorFrom = (profile: CandidateProfile): Editor => ({
  profile_id: profile.profile_id,
  version: profile.version,
  name: profile.name,
  direction_id: profile.direction_id,
  role_id: profile.role_id,
  stacks: profile.preferences.stacks?.length ? profile.preferences.stacks : profile.preferences.primary_language ? [profile.preferences.primary_language] : [],
  grades: profile.preferences.seniority ?? [],
  formats: profile.preferences.candidate_filter_version === 1 ? profile.preferences.formats ?? [] : [],
  geography: profile.preferences.candidate_filter_version === 1 ? profile.preferences.geography ?? [] : [],
  vacancyLanguages: profile.preferences.candidate_filter_version === 1 ? profile.preferences.vacancy_languages ?? [] : [],
  skills: (profile.preferences.required_skills ?? []).join(', '),
  excluded: (profile.preferences.excluded_skills ?? []).join(', '),
  delivery: profile.preferences.delivery_mode ?? 'immediate',
});
const profilePayload = (editor: Editor): ProfilePayload => ({
  name: editor.name.trim(),
  direction_id: editor.direction_id,
  role_id: editor.role_id,
  seniority: editor.grades,
  formats: editor.formats,
  geography: editor.geography,
  vacancy_languages: editor.vacancyLanguages,
  stacks: editor.stacks,
  required_skills: editor.skills.split(',').map(s => s.trim()).filter(Boolean),
  excluded_skills: editor.excluded.split(',').map(s => s.trim()).filter(Boolean),
  delivery_mode: editor.delivery,
});

const glyphPaths: Record<string, React.ReactNode> = {
  '▦': <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  '♧': <path d="M6 4.75A1.75 1.75 0 0 1 7.75 3h8.5A1.75 1.75 0 0 1 18 4.75V21l-6-4-6 4z"/>,
  '♙': <><circle cx="12" cy="8" r="4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></>,
  '✦': <><path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9z"/><path d="m19 17 .6 1.4L21 19l-1.4.6L19 21l-.6-1.4L17 19l1.4-.6z"/></>,
  '›': <path d="m9 18 6-6-6-6"/>,
  '→': <path d="M5 12h14m-6-6 6 6-6 6"/>,
  '☾': <path d="M20.3 15.7A8.5 8.5 0 0 1 8.3 3.7 8.5 8.5 0 1 0 20.3 15.7Z"/>,
  '☀': <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/></>,
  '☰': <path d="M4 7h16M4 12h16M4 17h16"/>,
  '⌕': <><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></>,
  'ⓘ': <><circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/></>,
  '↻': <><path d="M20 11a8 8 0 0 0-14.6-4.4L3 9m0-6v6h6"/><path d="M4 13a8 8 0 0 0 14.6 4.4L21 15m0 6v-6h-6"/></>,
  '＋': <path d="M12 5v14M5 12h14"/>,
  '✎': <><path d="M4 16.5V20h3.5L19 8.5 15.5 5 4 16.5Z"/><path d="m13.5 7 3.5 3.5"/></>,
};

function Glyph({ children, size = 19 }: { children: string; size?: number }) {
  return <svg aria-hidden="true" className="ma-live-glyph" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{glyphPaths[children]}</svg>;
}

function MultiSelect({ label, options, selected, onChange, open, onToggle }: {
  label: string;
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
  open: boolean;
  onToggle: () => void;
}) {
  const summary = selected.length
    ? selected.map(value => options.find(option => option.value === value)?.label ?? stackLabel(value)).join(', ')
    : label === 'Грейды' ? 'Любой грейд' : label === 'Стек' ? 'Любой стек' : 'Без ограничений';
  return <div className="ma-field ma-multi-field">
    <span>{label}</span>
    <button type="button" className="ma-multi-trigger" aria-label={label} aria-expanded={open} onClick={onToggle}>
      <span>{summary}</span><span className="ma-multi-chevron"><Glyph size={16}>›</Glyph></span>
    </button>
    {open && <div className="ma-multi-panel" role="group" aria-label={label}>
      <button type="button" className="ma-multi-clear" onClick={() => onChange([])}>Любой · без ограничения</button>
      {options.map(option => <label className="ma-multi-option" key={option.value}>
        <input type="checkbox" checked={selected.includes(option.value)} onChange={() => onChange(selected.includes(option.value) ? selected.filter(value => value !== option.value) : [...selected, option.value])}/>
        <span>{option.label}</span>
      </label>)}
    </div>}
  </div>;
}

export default function LiveMiniapp({ initData, csrfToken = '', onLogout }: { initData: string; csrfToken?: string; onLogout?: () => void }) {
  const api = useMemo(() => new CandidateApi(initData, csrfToken), [initData, csrfToken]);
  const [me, setMe] = useState<CandidateMe | null>(null);
  const [feed, setFeed] = useState<CandidateVacancy[]>([]);
  const [feedTotal, setFeedTotal] = useState(0);
  const [saved, setSaved] = useState<CandidateVacancy[]>([]);
  const [templates, setTemplates] = useState<CandidateTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState('');
  const [run, setRun] = useState<CandidateRun | null>(null);
  const [runs, setRuns] = useState<CandidateRun[]>([]);
  const [results, setResults] = useState<CandidateVacancy[]>([]);
  const [olderDays, setOlderDays] = useState(7);
  const [maxOlderDays, setMaxOlderDays] = useState(30);
  const [screen, setScreen] = useState<Screen>('feed');
  const [bucket, setBucket] = useState<Bucket>('all');
  const [query, setQuery] = useState('');
  const [feedFilters, setFeedFilters] = useState<FeedFilters>({ formats: [], geography: [], vacancyLanguages: [] });
  const [draftFilters, setDraftFilters] = useState<FeedFilters>({ formats: [], geography: [], vacancyLanguages: [] });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [openFeedMulti, setOpenFeedMulti] = useState<keyof FeedFilters | null>(null);
  const [theme, setTheme] = useState<'light' | 'dark'>('dark');
  const [editor, setEditor] = useState<Editor | null>(null);
  const [openMulti, setOpenMulti] = useState<'stacks' | 'grades' | 'formats' | 'geography' | 'vacancyLanguages' | null>(null);
  const [selected, setSelected] = useState<CandidateVacancy | null>(null);
  const [sourceText, setSourceText] = useState<string | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  useEffect(() => {
    let current = true;
    setSourceText(null);
    if (!selected?.vacancy_id || selected.id) { setSourceLoading(false); return; }
    setSourceLoading(true);
    api.vacancyText(selected.vacancy_id).then(result => { if (current) setSourceText(result.text); }).catch(() => { if (current) setSourceText(null); }).finally(() => { if (current) setSourceLoading(false); });
    return () => { current = false; };
  }, [api, selected?.vacancy_id, selected?.id]);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [dismissedTips, setDismissedTips] = useState<string[]>([]);
  useEffect(() => { try { setDismissedTips(JSON.parse(localStorage.getItem('radar-dismissed-tips') || '[]')); } catch { setDismissedTips([]); } }, []);
  const dismissTip = (id: string) => { const next = [...dismissedTips, id]; setDismissedTips(next); localStorage.setItem('radar-dismissed-tips', JSON.stringify(next)); };
  const tips: Record<Screen, string> = { feed: 'Лента обновляется по активному профилю. Нажмите на закладку, чтобы сохранить вакансию.', saved: 'Здесь остаются вакансии, которые вы сохранили.', profiles: 'Активный профиль определяет вакансии в ленте и уведомления.', premium: 'Запустите поиск по активному профилю за последние 7 дней.' };
  const tip = (id: string, text: string) => !dismissedTips.includes(id) && <aside className="ma-context-tip"><span className="ma-context-tip-icon">✳</span><p>{text}</p><button type="button" aria-label="Закрыть подсказку" onClick={() => dismissTip(id)}>×</button></aside>;
  const active = me?.profiles.find(profile => profile.profile_id === me.active_profile_id);
  const editorRole = directions.flatMap(direction => direction.roles).find(role => role.id === editor?.role_id);
  const allRoleStacks = editorRole?.stacks ?? [];
  const languageStacks = allRoleStacks.filter(stack => languageIds.has(stack));
  const editorStacks = editor?.direction_id === 'development' && languageStacks.length ? languageStacks : allRoleStacks;
  const stackOptions = editorStacks.map(value => ({ value, label: stackLabel(value) }));

  const report = (reason: unknown) => setError(reason instanceof Error ? reason.message : 'Не удалось загрузить данные');
  const showToast = (message: string) => setToast(message);

  const refreshFeed = useCallback(async (nextBucket: Bucket = bucket) => {
    const data = await api.feed(nextBucket, 0, 20, feedFilters);
    setFeed(data.items);
    setFeedTotal(data.total);
  }, [api, bucket, feedFilters]);
  const refreshSaved = useCallback(async () => setSaved((await api.saved()).items), [api]);
  const refreshTemplates = useCallback(async () => {
    const data = await api.templates();
    setTemplates(data.items);
    setSelectedTemplate(current => data.items.some(item => item.id === current) ? current : data.items[0]?.id ?? '');
  }, [api]);
  const refreshMe = useCallback(async () => setMe(await api.me()), [api]);
  const refreshRun = useCallback(async (id: string) => {
    const [nextRun, nextResults] = await Promise.all([api.run(id), api.results(id)]);
    setRun(nextRun);
    setRuns(current => [nextRun, ...current.filter(item => item.id !== nextRun.id)]);
    setResults(nextResults.items);
  }, [api]);
  const refreshRuns = useCallback(async () => {
    const data = await api.runs();
    setRuns(data.items);
    setRun(current => current ? data.items.find(item => item.id === current.id) ?? current : data.items[0] ?? null);
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.me(), api.feed('all'), api.saved(), api.templates(), api.runs(), api.feedSettings()])
      .then(([identity, vacancies, archive, availableTemplates, runs, settings]) => {
        if (cancelled) return;
        setMe(identity);
        setFeed(vacancies.items);
        setFeedTotal(vacancies.total);
        setSaved(archive.items);
        setTemplates(availableTemplates.items);
        setSelectedTemplate(availableTemplates.items[0]?.id ?? '');
        setRuns(runs.items);
        setRun(runs.items[0] ?? null);
        setOlderDays(settings.older_days);
        setMaxOlderDays(settings.max_older_days);
        setLoading(false);
      })
      .catch(reason => { if (!cancelled) { report(reason); setLoading(false); } });
    return () => { cancelled = true; };
  }, [api]);

  useEffect(() => {
    if (!run?.id) return;
    void api.results(run.id).then(data => setResults(data.items)).catch(report);
    if (!['queued', 'running'].includes(run.status)) return;
    const interval = window.setInterval(() => { void refreshRun(run.id).catch(report); }, 5000);
    return () => window.clearInterval(interval);
  }, [api, run?.id, run?.status, refreshRun]);
  useEffect(() => {
    if (!runs.some(item => ['queued', 'running'].includes(item.status))) return;
    const interval = window.setInterval(() => { void refreshRuns().catch(report); }, 5000);
    return () => window.clearInterval(interval);
  }, [runs, refreshRuns]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 3000); return () => window.clearTimeout(timer); }, [toast]);
  useEffect(() => {
    if (me?.profiles.length === 0 && !editor) {
      setScreen('profiles');
      setEditor(initialEditor());
    }
  }, [me, editor]);
  useEffect(() => {
    const stored = window.localStorage.getItem('miniapp-live-theme');
    if (stored === 'dark' || stored === 'light') setTheme(stored);
    else setTheme('dark');
  }, []);
  useEffect(() => { window.localStorage.setItem('miniapp-live-theme', theme); }, [theme]);

  const openScreen = (next: Screen) => {
    setScreen(next);
    setMobileMenu(false);
    setError('');
    if (next === 'saved') void refreshSaved().catch(report);
    if (next === 'premium') void refreshTemplates().catch(report);
    if (typeof window.scrollTo === 'function' && !navigator.userAgent.includes('jsdom')) window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  const chooseBucket = (next: Bucket) => {
    setBucket(next);
    setError('');
    void refreshFeed(next).catch(report);
  };
  const updateOlderDays = async (days: number) => {
    try {
      const settings = await api.setOlderDays(days);
      setOlderDays(settings.older_days);
      await refreshFeed('older');
    } catch (reason) { report(reason); }
  };
  const saveProfile = async () => {
    if (!editor?.name.trim() || !editor.role_id) { showToast('Укажите название профиля и роль'); return; }
    setBusy(true);
    try {
      const payload = profilePayload(editor);
      if (editor.profile_id && editor.version) await api.updateProfile(editor.profile_id, { ...payload, version: editor.version });
      else await api.createProfile(payload);
      await Promise.all([refreshMe(), refreshFeed('all'), refreshTemplates()]);
      setEditor(null);
      setScreen('feed');
      showToast('Профиль сохранён');
    } catch (reason) { report(reason); }
    finally { setBusy(false); }
  };
  const activateProfile = async (id: string) => {
    setBusy(true);
    try {
      await api.activateProfile(id);
      await Promise.all([refreshMe(), refreshFeed('all'), refreshTemplates()]);
      setBucket('all');
      showToast('Активный профиль изменён');
    } catch (reason) { report(reason); }
    finally { setBusy(false); }
  };
  const deleteProfile = async () => {
    if (!editor?.profile_id || !window.confirm('Удалить этот профиль? Сохранённые вакансии останутся.')) return;
    setBusy(true);
    try {
      await api.deleteProfile(editor.profile_id);
      await Promise.all([refreshMe(), refreshFeed('all'), refreshTemplates()]);
      setEditor(null);
      showToast('Профиль удалён');
    } catch (reason) { report(reason); }
    finally { setBusy(false); }
  };
  const toggleSaved = async (vacancy: CandidateVacancy, premium = false) => {
    const id = vacancy.vacancy_id ?? vacancy.id;
    if (!id) return;
    setBusy(true);
    try {
      if (vacancy.saved) await api.unsave(id);
      else if (premium && run && vacancy.id) await api.savePremium(run.id, vacancy.id);
      else await api.save(id);
      await Promise.all([refreshSaved(), refreshFeed(bucket)]);
      if (premium && run) setResults((await api.results(run.id)).items);
      setSelected(current => current && (current.vacancy_id ?? current.id) === id ? { ...current, saved: !current.saved } : current);
      showToast(vacancy.saved ? 'Убрано из сохранённых' : 'Вакансия сохранена');
    } catch (reason) { report(reason); }
    finally { setBusy(false); }
  };
  const startPremium = async () => {
    if (!selectedTemplate) return;
    setBusy(true);
    try {
      const intent = `web-${crypto.randomUUID()}`;
      const started = await api.startRun(selectedTemplate, intent);
      setRun(started);
      setRuns(current => [started, ...current.filter(item => item.id !== started.id)]);
      setResults([]);
      showToast('Поиск поставлен в очередь');
    } catch (reason) { report(reason); }
    finally { setBusy(false); }
  };
  const cancelPremium = async () => {
    if (!run) return;
    try {
      const cancelled = await api.cancelRun(run.id);
      setRun(cancelled);
      setRuns(current => [cancelled, ...current.filter(item => item.id !== cancelled.id)]);
      showToast('Поиск отменён');
    }
    catch (reason) { report(reason); }
  };
  const loadMore = async () => {
    try {
      const next = await api.feed(bucket, feed.length, 20, feedFilters);
      setFeed(current => [...current, ...next.items]);
      setFeedTotal(next.total);
    } catch (reason) { report(reason); }
  };
  const filteredFeed = feed.filter(item => `${item.title} ${item.company} ${item.tags.join(' ')}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')));
  const statusText = run?.status === 'queued' ? 'В очереди' : run?.status === 'running' ? premiumPhases[run.phase ?? ''] ?? 'Идёт поиск' : run?.status === 'completed' ? 'Поиск завершён' : run?.status === 'cancelled' ? 'Поиск отменён' : run?.status === 'completed_with_errors' ? 'Завершён с ошибками' : run?.status === 'failed' ? 'Ошибка поиска' : 'Поиск не запущен';
  const isRunActive = !!run && ['queued', 'running'].includes(run.status);
  const hasActiveRun = runs.some(item => ['queued', 'running'].includes(item.status));
  const runStatusTone = run?.status === 'completed' ? 'success' : run?.status === 'completed_with_errors' || run?.status === 'failed' ? 'error' : run?.status === 'cancelled' ? 'muted' : 'active';
  const runMetrics = run?.metrics ?? {};
  const runDate = (value?: string) => value && !Number.isNaN(new Date(value).getTime()) ? new Date(value).toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'Время неизвестно';

  function VacancyCard({ item, premium = false }: { item: CandidateVacancy; premium?: boolean }) {
    const initial = (item.company || 'В')[0].toUpperCase();
    return <article className="ma-vacancy">
      <div className="ma-vacancy-top"><div className="ma-company-icon ma-mint">{initial}</div><div className="ma-vacancy-identity"><span>{item.company || 'Компания'}</span><span className="ma-vacancy-age">{formatDate(item.published_at)}</span></div><button className={`ma-icon-button ma-save-button ${item.saved ? 'is-saved' : ''}`} type="button" aria-label={item.saved ? 'Убрать из сохранённых' : 'Сохранить вакансию'} disabled={busy} onClick={() => void toggleSaved(item, premium)}><Glyph>♧</Glyph></button></div>
      <button className="ma-vacancy-title" type="button" onClick={() => setSelected(item)}>{item.title}</button>
      <div className="ma-vacancy-meta"><span>{item.salary}</span>{item.format && <><i/><span>{item.format}</span></>}{item.location && <><i/><span>{item.location}</span></>}</div>
      {item.summary && <p className="ma-vacancy-summary">{item.summary}</p>}
      <div className="ma-vacancy-bottom"><div className="ma-tags">{item.tags.map(tag => <span key={tag}>{tag}</span>)}</div><button className="ma-open-button" type="button" aria-label={`Открыть ${item.title}`} onClick={() => setSelected(item)}><Glyph>→</Glyph></button></div>
    </article>;
  }

  if (loading) return <div className="miniapp ma-live-loading ma-orbit-splash"><div className="ma-splash-mark"><OrbitMark size={92} animated/></div><span>RADAR</span><p>Загружаем вакансии</p></div>;
  if (!me) return <div className="miniapp ma-live-loading"><strong>Не удалось открыть Mini App</strong><p>{error || 'Откройте приложение заново через бот в Telegram.'}</p><button className="ma-primary-button" type="button" onClick={() => window.location.reload()}>Повторить</button></div>;

  return <div className="miniapp" data-theme={theme} data-onboarding={me.profiles.length === 0 ? 'true' : undefined}><div className="ma-app">
    <aside className={`ma-sidebar ${mobileMenu ? 'ma-sidebar-open' : ''}`} aria-label="Навигация">
      <div className="ma-brand"><OrbitMark size={39}/><div><strong>radar<span className="ma-brand-dot">.</span></strong><small>вакансии для вас</small></div></div>
      <div className="ma-sidebar-section-label">НАВИГАЦИЯ</div>
      <nav className="ma-nav" aria-label="Навигация">{navigation.map(item => <button key={item.id} type="button" className={`ma-nav-item ${screen === item.id ? 'active' : ''}`} onClick={() => openScreen(item.id)}><Glyph>{item.icon}</Glyph><span>{item.label}</span>{item.id === 'saved' && saved.length > 0 && <em>{saved.length}</em>}</button>)}</nav>
      <div className="ma-sidebar-spacer"/>

      <div className="ma-sidebar-footer"><div className="ma-avatar">{(me.user.first_name || 'U')[0].toUpperCase()}</div><div><strong>{me.user.first_name || 'Пользователь'}</strong><small>Candidate Bot</small></div></div>
    </aside>
    <main className="ma-main"><header className="ma-topbar"><button className="ma-mobile-menu ma-icon-button" type="button" aria-label="Открыть меню" onClick={() => setMobileMenu(value => !value)}><Glyph>☰</Glyph></button><div className="ma-breadcrumb"><span>Рабочее пространство</span><Glyph size={13}>›</Glyph><strong>{navigation.find(item => item.id === screen)?.label}</strong></div><div className="ma-topbar-actions"><span className="ma-demo-label"><span/> СВЯЗАНО С БОТОМ</span><button className="ma-theme-button" type="button" aria-label={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'} onClick={() => setTheme(value => value === 'light' ? 'dark' : 'light')}><Glyph>{theme === 'light' ? '☾' : '☀'}</Glyph></button>{onLogout && <button className="ma-logout-button" type="button" onClick={onLogout}>Выйти</button>}<span className="ma-top-avatar">{(me.user.first_name || 'U')[0].toUpperCase()}</span></div></header>
      <div className="ma-content" key={screen}>{error && <div className="ma-live-error" role="alert"><span>{error}</span><button type="button" aria-label="Закрыть ошибку" onClick={() => setError('')}>×</button></div>}
        {screen === 'feed' && <>
          <div className="ma-section-heading ma-feed-heading"><div><span className="ma-overline">ВАКАНСИИ</span><h2>Подходящие вакансии <span className="ma-heading-count">{feedTotal}</span></h2><p>Профиль <button type="button" onClick={() => openScreen('profiles')}><span>{active?.name ?? 'не выбран'}</span> ›</button></p></div><button className="ma-text-action" type="button" aria-label="Обновить вакансии" onClick={() => void refreshFeed().catch(report)}><Glyph size={16}>↻</Glyph><span>Обновить</span></button></div>
          {tip('feed', tips.feed)}<div className="ma-feed-toolbar"><div className="ma-filters" role="tablist" aria-label="Период вакансий">{([['all', 'Для меня'], ['new', 'Новые · 24 ч'], ['older', 'Ранее'], ['undated', 'Без даты']] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={bucket === id} type="button" className={bucket === id ? 'selected' : ''} onClick={() => chooseBucket(id)}>{label}</button>)}</div><label className="ma-search-box"><Glyph size={16}>⌕</Glyph><input aria-label="Поиск по текущей выдаче" placeholder="Поиск в выдаче" value={query} onChange={event => setQuery(event.target.value)}/></label><button type="button" className="ma-feed-filter-button" aria-expanded={filtersOpen} onClick={() => { setDraftFilters(feedFilters); setFiltersOpen(value => !value); }}>Фильтры {Object.values(feedFilters).flat().length > 0 && `· ${Object.values(feedFilters).flat().length}`}</button></div>
          {filtersOpen && <section className="ma-feed-filter-panel" aria-label="Фильтры выдачи"><div className="ma-feed-filter-fields">{([['formats', 'Формат работы'], ['geography', 'Регион'], ['vacancyLanguages', 'Язык вакансии']] as const).map(([key, label]) => <MultiSelect key={key} label={label} options={filterOptions[key]} selected={draftFilters[key]} onChange={values => setDraftFilters(current => ({ ...current, [key]: values }))} open={openFeedMulti === key} onToggle={() => setOpenFeedMulti(current => current === key ? null : key)}/>)}</div><div className="ma-feed-filter-actions"><button type="button" onClick={() => setDraftFilters({ formats: [], geography: [], vacancyLanguages: [] })}>Сбросить</button><button type="button" className="ma-primary-button" onClick={() => { void api.feed(bucket, 0, 20, draftFilters).then(data => { setFeedFilters(draftFilters); setFeed(data.items); setFeedTotal(data.total); setFiltersOpen(false); setOpenFeedMulti(null); }).catch(report); }}>Показать вакансии</button></div><p>Фильтры действуют только в этой выдаче и не меняют профиль.</p></section>}
          {bucket === 'older' && <div className="ma-live-period"><label htmlFor="ma-older-days">Показать за последние</label><select id="ma-older-days" value={olderDays} onChange={event => void updateOlderDays(Number(event.target.value))}>{[3, 7, 14, 30, olderDays].filter((days, index, all) => days <= maxOlderDays && all.indexOf(days) === index).sort((a, b) => a - b).map(days => <option key={days} value={days}>{days} дней</option>)}</select></div>}
          {filteredFeed.length ? <div className="ma-vacancy-grid">{filteredFeed.map(item => <VacancyCard key={item.vacancy_id} item={item}/>)}</div> : <div className="ma-empty"><span><Glyph size={26}>⌕</Glyph></span><h3>Пока нет вакансий по этим условиям</h3><p>{active ? 'Попробуй другой период или дождись новых совпадений.' : 'Сначала создай активный профиль.'}</p><button type="button" onClick={() => openScreen('profiles')}>Мои профили →</button></div>}
          {feed.length < feedTotal && <button className="ma-live-more" type="button" onClick={() => void loadMore()}>Показать ещё</button>}
          <div className="ma-feed-note"><Glyph size={15}>ⓘ</Glyph> В ленте только подтверждённые совпадения активного профиля.</div>
        </>}
        {screen === 'saved' && <><section className="ma-page-heading"><div><span className="ma-overline">АРХИВ</span><h1>Сохранённые <span className="ma-heading-count">{saved.length}</span></h1><p>Вакансии останутся здесь при переключении профиля.</p></div><span className="ma-heading-decoration"><Glyph size={25}>♧</Glyph></span></section>{saved.length ? <div className="ma-vacancy-grid">{saved.map(item => <VacancyCard key={item.vacancy_id} item={item}/>)}</div> : <div className="ma-empty"><span><Glyph size={27}>♧</Glyph></span><h3>Здесь пока пусто</h3><p>Нажми на закладку у интересной вакансии.</p><button type="button" onClick={() => openScreen('feed')}>Смотреть вакансии →</button></div>}</>}
        {screen === 'profiles' && <><section className="ma-page-heading"><div><span className="ma-overline">НАСТРОЙКИ</span><h1>Мои профили</h1><p>Для ленты и уведомлений активным может быть только один профиль.</p></div><button className="ma-primary-button" type="button" onClick={() => setEditor(initialEditor())}><Glyph size={19}>＋</Glyph> Новый профиль</button></section><div className="ma-profile-list">{me.profiles.map(profile => <article className={`ma-profile-card ${profile.is_active ? 'is-active' : ''}`} key={profile.profile_id}><div className="ma-profile-leading"><div className="ma-profile-icon"><Glyph size={22}>♙</Glyph></div><div><div className="ma-profile-title-row"><h2>{profile.name}</h2>{profile.is_active && <span className="ma-active-badge"><span/> Активный</span>}</div><p>{directionLabel(profile.direction_id)} · {roleLabel(profile.role_id)}</p></div></div><div className="ma-profile-chips"><span>{profile.preferences.seniority?.length ? profile.preferences.seniority.map(grade => grade[0].toUpperCase() + grade.slice(1)).join(', ') : 'Любой грейд'}</span>{(profile.preferences.stacks ?? []).length > 0 && <span>{profile.preferences.stacks?.map(stackLabel).join(', ')}</span>}{(profile.preferences.required_skills ?? []).slice(0, 3).map(skill => <span key={skill}>{skill}</span>)}<span>{deliveryLabels[profile.preferences.delivery_mode ?? 'immediate']}</span></div><div className="ma-profile-actions"><button type="button" className="ma-profile-edit" onClick={() => setEditor(editorFrom(profile))}><Glyph size={16}>✎</Glyph> Изменить</button>{!profile.is_active && <button type="button" className="ma-profile-activate" disabled={busy} onClick={() => void activateProfile(profile.profile_id)}>Сделать активным →</button>}</div></article>)}</div>{me.profiles.length === 0 && <div className="ma-empty"><span><Glyph size={27}>♙</Glyph></span><h3>Создай первый профиль</h3><p>Выбери направление, роль и навыки, чтобы получить личную ленту.</p><button type="button" onClick={() => setEditor(initialEditor())}>Создать профиль →</button></div>}</>}
        {screen === 'premium' && <>
          <section className="ma-page-heading ma-premium-heading">
            <div><span className="ma-overline">TELEGRAM</span><h1>Premium поиск</h1><p>Запускай поиск по активному профилю и следи за каждым этапом.</p></div>
            <span className="ma-premium-icon"><Glyph size={23}>✦</Glyph></span>
          </section>
          <div className="ma-premium-workspace">
            <div className="ma-premium-main">
              <section className="ma-premium-card">
                <div className="ma-premium-card-heading"><div><span className="ma-overline">НОВЫЙ ЗАПУСК</span><h2>Настройки поиска</h2></div><span className="ma-run-limit">7 дней <i/> до 50 совпадений</span></div>
                <div className="ma-premium-fields">
                  <div><label>Активный профиль</label><button type="button" className="ma-input-button" onClick={() => openScreen('profiles')}><span className="ma-input-profile"><Glyph size={17}>♙</Glyph>{active?.name ?? 'Выбери профиль'}</span><Glyph size={17}>›</Glyph></button></div>
                  <div><label htmlFor="ma-live-template">Шаблон запроса</label><select id="ma-live-template" value={selectedTemplate} onChange={event => setSelectedTemplate(event.target.value)} disabled={!templates.length}>{templates.length ? templates.map(item => <option key={item.id} value={item.id}>{item.label}</option>) : <option value="">Нет шаблонов</option>}</select></div>
                </div>
                <div className="ma-query-preview"><span>ПОИСКОВЫЙ ЗАПРОС</span><strong>{templates.find(item => item.id === selectedTemplate)?.query ?? 'Выбери активный профиль'}</strong></div>
                <div className="ma-premium-action-row"><button className="ma-primary-button" type="button" disabled={!active || !me.premium_enabled || !selectedTemplate || busy || hasActiveRun} onClick={() => void startPremium()}><Glyph>✦</Glyph> Запустить поиск</button><span>{!me.premium_enabled ? 'Premium поиск сейчас недоступен.' : hasActiveRun ? 'Дождись завершения текущего прохода.' : 'Используется снимок активного профиля на момент запуска.'}</span></div>
              </section>

              {run ? <section className="ma-search-progress-card" aria-labelledby="ma-run-title">
                <div className="ma-run-header"><div><span className="ma-overline">ТЕКУЩИЙ ЗАПУСК</span><h2 id="ma-run-title">{run.query || 'Поиск вакансий'}</h2><p>Запущен {runDate(run.created_at)}</p></div><div className="ma-run-header-actions"><span className={`ma-run-status is-${runStatusTone}`}><i/>{statusText}</span><button className="ma-text-action" type="button" aria-label="Обновить статус" onClick={() => void refreshRun(run.id).catch(report)}><Glyph size={17}>↻</Glyph></button></div></div>
                <div className="ma-run-timeline" aria-label="Этапы поиска">
                  {[['Запрос принят', 0], ['Поиск в Telegram', 1], ['Анализ вакансий', 2], ['Готово', 3]].map(([label, index]) => {
                    const step = Number(index);
                    const phase = run.phase ?? '';
                    const currentStep = !isRunActive ? 3 : run.status === 'queued' ? 0 : phase === 'analyzing' || phase === 'llm_retry' ? 2 : phase === 'quota_check' ? 0 : 1;
                    const stepState = step < currentStep || !isRunActive && run.status === 'completed' ? 'done' : step === currentStep && isRunActive ? 'current' : !isRunActive && run.status !== 'completed' && step === 3 ? 'stopped' : '';
                    return <div className={`ma-run-step ${stepState}`} key={label}><span>{stepState === 'done' ? '✓' : step + 1}</span><b>{label}</b></div>;
                  })}
                </div>
                {isRunActive && <div className="ma-run-live-note"><span className="ma-pulse-dot"/> Обновляется автоматически каждые 5 секунд <button type="button" onClick={() => void cancelPremium()}>Отменить</button></div>}
                {run.error_reason && <div className="ma-run-error" role="alert"><Glyph size={18}>ⓘ</Glyph><div><strong>{run.status === 'completed_with_errors' ? 'Поиск завершён не полностью' : 'Не удалось выполнить поиск'}</strong><p>{premiumReasons[run.error_reason] ?? premiumReasons.premium_search_failed}</p></div></div>}
                <div className="ma-run-metrics" aria-label="Статистика запуска">
                  {[["Просмотрено", runMetrics.scanned ?? 0], ["Найдено", runMetrics.found ?? 0], ["В анализе", runMetrics.pending ?? 0], ["Подтверждено", run.confirmed_count], ["Отклонено", runMetrics.rejected ?? 0], ["Ошибки", runMetrics.errors ?? 0]].map(([label, value]) => <div className="ma-run-metric" key={String(label)}><span>{label}</span><strong>{Number(value).toLocaleString('ru-RU')}</strong></div>)}
                </div>
                {run.source_states && Object.keys(run.source_states).length > 0 && <div className="ma-source-status"><span>ИСТОЧНИКИ</span>{Object.entries(run.source_states).map(([source, state]) => <div key={source}><i className={state.status === 'completed' ? 'is-ok' : state.status === 'failed' ? 'is-error' : ''}/><strong>{source === 'premium' ? 'Telegram Premium' : source}</strong><small>{state.reason ? premiumReasons[state.reason] ?? state.reason : state.status === 'completed' ? 'Завершён' : state.status === 'failed' ? 'Ошибка' : state.status === 'running' ? 'В работе' : state.status === 'queued' ? 'В очереди' : state.status === 'unavailable' ? 'Недоступен' : state.status}</small></div>)}</div>}
              </section> : <section className="ma-run-empty"><span><Glyph size={22}>⌕</Glyph></span><div><strong>Запусков пока нет</strong><p>После старта здесь появится ход поиска и его результаты.</p></div></section>}

              {run && <section className="ma-run-history" aria-labelledby="ma-history-title"><div className="ma-run-history-heading"><div><span className="ma-overline">ИСТОРИЯ</span><h2 id="ma-history-title">Последние запуски</h2></div><button className="ma-text-action" type="button" onClick={() => void refreshRuns().catch(report)}><Glyph size={16}>↻</Glyph><span>Обновить</span></button></div>
                {runs.length ? <div className="ma-run-history-list">{runs.map(item => <button className={`ma-history-item ${item.id === run.id ? 'is-selected' : ''}`} type="button" key={item.id} aria-current={item.id === run.id ? 'true' : undefined} onClick={() => { setRun(item); void api.results(item.id).then(data => setResults(data.items)).catch(report); }}><span className={`ma-history-dot is-${item.status === 'completed' ? 'success' : item.status === 'failed' || item.status === 'completed_with_errors' ? 'error' : ['queued', 'running'].includes(item.status) ? 'active' : 'muted'}`}/><span className="ma-history-copy"><strong>{item.query || 'Поиск вакансий'}</strong><small>{runDate(item.created_at)} · {item.confirmed_count} подтверждено</small></span><span className="ma-history-label">{item.status === 'completed' ? 'Готово' : item.status === 'completed_with_errors' ? 'С ошибками' : item.status === 'failed' ? 'Ошибка' : item.status === 'cancelled' ? 'Отменён' : item.status === 'queued' ? 'В очереди' : item.status === 'running' ? 'В работе' : item.status}</span><Glyph size={16}>›</Glyph></button>)}</div> : <p className="ma-history-empty">История появится после первого запуска.</p>}
              </section>}

              {run && <section className="ma-premium-results-section"><div className="ma-section-heading ma-premium-results-heading"><div><span className="ma-overline">РЕЗУЛЬТАТЫ ЗАПУСКА</span><h2>Подтверждённые вакансии <span className="ma-heading-count">{results.length}</span></h2></div></div>{results.length > 0 ? <div className="ma-vacancy-grid ma-premium-results">{results.map(item => <VacancyCard key={item.id} item={item} premium/>)}</div> : <div className="ma-results-empty">{isRunActive ? 'Подтверждённые совпадения появятся здесь по мере обработки.' : 'В этом запуске нет подтверждённых вакансий.'}</div>}</section>}
            </div>
            <aside className="ma-premium-aside"><div className="ma-how-card"><span className="ma-how-icon"><Glyph>✦</Glyph></span><h3>Параметры прохода</h3><div className="ma-how-step"><b>7</b><p>Последних дней истории Telegram.</p></div><div className="ma-how-step"><b>50</b><p>Подтверждённых совпадений максимум.</p></div><div className="ma-how-step"><b>↻</b><p>Статус и счётчики обновляются автоматически во время работы.</p></div></div></aside>
          </div>
        </>}
      </div>
    </main>
    <nav className="ma-mobile-nav" aria-label="Основные разделы">{navigation.map(item => <button key={item.id} type="button" className={screen === item.id ? 'active' : ''} onClick={() => openScreen(item.id)}><Glyph size={20}>{item.icon}</Glyph><span>{item.id === 'feed' ? 'Лента' : item.id === 'saved' ? 'Сохранено' : item.id === 'profiles' ? 'Профили' : 'Premium'}</span></button>)}</nav>
  </div>
    {editor && <div className="ma-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget && me.profiles.length > 0) setEditor(null); }}><section className="ma-dialog ma-profile-dialog" role="dialog" aria-modal="true" aria-labelledby="ma-editor-title"><div className="ma-dialog-head"><div><span className="ma-overline">{me.profiles.length === 0 ? 'ШАГ 1 · ВАШ ПРОФИЛЬ' : 'НАСТРОЙКИ ПОИСКА'}</span><h2 id="ma-editor-title">{me.profiles.length === 0 ? 'Что вам подходит?' : editor.profile_id ? 'Изменить профиль' : 'Новый профиль'}</h2><p>Укажите роль, навыки и частоту уведомлений.</p></div>{me.profiles.length > 0 && <button type="button" className="ma-icon-button" aria-label="Закрыть" onClick={() => setEditor(null)}>×</button>}</div><div className="ma-dialog-body"><div className="ma-form-grid"><label className="ma-field ma-field-full">Название профиля<input value={editor.name} onChange={event => setEditor({ ...editor, name: event.target.value })} placeholder="Например, Go Backend" maxLength={80}/></label><label className="ma-field">Направление<select value={editor.direction_id} onChange={event => { setEditor({ ...editor, direction_id: event.target.value, role_id: '', stacks: [] }); setOpenMulti(null); }}>{directions.map(direction => <option key={direction.id} value={direction.id}>{direction.label}</option>)}</select></label><label className="ma-field">{editor.direction_id === 'development' ? 'Вид разработки' : 'Роль'}<select value={editor.role_id} onChange={event => { setEditor({ ...editor, role_id: event.target.value, stacks: [] }); setOpenMulti(null); }}><option value="">Выбери роль</option>{directions.find(direction => direction.id === editor.direction_id)?.roles.map(role => <option key={role.id} value={role.id}>{role.label}</option>)}</select></label>{editor.role_id && <MultiSelect label={editor.direction_id === 'development' ? 'Язык разработки' : 'Стек'} options={stackOptions} selected={editor.stacks} onChange={stacks => setEditor({ ...editor, stacks })} open={openMulti === 'stacks'} onToggle={() => setOpenMulti(current => current === 'stacks' ? null : 'stacks')}/>}<MultiSelect label="Грейды" options={grades.map(grade => ({ value: grade.toLowerCase(), label: grade }))} selected={editor.grades} onChange={selectedGrades => setEditor({ ...editor, grades: selectedGrades })} open={openMulti === 'grades'} onToggle={() => setOpenMulti(current => current === 'grades' ? null : 'grades')}/><MultiSelect label="Формат работы" options={filterOptions.formats} selected={editor.formats} onChange={values => setEditor({ ...editor, formats: values })} open={openMulti === 'formats'} onToggle={() => setOpenMulti(current => current === 'formats' ? null : 'formats')}/><MultiSelect label="Регион" options={filterOptions.geography} selected={editor.geography} onChange={values => setEditor({ ...editor, geography: values })} open={openMulti === 'geography'} onToggle={() => setOpenMulti(current => current === 'geography' ? null : 'geography')}/><MultiSelect label="Язык вакансии" options={filterOptions.vacancyLanguages} selected={editor.vacancyLanguages} onChange={values => setEditor({ ...editor, vacancyLanguages: values })} open={openMulti === 'vacancyLanguages'} onToggle={() => setOpenMulti(current => current === 'vacancyLanguages' ? null : 'vacancyLanguages')}/><label className="ma-field">Уведомления<select value={editor.delivery} onChange={event => setEditor({ ...editor, delivery: event.target.value as Editor['delivery'] })}><option value="immediate">Сразу</option><option value="hourly">Раз в час</option><option value="manual">Только вручную</option></select></label><label className="ma-field ma-field-full">Ключевые навыки <small>через запятую · достаточно одного совпадения</small><input value={editor.skills} onChange={event => setEditor({ ...editor, skills: event.target.value })} placeholder="Go, PostgreSQL, Kafka"/></label><label className="ma-field ma-field-full">Слова-исключения <small>через запятую</small><input value={editor.excluded} onChange={event => setEditor({ ...editor, excluded: event.target.value })} placeholder="стажировка, офис"/></label></div></div><div className="ma-dialog-footer">{editor.profile_id && <button className="ma-delete-button" type="button" onClick={() => void deleteProfile()}>Удалить</button>}{me.profiles.length > 0 && <button className="ma-secondary-button" type="button" onClick={() => setEditor(null)}>Отмена</button>}<button className="ma-primary-button" type="button" disabled={busy} onClick={() => void saveProfile()}>{me.profiles.length === 0 ? 'Открыть вакансии →' : 'Сохранить профиль →'}</button></div></section></div>}
    {selected && <div className="ma-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="ma-dialog ma-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="ma-detail-title"><div className="ma-dialog-head"><div className="ma-detail-company"><div className="ma-company-icon ma-mint">{(selected.company || 'В')[0].toUpperCase()}</div><div><strong>{selected.company || 'Компания'}</strong><small>{formatDate(selected.published_at)}</small></div></div><button type="button" className="ma-icon-button" aria-label="Закрыть" onClick={() => setSelected(null)}>×</button></div><div className="ma-dialog-body"><span className="ma-overline">ПОДТВЕРЖДЁННАЯ ВАКАНСИЯ</span><h2 id="ma-detail-title">{selected.title}</h2><p className="ma-detail-salary">{selected.salary}</p><div className="ma-detail-facts">{selected.grade && <span>{selected.grade}</span>}{selected.format && <span>{selected.format}</span>}{selected.location && <span>{selected.location}</span>}</div><div className="ma-detail-divider"/><h3>О вакансии</h3><p className="ma-detail-summary">{selected.summary && selected.summary !== 'Не указано' ? selected.summary : 'Краткое описание не указано.'}</p><h3>Полный текст источника</h3><p className="ma-source-text">{sourceLoading ? 'Загружаем описание…' : sourceText || 'Полный текст недоступен. Проверьте источник вакансии.'}</p><h3>Навыки</h3><div className="ma-tags">{selected.tags.map(tag => <span key={tag}>{tag}</span>)}</div></div><div className="ma-dialog-footer"><button className="ma-secondary-button" type="button" onClick={() => void toggleSaved(selected, !!selected.id)}>{selected.saved ? 'Сохранено' : 'Сохранить'}</button>{selected.post_link.startsWith('https://') && <a className="ma-primary-button" href={selected.post_link} target="_blank" rel="noreferrer">Открыть источник ↗</a>}</div></section></div>}
    {toast && <div className="ma-toast" role="status">✓ {toast}</div>}
  </div>;
}
