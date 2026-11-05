'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import catalog from '../../../src/tg_vacancy_bot/data/it_roles.v1.json';
import {
  CandidateApi,
  type CandidateMe,
  type CandidateProfile,
  type CandidateRun,
  type CandidateTemplate,
  type CandidateVacancy,
  type ProfilePayload,
} from '../../lib/candidate-api';

type Screen = 'feed' | 'saved' | 'profiles' | 'premium';
type Bucket = 'all' | 'new' | 'older' | 'undated';
type Editor = { profile_id?: string; version?: number; name: string; direction_id: string; role_id: string; grade: string; skills: string; excluded: string; delivery: 'immediate' | 'hourly' | 'manual' };

const directions = catalog.directions.map(direction => ({
  id: direction.id,
  label: direction.label,
  roles: direction.specializations.flatMap(s => s.roles.map(role => ({ id: role.id, label: role.label }))),
}));
const navigation: { id: Screen; label: string; icon: string }[] = [
  { id: 'feed', label: 'Лента вакансий', icon: '▦' },
  { id: 'saved', label: 'Сохранённые', icon: '♧' },
  { id: 'profiles', label: 'Мои профили', icon: '♙' },
  { id: 'premium', label: 'Premium поиск', icon: '✦' },
];
const deliveryLabels = { immediate: 'Сразу', hourly: 'Раз в час', manual: 'Только вручную' };
const grades = ['Любой', 'Intern', 'Junior', 'Middle', 'Senior', 'Staff', 'Lead', 'Head'];
const initialEditor = (): Editor => ({ name: '', direction_id: 'development', role_id: '', grade: 'Любой', skills: '', excluded: '', delivery: 'immediate' });
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
  grade: profile.preferences.seniority?.[0] ? profile.preferences.seniority[0][0].toUpperCase() + profile.preferences.seniority[0].slice(1) : 'Любой',
  skills: (profile.preferences.required_skills ?? []).join(', '),
  excluded: (profile.preferences.excluded_skills ?? []).join(', '),
  delivery: profile.preferences.delivery_mode ?? 'immediate',
});
const profilePayload = (editor: Editor): ProfilePayload => ({
  name: editor.name.trim(),
  direction_id: editor.direction_id,
  role_id: editor.role_id,
  seniority: editor.grade === 'Любой' ? [] : [editor.grade.toLowerCase()],
  required_skills: editor.skills.split(',').map(s => s.trim()).filter(Boolean),
  excluded_skills: editor.excluded.split(',').map(s => s.trim()).filter(Boolean),
  delivery_mode: editor.delivery,
});

function Glyph({ children, size = 19 }: { children: React.ReactNode; size?: number }) {
  return <span aria-hidden="true" className="ma-live-glyph" style={{ fontSize: size }}>{children}</span>;
}

