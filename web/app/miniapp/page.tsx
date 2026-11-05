'use client';

import { useEffect, useMemo, useState } from 'react';
import catalog from '../../../src/tg_vacancy_bot/data/it_roles.v1.json';
import LiveMiniapp from './live';
import './miniapp.css';

type Screen = 'feed' | 'saved' | 'profiles' | 'premium';
type FeedFilter = 'all' | 'new' | 'older' | 'undated';
type Delivery = 'immediate' | 'hourly' | 'manual';
type Profile = { id: string; name: string; directionId: string; roleId: string; grade: string; skills: string; excluded: string; delivery: Delivery };
type Vacancy = { id: string; title: string; company: string; directionId: string; roleId: string; grade: string; salary: string; location: string; format: string; age: string; bucket: Exclude<FeedFilter, 'all'>; tags: string[]; summary: string; source: string; icon: string; color: string; premium?: boolean };
type IconName = 'grid' | 'bookmark' | 'user' | 'spark' | 'chevron' | 'arrow' | 'search' | 'sun' | 'moon' | 'plus' | 'close' | 'check' | 'bell' | 'bolt' | 'clock' | 'external' | 'sliders' | 'menu' | 'briefcase' | 'refresh' | 'info' | 'edit' | 'trash' | 'play' | 'pause';