export default function LiveMiniapp({ initData }: { initData: string }) {
  const api = useMemo(() => new CandidateApi(initData), [initData]);
  const [me, setMe] = useState<CandidateMe | null>(null);
  const [feed, setFeed] = useState<CandidateVacancy[]>([]);
  const [feedTotal, setFeedTotal] = useState(0);
  const [saved, setSaved] = useState<CandidateVacancy[]>([]);
  const [templates, setTemplates] = useState<CandidateTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState('');
  const [run, setRun] = useState<CandidateRun | null>(null);
  const [results, setResults] = useState<CandidateVacancy[]>([]);
  const [olderDays, setOlderDays] = useState(7);
  const [maxOlderDays, setMaxOlderDays] = useState(30);
  const [screen, setScreen] = useState<Screen>('feed');
  const [bucket, setBucket] = useState<Bucket>('all');
  const [query, setQuery] = useState('');
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [editor, setEditor] = useState<Editor | null>(null);
  const [selected, setSelected] = useState<CandidateVacancy | null>(null);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const active = me?.profiles.find(profile => profile.profile_id === me.active_profile_id);

  const report = (reason: unknown) => setError(reason instanceof Error ? reason.message : 'Не удалось загрузить данные');
  const showToast = (message: string) => setToast(message);

  const refreshFeed = useCallback(async (nextBucket: Bucket = bucket) => {
    const data = await api.feed(nextBucket);
    setFeed(data.items);
    setFeedTotal(data.total);
  }, [api, bucket]);
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
    setResults(nextResults.items);
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
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 3000); return () => window.clearTimeout(timer); }, [toast]);
  useEffect(() => {
    const stored = window.localStorage.getItem('miniapp-live-theme');
    if (stored === 'dark' || stored === 'light') setTheme(stored);
    else if ((window as Window & { Telegram?: { WebApp?: { colorScheme?: string } } }).Telegram?.WebApp?.colorScheme === 'dark') setTheme('dark');
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
      setResults([]);
      showToast('Поиск поставлен в очередь');
    } catch (reason) { report(reason); }
    finally { setBusy(false); }
  };
  const cancelPremium = async () => {
    if (!run) return;
    try { setRun(await api.cancelRun(run.id)); showToast('Поиск отменён'); }
    catch (reason) { report(reason); }
  };
  const loadMore = async () => {
    try {
      const next = await api.feed(bucket, feed.length);
      setFeed(current => [...current, ...next.items]);
      setFeedTotal(next.total);
    } catch (reason) { report(reason); }
  };
  const filteredFeed = feed.filter(item => `${item.title} ${item.company} ${item.tags.join(' ')}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')));
  const statusText = run?.status === 'queued' ? 'В очереди' : run?.status === 'running' ? 'Идёт поиск' : run?.status === 'completed' ? 'Поиск завершён' : run?.status === 'cancelled' ? 'Поиск отменён' : run?.status === 'completed_with_errors' ? 'Завершён с ошибками' : 'Поиск не запущен';

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

  if (loading) return <div className="miniapp ma-live-loading"><span className="ma-brand-mark"><span/><span/><span/><span/></span><strong>Загружаем твои вакансии…</strong></div>;
  if (!me) return <div className="miniapp ma-live-loading"><strong>Не удалось открыть Mini App</strong><p>{error || 'Откройте приложение заново через бот в Telegram.'}</p><button className="ma-primary-button" type="button" onClick={() => window.location.reload()}>Повторить</button></div>;

  return <div className="miniapp" data-theme={theme}><div className="ma-app">
    <aside className={`ma-sidebar ${mobileMenu ? 'ma-sidebar-open' : ''}`} aria-label="Навигация">
      <div className="ma-brand"><div className="ma-brand-mark"><span/><span/><span/><span/></div><div><strong>radar<span className="ma-brand-dot">.</span></strong><small>работа по твоим правилам</small></div></div>
      <div className="ma-sidebar-section-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav className="ma-nav" aria-label="Навигация">{navigation.map(item => <button key={item.id} type="button" className={`ma-nav-item ${screen === item.id ? 'active' : ''}`} onClick={() => openScreen(item.id)}><Glyph>{item.icon}</Glyph><span>{item.label}</span>{item.id === 'saved' && saved.length > 0 && <em>{saved.length}</em>}</button>)}</nav>
      <div className="ma-sidebar-spacer"/>
      <div className="ma-sidebar-guide"><span className="ma-guide-icon"><Glyph>✦</Glyph></span><strong>Поиск под тебя</strong><p>Активный профиль управляет личной лентой и уведомлениями.</p><button type="button" onClick={() => openScreen('profiles')}>Настроить профиль →</button></div>
      <div className="ma-sidebar-footer"><div className="ma-avatar">{(me.user.first_name || 'U')[0].toUpperCase()}</div><div><strong>{me.user.first_name || 'Пользователь'}</strong><small>Candidate Bot</small></div></div>
    </aside>
    <main className="ma-main"><header className="ma-topbar"><button className="ma-mobile-menu ma-icon-button" type="button" aria-label="Открыть меню" onClick={() => setMobileMenu(value => !value)}><Glyph>☰</Glyph></button><div className="ma-breadcrumb"><span>Рабочее пространство</span><Glyph size={13}>›</Glyph><strong>{navigation.find(item => item.id === screen)?.label}</strong></div><div className="ma-topbar-actions"><span className="ma-demo-label"><span/> СВЯЗАНО С БОТОМ</span><button className="ma-theme-button" type="button" aria-label={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'} onClick={() => setTheme(value => value === 'light' ? 'dark' : 'light')}><Glyph>{theme === 'light' ? '☾' : '☀'}</Glyph></button><span className="ma-top-avatar">{(me.user.first_name || 'U')[0].toUpperCase()}</span></div></header>
      <div className="ma-content">{error && <div className="ma-live-error" role="alert"><span>{error}</span><button type="button" aria-label="Закрыть ошибку" onClick={() => setError('')}>×</button></div>}
        {screen === 'feed' && <><section className="ma-hero"><div className="ma-hero-copy"><div className="ma-eyebrow"><span/> ТВОЯ КАРЬЕРА, ТВОИ ПРАВИЛА</div><h1>Хорошая работа<br/><em>находится здесь.</em></h1><p>{active ? `Подборка для профиля «${active.name}». Новые подтверждённые совпадения появятся в ленте.` : 'Создай профиль и выбери его активным, чтобы получать подходящие вакансии.'}</p><button className="ma-hero-cta" type="button" onClick={() => openScreen('profiles')}>Настроить поиск <Glyph size={17}>→</Glyph></button></div><div className="ma-hero-art" aria-hidden="true"><div className="ma-art-orbit ma-art-orbit-one"/><div className="ma-art-orbit ma-art-orbit-two"/><div className="ma-art-glow"/><div className="ma-art-card ma-art-card-front"><div className="ma-art-card-line"><span className="ma-art-company">✦</span><span><b>Подходящая вакансия</b><small>Для твоего профиля</small></span></div><div className="ma-art-match">✦ Проверено по условиям профиля</div></div></div></section>
          <div className="ma-section-heading ma-feed-heading"><div><span className="ma-overline">ТВОЯ ЛЕНТА</span><h2>Подходящие вакансии <span className="ma-heading-count">{feedTotal}</span></h2><p>Результаты для профиля <button type="button" onClick={() => openScreen('profiles')}><span>{active?.name ?? 'не выбран'}</span> ›</button></p></div><button className="ma-text-action" type="button" aria-label="Обновить вакансии" onClick={() => void refreshFeed().catch(report)}><Glyph size={16}>↻</Glyph><span>Обновить</span></button></div>
          <div className="ma-feed-toolbar"><div className="ma-filters" role="tablist" aria-label="Период вакансий">{([['all', 'Для меня'], ['new', 'Новые · 24 ч'], ['older', 'Ранее'], ['undated', 'Без даты']] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={bucket === id} type="button" className={bucket === id ? 'selected' : ''} onClick={() => chooseBucket(id)}>{label}</button>)}</div><label className="ma-search-box"><Glyph size={16}>⌕</Glyph><input aria-label="Поиск по текущей выдаче" placeholder="Поиск в выдаче" value={query} onChange={event => setQuery(event.target.value)}/></label></div>
          {bucket === 'older' && <div className="ma-live-period"><label htmlFor="ma-older-days">Показать за последние</label><select id="ma-older-days" value={olderDays} onChange={event => void updateOlderDays(Number(event.target.value))}>{[3, 7, 14, 30, olderDays].filter((days, index, all) => days <= maxOlderDays && all.indexOf(days) === index).sort((a, b) => a - b).map(days => <option key={days} value={days}>{days} дней</option>)}</select></div>}
          {filteredFeed.length ? <div className="ma-vacancy-grid">{filteredFeed.map(item => <VacancyCard key={item.vacancy_id} item={item}/>)}</div> : <div className="ma-empty"><span><Glyph size={26}>⌕</Glyph></span><h3>Пока нет вакансий по этим условиям</h3><p>{active ? 'Попробуй другой период или дождись новых совпадений.' : 'Сначала создай активный профиль.'}</p><button type="button" onClick={() => openScreen('profiles')}>Мои профили →</button></div>}
          {feed.length < feedTotal && <button className="ma-live-more" type="button" onClick={() => void loadMore()}>Показать ещё</button>}
          <div className="ma-feed-note"><Glyph size={15}>ⓘ</Glyph> В ленте только подтверждённые совпадения активного профиля.</div>
        </>}
        {screen === 'saved' && <><section className="ma-page-heading"><div><span className="ma-overline">ТВОЯ КОЛЛЕКЦИЯ</span><h1>Сохранённые <span className="ma-heading-count">{saved.length}</span></h1><p>Вакансии останутся здесь при переключении профиля.</p></div><span className="ma-heading-decoration"><Glyph size={25}>♧</Glyph></span></section>{saved.length ? <div className="ma-vacancy-grid">{saved.map(item => <VacancyCard key={item.vacancy_id} item={item}/>)}</div> : <div className="ma-empty"><span><Glyph size={27}>♧</Glyph></span><h3>Здесь пока пусто</h3><p>Нажми на закладку у интересной вакансии.</p><button type="button" onClick={() => openScreen('feed')}>Смотреть вакансии →</button></div>}</>}
        {screen === 'profiles' && <><section className="ma-page-heading"><div><span className="ma-overline">НАСТРОЙ ПОИСК ПОД СЕБЯ</span><h1>Мои профили</h1><p>Для ленты и уведомлений активным может быть только один профиль.</p></div><button className="ma-primary-button" type="button" onClick={() => setEditor(initialEditor())}><Glyph size={19}>＋</Glyph> Новый профиль</button></section><div className="ma-profile-list">{me.profiles.map(profile => <article className={`ma-profile-card ${profile.is_active ? 'is-active' : ''}`} key={profile.profile_id}><div className="ma-profile-leading"><div className="ma-profile-icon"><Glyph size={22}>♙</Glyph></div><div><div className="ma-profile-title-row"><h2>{profile.name}</h2>{profile.is_active && <span className="ma-active-badge"><span/> Активный</span>}</div><p>{directionLabel(profile.direction_id)} · {roleLabel(profile.role_id)}</p></div></div><div className="ma-profile-chips"><span>{profile.preferences.seniority?.length ? profile.preferences.seniority.join(', ') : 'Любой грейд'}</span>{(profile.preferences.required_skills ?? []).slice(0, 3).map(skill => <span key={skill}>{skill}</span>)}<span>{deliveryLabels[profile.preferences.delivery_mode ?? 'immediate']}</span></div><div className="ma-profile-actions"><button type="button" className="ma-profile-edit" onClick={() => setEditor(editorFrom(profile))}>✎ Изменить</button>{!profile.is_active && <button type="button" className="ma-profile-activate" disabled={busy} onClick={() => void activateProfile(profile.profile_id)}>Сделать активным →</button>}</div></article>)}</div>{me.profiles.length === 0 && <div className="ma-empty"><span><Glyph size={27}>♙</Glyph></span><h3>Создай первый профиль</h3><p>Выбери направление, роль и навыки, чтобы получить личную ленту.</p><button type="button" onClick={() => setEditor(initialEditor())}>Создать профиль →</button></div>}<div className="ma-tip"><span><Glyph>♧</Glyph></span><div><strong>Уведомления работают по активному профилю</strong><p>Выбери, как часто получать новые совпадения: сразу, раз в час или только при открытии ленты.</p></div></div></>}
        {screen === 'premium' && <><section className="ma-page-heading ma-premium-heading"><div><span className="ma-overline">РАСШИРЕННЫЙ ПОИСК</span><h1>Premium поиск <span className="ma-title-spark">✦</span></h1><p>Проверенные вакансии из истории Telegram по активному профилю.</p></div><span className="ma-premium-icon"><Glyph size={25}>✦</Glyph></span></section><div className="ma-premium-layout"><div className="ma-premium-main"><div className="ma-premium-card"><div className="ma-card-step"><span>01</span><div><h2>Проверь настройки поиска</h2><p>Поиск использует снимок активного профиля на момент запуска.</p></div></div><div className="ma-premium-fields"><div><label>Активный профиль</label><button type="button" className="ma-input-button" onClick={() => openScreen('profiles')}><span>♙ {active?.name ?? 'Выбери профиль'}</span><Glyph size={17}>›</Glyph></button></div><div><label htmlFor="ma-live-template">Шаблон запроса</label><select id="ma-live-template" value={selectedTemplate} onChange={event => setSelectedTemplate(event.target.value)} disabled={!templates.length}>{templates.length ? templates.map(item => <option key={item.id} value={item.id}>{item.label}</option>) : <option value="">Нет шаблонов</option>}</select></div></div><div className="ma-query-preview"><span>ПРЕДПРОСМОТР ЗАПРОСА</span><strong>{templates.find(item => item.id === selectedTemplate)?.query ?? 'Выбери активный профиль'}</strong><p>Telegram Premium · 7 дней · до 50 подтверждённых совпадений</p></div><div className="ma-premium-action-row"><button className="ma-primary-button" type="button" disabled={!active || !me.premium_enabled || !selectedTemplate || busy || !!run && ['queued', 'running'].includes(run.status)} onClick={() => void startPremium()}><Glyph>✦</Glyph> Запустить поиск</button><span>{me.premium_enabled ? '1 поиск за 7 дней; результаты доступны до завершения' : 'Premium поиск сейчас недоступен'}</span></div></div>{run && <div className="ma-search-progress-card"><div className="ma-progress-top"><span className="ma-progress-icon"><Glyph>⌕</Glyph></span><div><strong>{statusText}</strong><p>Подтверждено вакансий: {run.confirmed_count}</p></div><button className="ma-text-action" type="button" aria-label="Обновить поиск" onClick={() => void refreshRun(run.id).catch(report)}>↻</button></div>{['queued', 'running'].includes(run.status) && <><div className="ma-progress-track"><div className="ma-live-progress-indeterminate"/></div><button type="button" onClick={() => void cancelPremium()}>Отменить поиск</button></>}</div>}{results.length > 0 && <><div className="ma-section-heading ma-premium-results-heading"><div><span className="ma-overline">РЕЗУЛЬТАТЫ</span><h2>Найденные вакансии <span className="ma-heading-count">{results.length}</span></h2></div></div><div className="ma-vacancy-grid ma-premium-results">{results.map(item => <VacancyCard key={item.id} item={item} premium/>)}</div></>}</div><aside className="ma-premium-aside"><div className="ma-how-card"><span className="ma-how-icon"><Glyph>✦</Glyph></span><h3>Как это работает</h3><div className="ma-how-step"><b>1</b><p>Выбери активный профиль и шаблон запроса.</p></div><div className="ma-how-step"><b>2</b><p>Проверь запрос и запусти поиск по истории.</p></div><div className="ma-how-step"><b>3</b><p>Изучи результаты и сохрани интересное.</p></div></div><div className="ma-premium-notice"><Glyph>ⓘ</Glyph><p>Поиск выполняет существующий live worker. Уведомления по найденным вакансиям отдельно не запускаются.</p></div></aside></div></>}
      </div>
    </main>
    <nav className="ma-mobile-nav" aria-label="Основные разделы">{navigation.map(item => <button key={item.id} type="button" className={screen === item.id ? 'active' : ''} onClick={() => openScreen(item.id)}><Glyph size={20}>{item.icon}</Glyph><span>{item.id === 'feed' ? 'Лента' : item.id === 'saved' ? 'Сохранено' : item.id === 'profiles' ? 'Профили' : 'Premium'}</span></button>)}</nav>
  </div>
    {editor && <div className="ma-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setEditor(null); }}><section className="ma-dialog ma-profile-dialog" role="dialog" aria-modal="true" aria-labelledby="ma-editor-title"><div className="ma-dialog-head"><div><span className="ma-overline">НАСТРОЙКИ ПОИСКА</span><h2 id="ma-editor-title">{editor.profile_id ? 'Изменить профиль' : 'Новый профиль'}</h2><p>Условия будут использоваться ботом и Mini App.</p></div><button type="button" className="ma-icon-button" aria-label="Закрыть" onClick={() => setEditor(null)}>×</button></div><div className="ma-dialog-body"><div className="ma-form-grid"><label className="ma-field ma-field-full">Название профиля<input value={editor.name} onChange={event => setEditor({ ...editor, name: event.target.value })} placeholder="Например, Go Backend" maxLength={80}/></label><label className="ma-field">Направление<select value={editor.direction_id} onChange={event => setEditor({ ...editor, direction_id: event.target.value, role_id: '' })}>{directions.map(direction => <option key={direction.id} value={direction.id}>{direction.label}</option>)}</select></label><label className="ma-field">Роль<select value={editor.role_id} onChange={event => setEditor({ ...editor, role_id: event.target.value })}><option value="">Выбери роль</option>{directions.find(direction => direction.id === editor.direction_id)?.roles.map(role => <option key={role.id} value={role.id}>{role.label}</option>)}</select></label><label className="ma-field">Грейд<select value={editor.grade} onChange={event => setEditor({ ...editor, grade: event.target.value })}>{grades.map(grade => <option key={grade}>{grade}</option>)}</select></label><label className="ma-field">Уведомления<select value={editor.delivery} onChange={event => setEditor({ ...editor, delivery: event.target.value as Editor['delivery'] })}><option value="immediate">Сразу</option><option value="hourly">Раз в час</option><option value="manual">Только вручную</option></select></label><label className="ma-field ma-field-full">Ключевые навыки <small>через запятую · достаточно одного совпадения</small><input value={editor.skills} onChange={event => setEditor({ ...editor, skills: event.target.value })} placeholder="Go, PostgreSQL, Kafka"/></label><label className="ma-field ma-field-full">Слова-исключения <small>через запятую</small><input value={editor.excluded} onChange={event => setEditor({ ...editor, excluded: event.target.value })} placeholder="стажировка, офис"/></label></div></div><div className="ma-dialog-footer">{editor.profile_id && <button className="ma-delete-button" type="button" onClick={() => void deleteProfile()}>Удалить</button>}<button className="ma-secondary-button" type="button" onClick={() => setEditor(null)}>Отмена</button><button className="ma-primary-button" type="button" disabled={busy} onClick={() => void saveProfile()}>Сохранить профиль →</button></div></section></div>}
    {selected && <div className="ma-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="ma-dialog ma-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="ma-detail-title"><div className="ma-dialog-head"><div className="ma-detail-company"><div className="ma-company-icon ma-mint">{(selected.company || 'В')[0].toUpperCase()}</div><div><strong>{selected.company || 'Компания'}</strong><small>{formatDate(selected.published_at)}</small></div></div><button type="button" className="ma-icon-button" aria-label="Закрыть" onClick={() => setSelected(null)}>×</button></div><div className="ma-dialog-body"><span className="ma-overline">ПОДТВЕРЖДЁННАЯ ВАКАНСИЯ</span><h2 id="ma-detail-title">{selected.title}</h2><p className="ma-detail-salary">{selected.salary}</p><div className="ma-detail-facts">{selected.grade && <span>{selected.grade}</span>}{selected.format && <span>{selected.format}</span>}{selected.location && <span>{selected.location}</span>}</div><div className="ma-detail-divider"/><h3>О вакансии</h3><p className="ma-detail-summary">{selected.summary}</p><h3>Навыки</h3><div className="ma-tags">{selected.tags.map(tag => <span key={tag}>{tag}</span>)}</div></div><div className="ma-dialog-footer"><button className="ma-secondary-button" type="button" onClick={() => void toggleSaved(selected, !!selected.id)}>{selected.saved ? 'Сохранено' : 'Сохранить'}</button>{selected.post_link.startsWith('https://') && <a className="ma-primary-button" href={selected.post_link} target="_blank" rel="noreferrer">Открыть источник ↗</a>}</div></section></div>}
    {toast && <div className="ma-toast" role="status">✓ {toast}</div>}
  </div>;
}