const paths: Record<IconName, React.ReactNode> = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  bookmark: <path d="M6 4.75A1.75 1.75 0 0 1 7.75 3h8.5A1.75 1.75 0 0 1 18 4.75V21l-6-4-6 4z"/>,
  user: <><circle cx="12" cy="8" r="4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></>,
  spark: <><path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9z"/><path d="m19 17 .6 1.4L21 19l-1.4.6L19 21l-.6-1.4L17 19l1.4-.6z"/></>,
  chevron: <path d="m9 18 6-6-6-6"/>, arrow: <path d="M5 12h14m-6-6 6 6-6 6"/>,
  search: <><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></>,
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/></>,
  moon: <path d="M20.3 15.7A8.5 8.5 0 0 1 8.3 3.7 8.5 8.5 0 1 0 20.3 15.7Z"/>,
  plus: <path d="M12 5v14M5 12h14"/>, close: <path d="m5 5 14 14M19 5 5 19"/>, check: <path d="m5 12 4.5 4.5L19 7"/>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 8-3 9h18c0-1-3-2-3-9Z"/><path d="M10 21h4"/></>,
  bolt: <path d="m13 2-8 11h6l-1 9 9-12h-6z"/>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  external: <><path d="M13 5h6v6m0-6-9 9"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></>,
  sliders: <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16"/>,
  briefcase: <><rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18"/></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.6-4.4L3 9m0-6v6h6"/><path d="M4 13a8 8 0 0 0 14.6 4.4L21 15m0 6v-6h-6"/></>,
  info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/></>,
  edit: <><path d="m4 16-.5 4.5L8 20l11-11-4-4zM13 7l4 4"/></>,
  trash: <><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7"/><path d="M10 11v5m4-5v5"/></>,
  play: <path d="m8 5 11 7-11 7z"/>, pause: <><path d="M9 5v14M15 5v14"/></>,
};
function Icon({ name, size = 20, strokeWidth = 1.8 }: { name: IconName; size?: number; strokeWidth?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

const directions = catalog.directions.map(direction => ({
  id: direction.id,
  label: direction.label,
  roles: direction.specializations.flatMap(s => s.roles.map(role => ({ id: role.id, label: role.label, stacks: role.stacks }))),
}));
const firstProfiles: Profile[] = [
  { id: 'go', name: 'Go Backend', directionId: 'development', roleId: 'backend_developer', grade: 'Middle', skills: 'Go, PostgreSQL', excluded: 'стажировка', delivery: 'immediate' },
  { id: 'design', name: 'Product Design', directionId: 'design', roleId: 'product_designer', grade: 'Senior', skills: 'Figma, UX', excluded: 'стажировка', delivery: 'hourly' },
];
const vacancies: Vacancy[] = [
  { id: 'v1', title: 'Go Backend Developer', company: 'Linear', directionId: 'development', roleId: 'backend_developer', grade: 'Middle+', salary: '250–350 тыс. ₽', location: 'Москва · Россия', format: 'Удалённо', age: '2 часа назад', bucket: 'new', tags: ['Go', 'PostgreSQL', 'Microservices'], summary: 'Разрабатывать сервисы для платформы командной работы, проектировать API и улучшать производительность высоконагруженной системы.', source: 'Telegram · @it_vacancies', icon: 'L', color: 'lavender' },
  { id: 'v2', title: 'Senior Go Engineer', company: 'Авито', directionId: 'development', roleId: 'backend_developer', grade: 'Senior', salary: 'от 320 тыс. ₽', location: 'Россия', format: 'Гибрид', age: '5 часов назад', bucket: 'new', tags: ['Go', 'Kafka', 'Kubernetes'], summary: 'Команда инфраструктуры ищет инженера для разработки масштабируемых сервисов и надёжных внутренних инструментов.', source: 'Telegram · @go_jobs', icon: 'А', color: 'mint' },
  { id: 'v3', title: 'Backend Developer (Go)', company: 'Точка', directionId: 'development', roleId: 'backend_developer', grade: 'Middle', salary: '220–300 тыс. ₽', location: 'Россия', format: 'Удалённо', age: 'Вчера', bucket: 'older', tags: ['Go', 'SQL', 'REST API'], summary: 'Создавать продукты для предпринимателей: от идеи и архитектуры до запуска и поддержки в продакшене.', source: 'Telegram · @backend_jobs', icon: 'Т', color: 'peach' },
  { id: 'v4', title: 'Go Developer · финтех', company: 'Банк Сфера', directionId: 'development', roleId: 'backend_developer', grade: 'Middle', salary: 'По договорённости', location: 'Москва', format: 'Гибрид', age: 'Дата не указана', bucket: 'undated', tags: ['Go', 'Redis', 'API'], summary: 'Развитие платёжной платформы и интеграций с партнёрами в небольшой продуктовой команде.', source: 'Telegram · @golang_vacancies', icon: 'С', color: 'blue' },
  { id: 'v5', title: 'Product Designer', company: 'Т-Банк', directionId: 'design', roleId: 'product_designer', grade: 'Senior', salary: 'от 280 тыс. ₽', location: 'Россия', format: 'Удалённо', age: '3 часа назад', bucket: 'new', tags: ['Figma', 'Design System', 'Research'], summary: 'Проектировать сложные финансовые сценарии и развивать дизайн систему вместе с продуктовой командой.', source: 'Telegram · @design_jobs', icon: 'Т', color: 'yellow' },
  { id: 'v6', title: 'Senior Product Designer', company: 'Ozon', directionId: 'design', roleId: 'product_designer', grade: 'Senior', salary: 'По договорённости', location: 'Москва', format: 'Гибрид', age: 'Вчера', bucket: 'older', tags: ['UX', 'Figma', 'E-commerce'], summary: 'Создавать цельный опыт для покупателей маркетплейса, проверять гипотезы и работать с продуктовыми метриками.', source: 'Telegram · @ux_vacancies', icon: 'O', color: 'pink' },
];
const premiumVacancies: Vacancy[] = [
  { ...vacancies[0], id: 'p1', company: 'Новая команда', title: 'Go Engineer · Platform', salary: '300–420 тыс. ₽', age: 'Найдено в истории', premium: true, icon: 'Н', color: 'blue' },
  { ...vacancies[1], id: 'p2', company: 'Финтех стартап', title: 'Backend Engineer (Go)', salary: 'от 300 тыс. ₽', age: 'Найдено в истории', premium: true, icon: 'Ф', color: 'peach' },
  { ...vacancies[4], id: 'p3', company: 'Студия Продукт', title: 'Lead Product Designer', salary: 'от 300 тыс. ₽', age: 'Найдено в истории', premium: true, icon: 'П', color: 'lavender' },
];
const nav: { id: Screen; label: string; icon: IconName }[] = [
  { id: 'feed', label: 'Лента вакансий', icon: 'grid' },
  { id: 'saved', label: 'Сохранённые', icon: 'bookmark' },
  { id: 'profiles', label: 'Мои профили', icon: 'user' },
  { id: 'premium', label: 'Premium поиск', icon: 'spark' },
];
const blankProfile = (directionId = 'development'): Profile => ({ id: '', name: '', directionId, roleId: '', grade: 'Любой', skills: '', excluded: '', delivery: 'immediate' });
const gradeOptions = ['Любой', 'Intern', 'Junior', 'Middle', 'Senior', 'Lead'];
const deliveryLabels: Record<Delivery, string> = { immediate: 'Сразу', hourly: 'Раз в час', manual: 'Только вручную' };
function roleLabel(id: string) { return directions.flatMap(d => d.roles).find(r => r.id === id)?.label ?? 'Роль не выбрана'; }
function directionLabel(id: string) { return directions.find(d => d.id === id)?.label ?? 'Направление'; }

function DemoMiniappPage() {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [screen, setScreen] = useState<Screen>('feed');
  const [filter, setFilter] = useState<FeedFilter>('all');
  const [profiles, setProfiles] = useState<Profile[]>(firstProfiles);
  const [activeId, setActiveId] = useState('go');
  const [savedIds, setSavedIds] = useState<string[]>(['v3']);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Vacancy | null>(null);
  const [editor, setEditor] = useState<Profile | null>(null);
  const [toast, setToast] = useState('');
  const [premiumTemplate, setPremiumTemplate] = useState('role');
  const [premiumStatus, setPremiumStatus] = useState<'idle' | 'running' | 'done' | 'cancelled'>('idle');
  const [premiumProgress, setPremiumProgress] = useState(0);
  const [mobileMenu, setMobileMenu] = useState(false);
  const active = profiles.find(profile => profile.id === activeId) ?? profiles[0];

  useEffect(() => { const stored = window.localStorage.getItem('miniapp-demo-theme'); if (stored === 'dark') setTheme('dark'); }, []);
  useEffect(() => { window.localStorage.setItem('miniapp-demo-theme', theme); }, [theme]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 3200); return () => window.clearTimeout(timer); }, [toast]);
  useEffect(() => {
    if (premiumStatus !== 'running') return;
    const timer = window.setInterval(() => setPremiumProgress(value => {
      if (value >= 100) { window.clearInterval(timer); setPremiumStatus('done'); return 100; }
      return Math.min(100, value + 4);
    }), 130);
    return () => window.clearInterval(timer);
  }, [premiumStatus]);

  const feed = useMemo(() => vacancies.filter(v => v.directionId === active?.directionId && v.roleId === active?.roleId && (filter === 'all' || v.bucket === filter) && `${v.title} ${v.company} ${v.tags.join(' ')}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'))), [active, filter, query]);
  const saved = [...vacancies, ...premiumVacancies].filter(v => savedIds.includes(v.id));
  const matches = vacancies.filter(v => v.directionId === active?.directionId && v.roleId === active?.roleId);
  const premiumMatches = premiumVacancies.filter(v => v.directionId === active?.directionId && v.roleId === active?.roleId);
  const template = premiumTemplate === 'stack' ? `${roleLabel(active?.roleId ?? '')} ${active?.skills || ''} вакансии` : `${roleLabel(active?.roleId ?? '')} вакансии`;
  const showToast = (message: string) => setToast(message);
  const openScreen = (next: Screen) => { setScreen(next); setMobileMenu(false); setQuery(''); window.scrollTo({ top: 0, behavior: 'smooth' }); };
  const toggleSaved = (id: string) => { const exists = savedIds.includes(id); setSavedIds(items => exists ? items.filter(item => item !== id) : [...items, id]); showToast(exists ? 'Убрано из сохранённых' : 'Вакансия сохранена'); };
  const saveProfile = () => {
    if (!editor) return;
    if (!editor.name.trim() || !editor.roleId) { showToast('Укажите название профиля и роль'); return; }
    const updated = { ...editor, id: editor.id || `profile-${Date.now()}`, name: editor.name.trim(), skills: editor.skills.trim(), excluded: editor.excluded.trim() };
    setProfiles(items => editor.id ? items.map(item => item.id === editor.id ? updated : item) : [...items, updated]);
    if (!activeId) setActiveId(updated.id);
    setEditor(null);
    showToast(editor.id ? 'Профиль обновлён' : 'Профиль создан. Выберите его активным для поиска.');
  };
  const startPremium = () => { setPremiumProgress(0); setPremiumStatus('running'); showToast('Демо-поиск запущен'); };

  function VacancyCard({ vacancy, compact = false }: { vacancy: Vacancy; compact?: boolean }) {
    return <article className={`ma-vacancy ${compact ? 'ma-vacancy-compact' : ''}`}>
      <div className="ma-vacancy-top">
        <div className={`ma-company-icon ma-${vacancy.color}`}>{vacancy.icon}</div>
        <div className="ma-vacancy-identity"><span>{vacancy.company}</span><span className="ma-vacancy-age">{vacancy.age}</span></div>
        <button className={`ma-icon-button ma-save-button ${savedIds.includes(vacancy.id) ? 'is-saved' : ''}`} type="button" aria-label={savedIds.includes(vacancy.id) ? 'Убрать из сохранённых' : 'Сохранить вакансию'} onClick={() => toggleSaved(vacancy.id)}><Icon name="bookmark" size={19}/></button>
      </div>
      <button className="ma-vacancy-title" type="button" onClick={() => setSelected(vacancy)}>{vacancy.title}</button>
      <div className="ma-vacancy-meta"><span>{vacancy.salary}</span><i/> <span>{vacancy.format}</span><i/> <span>{vacancy.location}</span></div>
      {!compact && <p className="ma-vacancy-summary">{vacancy.summary}</p>}
      <div className="ma-vacancy-bottom"><div className="ma-tags">{vacancy.tags.map(tag => <span key={tag}>{tag}</span>)}</div><button className="ma-open-button" type="button" onClick={() => setSelected(vacancy)} aria-label={`Открыть ${vacancy.title}`}><Icon name="arrow" size={19}/></button></div>
    </article>;
  }

  return <div className="miniapp" data-theme={theme}>
    <div className="ma-app">
      <aside className={`ma-sidebar ${mobileMenu ? 'ma-sidebar-open' : ''}`} aria-label="Навигация">
        <div className="ma-brand"><div className="ma-brand-mark"><span/><span/><span/><span/></div><div><strong>radar<span className="ma-brand-dot">.</span></strong><small>работа по твоим правилам</small></div></div>
        <div className="ma-sidebar-section-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
        <nav className="ma-nav">{nav.map(item => <button key={item.id} type="button" className={`ma-nav-item ${screen === item.id ? 'active' : ''}`} onClick={() => openScreen(item.id)}><Icon name={item.icon} size={19}/><span>{item.label}</span>{item.id === 'saved' && saved.length > 0 && <em>{saved.length}</em>}{item.id === 'premium' && <span className="ma-nav-new">NEW</span>}</button>)}</nav>
        <div className="ma-sidebar-spacer"/>
        <div className="ma-sidebar-guide"><span className="ma-guide-icon"><Icon name="spark" size={17}/></span><strong>Поиск под тебя</strong><p>Создай профиль один раз. Radar покажет подходящие вакансии и сохранит интересное.</p><button type="button" onClick={() => openScreen('profiles')}>Настроить профиль <Icon name="arrow" size={15}/></button></div>
        <div className="ma-sidebar-footer"><div className="ma-avatar">MK</div><div><strong>Демо пользователь</strong><small>Интерактивный макет</small></div><span className="ma-avatar-more">•••</span></div>
      </aside>

      <main className="ma-main">
        <header className="ma-topbar"><button className="ma-mobile-menu ma-icon-button" type="button" aria-label="Открыть меню" onClick={() => setMobileMenu(v => !v)}><Icon name={mobileMenu ? 'close' : 'menu'}/></button><div className="ma-breadcrumb"><span>Рабочее пространство</span><Icon name="chevron" size={14}/><strong>{nav.find(item => item.id === screen)?.label}</strong></div><div className="ma-topbar-actions"><span className="ma-demo-label"><span/> ДЕМО-РЕЖИМ</span><button className="ma-theme-button" type="button" onClick={() => setTheme(t => t === 'light' ? 'dark' : 'light')} aria-label={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'} title={theme === 'light' ? 'Тёмная тема' : 'Светлая тема'}><Icon name={theme === 'light' ? 'moon' : 'sun'} size={19}/></button><span className="ma-top-avatar">MK</span></div></header>

        <div className="ma-content">
          {screen === 'feed' && <>
            <section className="ma-hero"><div className="ma-hero-copy"><div className="ma-eyebrow"><span/> ТВОЯ КАРЬЕРА, ТВОИ ПРАВИЛА</div><h1>Хорошая работа<br/><em>находится здесь.</em></h1><p>Вакансии, которые подходят твоему профилю. Без лишнего шума — только то, что действительно интересно.</p><button className="ma-hero-cta" type="button" onClick={() => openScreen('profiles')}>Настроить поиск <Icon name="arrow" size={18}/></button></div><div className="ma-hero-art" aria-hidden="true"><div className="ma-art-orbit ma-art-orbit-one"/><div className="ma-art-orbit ma-art-orbit-two"/><div className="ma-art-glow"/><div className="ma-art-card ma-art-card-back"><span className="ma-art-square">✦</span><span><b>Новая вакансия</b><small>Совпадение с профилем</small></span></div><div className="ma-art-card ma-art-card-front"><div className="ma-art-card-line"><span className="ma-art-company">G</span><span><b>Go Backend Developer</b><small>Удалённо · 250–350 тыс. ₽</small></span></div><div className="ma-art-match"><span>✦</span> Подходит тебе <b>98%</b></div></div><div className="ma-art-star ma-art-star-one">✦</div><div className="ma-art-star ma-art-star-two">✦</div></div></section>
            <div className="ma-section-heading ma-feed-heading"><div><span className="ma-overline">ТВОЯ ЛЕНТА</span><h2>Подходящие вакансии <span className="ma-heading-count">{matches.length}</span></h2><p>Результаты для профиля <button type="button" onClick={() => openScreen('profiles')}>{active?.name} <Icon name="chevron" size={13}/></button></p></div><button className="ma-text-action" type="button" aria-label="Обновить вакансии" onClick={() => showToast('Демо-данные обновлены')}><Icon name="refresh" size={16}/><span>Обновить</span></button></div>
            <div className="ma-feed-toolbar"><div className="ma-filters" role="tablist" aria-label="Период вакансий">{([['all', 'Для меня'], ['new', 'Новые · 24 ч'], ['older', 'Ранее'], ['undated', 'Без даты']] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={filter === id} type="button" className={filter === id ? 'selected' : ''} onClick={() => setFilter(id)}>{label}</button>)}</div><label className="ma-search-box"><Icon name="search" size={18}/><input aria-label="Поиск по вакансиям" placeholder="Поиск вакансий" value={query} onChange={event => setQuery(event.target.value)}/></label></div>
            {feed.length ? <div className="ma-vacancy-grid">{feed.map(v => <VacancyCard key={v.id} vacancy={v}/>)}</div> : <div className="ma-empty"><span><Icon name="search" size={27}/></span><h3>Пока нет вакансий по этим условиям</h3><p>Попробуй другой период или профиль. Новые совпадения появятся здесь.</p><button type="button" onClick={() => { setFilter('all'); setQuery(''); }}>Показать все</button></div>}
            <div className="ma-feed-note"><Icon name="info" size={17}/> В демо показаны примеры вакансий. В боте лента содержит только подтверждённые совпадения активного профиля.</div>
          </>}

          {screen === 'saved' && <><section className="ma-page-heading"><div><span className="ma-overline">ТВОЯ КОЛЛЕКЦИЯ</span><h1>Сохранённые <span className="ma-heading-count">{saved.length}</span></h1><p>Интересные вакансии всегда под рукой, даже если переключить профиль.</p></div><span className="ma-heading-decoration"><Icon name="bookmark" size={26}/></span></section>{saved.length ? <div className="ma-vacancy-grid">{saved.map(v => <VacancyCard key={v.id} vacancy={v}/>)}</div> : <div className="ma-empty"><span><Icon name="bookmark" size={27}/></span><h3>Здесь пока пусто</h3><p>Нажми на закладку у вакансии, и она появится в этом разделе.</p><button type="button" onClick={() => openScreen('feed')}>Смотреть вакансии <Icon name="arrow" size={16}/></button></div>}</>}

          {screen === 'profiles' && <><section className="ma-page-heading"><div><span className="ma-overline">НАСТРОЙ ПОИСК ПОД СЕБЯ</span><h1>Мои профили</h1><p>Разные карьерные цели — разные профили. Активным может быть только один.</p></div><button className="ma-primary-button" type="button" onClick={() => setEditor(blankProfile())}><Icon name="plus" size={18}/> Новый профиль</button></section><div className="ma-profile-list">{profiles.map(profile => <article className={`ma-profile-card ${profile.id === activeId ? 'is-active' : ''}`} key={profile.id}><div className="ma-profile-leading"><div className="ma-profile-icon"><Icon name={profile.directionId === 'design' ? 'spark' : 'briefcase'} size={23}/></div><div><div className="ma-profile-title-row"><h2>{profile.name}</h2>{profile.id === activeId && <span className="ma-active-badge"><span/> Активный</span>}</div><p>{directionLabel(profile.directionId)} <span>·</span> {roleLabel(profile.roleId)}</p></div></div><div className="ma-profile-chips"><span>{profile.grade}</span>{profile.skills.split(',').map(s => s.trim()).filter(Boolean).slice(0, 3).map(s => <span key={s}>{s}</span>)}<span><Icon name={profile.delivery === 'immediate' ? 'bolt' : 'clock'} size={13}/> {deliveryLabels[profile.delivery]}</span></div><div className="ma-profile-actions"><button type="button" className="ma-profile-edit" onClick={() => setEditor({ ...profile })}><Icon name="edit" size={16}/> Изменить</button>{profile.id !== activeId && <button type="button" className="ma-profile-activate" onClick={() => { setActiveId(profile.id); setFilter('all'); setPremiumStatus('idle'); showToast(`Профиль «${profile.name}» активен`); }}>Сделать активным <Icon name="arrow" size={16}/></button>}</div></article>)}</div><div className="ma-tip"><span><Icon name="bell" size={19}/></span><div><strong>Уведомления работают по активному профилю</strong><p>Выбери, как часто получать новые совпадения: сразу, раз в час или только при открытии ленты.</p></div></div></>}

          {screen === 'premium' && <><section className="ma-page-heading ma-premium-heading"><div><span className="ma-overline">РАСШИРЕННЫЙ ПОИСК</span><h1>Premium поиск <span className="ma-title-spark">✦</span></h1><p>Посмотри вакансии из истории Telegram по своему активному профилю.</p></div><span className="ma-premium-icon"><Icon name="spark" size={27}/></span></section><div className="ma-premium-layout"><div className="ma-premium-main"><div className="ma-premium-card"><div className="ma-card-step"><span>01</span><div><h2>Проверь настройки поиска</h2><p>Поиск использует снимок активного профиля на момент запуска.</p></div></div><div className="ma-premium-fields"><div><label>Активный профиль</label><button type="button" className="ma-input-button" onClick={() => openScreen('profiles')}><span><Icon name="user" size={17}/> {active?.name}</span><Icon name="chevron" size={16}/></button></div><div><label htmlFor="ma-premium-template">Шаблон запроса</label><select id="ma-premium-template" value={premiumTemplate} onChange={event => setPremiumTemplate(event.target.value)}><option value="role">По роли</option><option value="stack">Роль + навыки</option></select></div></div><div className="ma-query-preview"><span>ПРЕДПРОСМОТР ЗАПРОСА</span><strong>{template}</strong><p>Источник: Telegram Premium · Период: 7 дней · До 50 подтверждённых совпадений</p></div><div className="ma-premium-action-row"><button className="ma-primary-button" type="button" onClick={startPremium} disabled={premiumStatus === 'running'}><Icon name={premiumStatus === 'running' ? 'clock' : 'spark'} size={18}/>{premiumStatus === 'running' ? 'Поиск выполняется…' : premiumStatus === 'done' ? 'Повторить демо-поиск' : 'Запустить поиск'}</button><span>В реальном боте — 1 поиск за 7 дней</span></div></div>{premiumStatus !== 'idle' && <div className="ma-search-progress-card"><div className="ma-progress-top"><span className="ma-progress-icon"><Icon name={premiumStatus === 'done' ? 'check' : premiumStatus === 'cancelled' ? 'pause' : 'search'} size={20}/></span><div><strong>{premiumStatus === 'done' ? 'Поиск завершён' : premiumStatus === 'cancelled' ? 'Поиск отменён' : 'Ищем подходящие вакансии'}</strong><p>{premiumStatus === 'done' ? `Найдено ${premiumMatches.length} примера для профиля` : premiumStatus === 'cancelled' ? 'Можно запустить демонстрацию снова' : 'Проверяем историю и показываем подтверждённые совпадения'}</p></div><b>{premiumProgress}%</b></div><div className="ma-progress-track"><div style={{ width: `${premiumProgress}%` }}/></div>{premiumStatus === 'running' && <button type="button" onClick={() => setPremiumStatus('cancelled')}>Отменить поиск</button>}</div>}{premiumStatus === 'done' && <><div className="ma-section-heading ma-premium-results-heading"><div><span className="ma-overline">РЕЗУЛЬТАТЫ ПОИСКА</span><h2>Найденные вакансии <span className="ma-heading-count">{premiumMatches.length}</span></h2></div></div>{premiumMatches.length ? <div className="ma-vacancy-grid ma-premium-results">{premiumMatches.map(v => <VacancyCard key={v.id} vacancy={v} compact/>)}</div> : <div className="ma-empty"><span><Icon name="search" size={27}/></span><h3>Совпадений для этого профиля нет</h3><p>Выбери другой профиль или измени навыки и повтори демо-поиск.</p></div>}</>}</div><aside className="ma-premium-aside"><div className="ma-how-card"><span className="ma-how-icon"><Icon name="spark" size={22}/></span><h3>Как это работает</h3><div className="ma-how-step"><b>1</b><p>Выбери активный профиль и шаблон запроса.</p></div><div className="ma-how-step"><b>2</b><p>Проверь запрос и запусти поиск по истории.</p></div><div className="ma-how-step"><b>3</b><p>Изучи результаты и сохрани интересное.</p></div></div><div className="ma-premium-notice"><Icon name="info" size={18}/><p>Это демонстрация. Реальный поиск выполняется ботом в Telegram и не запускается с этой страницы.</p></div></aside></div></>}
        </div>
      </main>
      <nav className="ma-mobile-nav" aria-label="Основные разделы">{nav.map(item => <button key={item.id} type="button" className={screen === item.id ? 'active' : ''} onClick={() => openScreen(item.id)}><Icon name={item.icon} size={21}/><span>{item.id === 'feed' ? 'Лента' : item.id === 'profiles' ? 'Профили' : item.id === 'premium' ? 'Premium' : 'Сохранено'}</span></button>)}</nav>
    </div>

    {editor && <div className="ma-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setEditor(null); }}><section className="ma-dialog ma-profile-dialog" role="dialog" aria-modal="true" aria-labelledby="ma-editor-title"><div className="ma-dialog-head"><div><span className="ma-overline">НАСТРОЙКИ ПОИСКА</span><h2 id="ma-editor-title">{editor.id ? 'Изменить профиль' : 'Новый профиль'}</h2><p>Расскажи, какую работу ищешь. Подходящие вакансии появятся в ленте.</p></div><button type="button" className="ma-icon-button" aria-label="Закрыть" onClick={() => setEditor(null)}><Icon name="close"/></button></div><div className="ma-dialog-body"><div className="ma-form-grid"><label className="ma-field ma-field-full">Название профиля<input value={editor.name} onChange={event => setEditor({ ...editor, name: event.target.value })} placeholder="Например, Go Backend" maxLength={40}/></label><label className="ma-field">Направление<select value={editor.directionId} onChange={event => setEditor({ ...editor, directionId: event.target.value, roleId: '' })}>{directions.map(d => <option key={d.id} value={d.id}>{d.label}</option>)}</select></label><label className="ma-field">Роль<select value={editor.roleId} onChange={event => setEditor({ ...editor, roleId: event.target.value })}><option value="">Выбери роль</option>{directions.find(d => d.id === editor.directionId)?.roles.map(role => <option key={role.id} value={role.id}>{role.label}</option>)}</select></label><label className="ma-field">Грейд<select value={editor.grade} onChange={event => setEditor({ ...editor, grade: event.target.value })}>{gradeOptions.map(grade => <option key={grade}>{grade}</option>)}</select></label><label className="ma-field">Уведомления<select value={editor.delivery} onChange={event => setEditor({ ...editor, delivery: event.target.value as Delivery })}><option value="immediate">Сразу</option><option value="hourly">Раз в час</option><option value="manual">Только вручную</option></select></label><label className="ma-field ma-field-full">Ключевые навыки <small>через запятую · достаточно одного совпадения</small><input value={editor.skills} onChange={event => setEditor({ ...editor, skills: event.target.value })} placeholder="Go, PostgreSQL, Kafka"/></label><label className="ma-field ma-field-full">Слова-исключения <small>через запятую</small><input value={editor.excluded} onChange={event => setEditor({ ...editor, excluded: event.target.value })} placeholder="стажировка, офис"/></label></div></div><div className="ma-dialog-footer">{editor.id && editor.id !== activeId && <button className="ma-delete-button" type="button" onClick={() => { setProfiles(items => items.filter(p => p.id !== editor.id)); setEditor(null); showToast('Профиль удалён'); }}><Icon name="trash" size={16}/> Удалить</button>}<button className="ma-secondary-button" type="button" onClick={() => setEditor(null)}>Отмена</button><button className="ma-primary-button" type="button" onClick={saveProfile}>Сохранить профиль <Icon name="arrow" size={17}/></button></div></section></div>}
    {selected && <div className="ma-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="ma-dialog ma-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="ma-detail-title"><div className="ma-dialog-head"><div className="ma-detail-company"><div className={`ma-company-icon ma-${selected.color}`}>{selected.icon}</div><div><strong>{selected.company}</strong><small>{selected.source}</small></div></div><button type="button" className="ma-icon-button" aria-label="Закрыть" onClick={() => setSelected(null)}><Icon name="close"/></button></div><div className="ma-dialog-body"><span className="ma-overline">{selected.premium ? 'НАЙДЕНО В PREMIUM ПОИСКЕ' : 'ПОДХОДЯЩАЯ ВАКАНСИЯ'}</span><h2 id="ma-detail-title">{selected.title}</h2><p className="ma-detail-salary">{selected.salary}</p><div className="ma-detail-facts"><span><Icon name="briefcase" size={16}/> {selected.grade}</span><span><Icon name="clock" size={16}/> {selected.age}</span><span>{selected.format}</span><span>{selected.location}</span></div><div className="ma-detail-divider"/><h3>О вакансии</h3><p className="ma-detail-summary">{selected.summary}</p><h3>Ключевые навыки</h3><div className="ma-tags">{selected.tags.map(tag => <span key={tag}>{tag}</span>)}</div></div><div className="ma-dialog-footer"><button className="ma-secondary-button" type="button" onClick={() => toggleSaved(selected.id)}><Icon name="bookmark" size={17}/>{savedIds.includes(selected.id) ? 'Сохранено' : 'Сохранить'}</button><button className="ma-primary-button" type="button" onClick={() => showToast('В демо ссылка на Telegram не открывается')}>Открыть источник <Icon name="external" size={17}/></button></div></section></div>}
    {toast && <div className="ma-toast" role="status"><Icon name="check" size={17}/>{toast}</div>}
  </div>;
}

export default function MiniappPage() {
  const [initData, setInitData] = useState<string | null>(null);
  useEffect(() => {
    const bridge = (window as Window & { Telegram?: { WebApp?: { initData?: string; ready?: () => void; expand?: () => void } } }).Telegram?.WebApp;
    bridge?.ready?.();
    bridge?.expand?.();
    setInitData(bridge?.initData || '');
  }, []);
  if (initData === null) return <div className="miniapp ma-live-loading">Загружаем Mini App…</div>;
  return initData ? <LiveMiniapp initData={initData}/> : <DemoMiniappPage/>;
}
