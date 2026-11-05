'use client';

import { useCallback, useEffect, useState } from 'react';
import { SearchPanel } from '../components/search-panel';
import { PremiumSearchPanel } from '../components/premium-search-panel';
import { premiumApi, type PremiumCapabilities } from '../lib/api';
import { AttentionPanel } from '../components/attention-panel';
import { ConfirmAction } from '../components/confirm-action';
import { LoginForm } from '../components/login-form';
import { LogsPanel } from '../components/logs-panel';
import { MetricCards } from '../components/metric-cards';
import { PromptEditor } from '../components/prompt-editor';
import { SettingsForm } from '../components/settings-form';
import { SourcesPanel } from '../components/sources-panel';
import { VacancyGroupsPanel } from '../components/vacancy-groups-panel';
import { api, waitForAction, type AdminAction, type AttentionError, type Metrics, type Settings } from '../lib/api';

type Dashboard = {
  settings_revision:number;
  latest_export_at?:string|null;
  channel_count:number;
  heartbeat?:{status?:string;updated_at?:string;settings_revision?:number;queue_size?:number;received_messages?:number;keyword_matches?:number;saved_matches?:number};
  operations:{at:string;event:string;action?:string}[];
  secret_status:{telegram:boolean;mistral:boolean;google_sheets:boolean};
  active_action?:AdminAction|null;
};
type Route = '/search'|'/premium-search'|'/'|'/sources'|'/groups'|'/settings'|'/prompt'|'/logs'|'/errors';
const routeLabels:Record<Route,string> = {'/search':'Общий поиск','/premium-search':'Premium поиск','/':'Обзор','/sources':'Источники','/groups':'Группы вакансий','/settings':'Настройки','/prompt':'Инструкции LLM','/logs':'Журнал событий','/errors':'Ошибки'};
const actionText: Record<string,[string,string]> = {
  restart:['Перезапустить бота','Очередь будет корректно завершена, затем бот применит сохранённые настройки.'],
  history:['Запустить историю','Live-мониторинг временно остановится: один Telegram session нельзя использовать одновременно.'],
  sync_channels:['Синхронизировать папку','Live bot целиком прочитает Telegram-папку, атомарно сохранит её состав и перезапустит listener.'],
};

function currentRoute():Route {
  const path = window.location.pathname.replace(/\/$/, '') || '/';
  return Object.prototype.hasOwnProperty.call(routeLabels,path) ? path as Route : '/';
}

function navigate(path:Route) { window.history.pushState({},'',path); window.dispatchEvent(new PopStateEvent('popstate')); }
function formatDate(value?:string|null) { return value ? new Date(value).toLocaleString('ru-RU') : 'нет данных'; }
function actionResult(result:AdminAction) { return result.status==='succeeded'?`Готово: получено ${result.received}, добавлено ${result.created}, обновлено ${result.updated}, пропущено ${result.skipped}, ошибок ${result.failed}.`:result.error||'Операция завершилась с ошибкой.'; }

type IconName='dashboard'|'search'|'premium'|'groups'|'sources'|'logs'|'errors'|'settings'|'prompt'|'sun'|'moon'|'refresh'|'bell'|'logout'|'chevron'|'radar';
function Icon({name}:{name:IconName}) {
  const paths:Record<IconName,React.ReactNode>={
    dashboard:<><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="5" rx="2"/><rect x="13" y="10" width="8" height="11" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/></>,
    search:<><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></>,
    premium:<><path d="m12 3 1.9 5.4L19 10l-5.1 1.8L12 17l-1.9-5.2L5 10l5.1-1.6L12 3Z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/></>,
    groups:<><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/></>,
    sources:<><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3"/><path d="M12 3.5V9m8.5 3H15"/></>,
    logs:<><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></>,
    errors:<><path d="m12 3 9 17H3l9-17Z"/><path d="M12 9v4m0 3h.01"/></>,
    settings:<><circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.3 1-1.3 2.2-1.6-.6a8 8 0 0 1-1.7 1l-.3 1.7h-2.6l-.3-1.7a8 8 0 0 1-1.7-1l-1.6.6-1.3-2.2 1.3-1a8 8 0 0 1 0-2l-1.3-1 1.3-2.2 1.6.6a8 8 0 0 1 1.7-1l.3-1.7h2.6l.3 1.7a8 8 0 0 1 1.7 1l1.6-.6 1.3 2.2-1.3 1a8 8 0 0 1 0 2Z"/></>,
    prompt:<><path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1"/><circle cx="12" cy="12" r="4"/></>,
    sun:<><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m12-12-1.4 1.4"/></>,
    moon:<path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5 8.5 8.5 0 1 0 20.5 14.2Z"/>,
    refresh:<><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 9A7 7 0 0 1 18 6.5L20 9M4 15l2 2.5A7 7 0 0 0 18.4 15"/></>,
    bell:<><path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 12h4"/></>,
    logout:<><path d="M10 17l5-5-5-5m5 5H3"/><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6"/></>,
    chevron:<path d="m9 18 6-6-6-6"/>,
    radar:<><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12 18.5 6"/><circle cx="12" cy="12" r="1"/></>,
  };
  return <svg className="admin-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{paths[name]}</svg>;
}

function StatusCard({dashboard, errors, onNavigate}:{dashboard?:Dashboard;errors:AttentionError[];onNavigate:(path:Route)=>void}) {
  const heartbeat=dashboard?.heartbeat;
  const heartbeatAge=heartbeat?.updated_at ? Date.now()-new Date(heartbeat.updated_at).getTime() : Number.POSITIVE_INFINITY;
  const heartbeatFresh=heartbeatAge<=180000;
  const running=heartbeat?.status==='running' && heartbeatFresh;
  const paused=heartbeat?.status==='paused';
  const label=running?'Активен':paused?'Приостановлен':'Нет heartbeat';
  const pendingRestart=typeof heartbeat?.settings_revision==='number' && heartbeat.settings_revision !== dashboard?.settings_revision;
  const details = [
    {label:'Heartbeat', value:formatDate(heartbeat?.updated_at), hint:`Revision ${dashboard?.settings_revision??0}${pendingRestart?' · требуется перезапуск':''}`},
    {label:'Очередь', value:String(heartbeat?.queue_size ?? '—'), hint:`Источников настроено: ${dashboard?.channel_count??0}`},
    {label:'Последний экспорт', value:formatDate(dashboard?.latest_export_at), hint:'Последняя успешная запись'},
  ];
  const toneClass = running ? 'tone-good' : paused ? 'tone-muted' : 'tone-error';
  return <section className="health-card" aria-labelledby="system-status">
    <div className="health-header">
      <div><p className={`health-state ${toneClass}`}><span className="health-dot" aria-hidden="true"/>{label}</p><h1 id="system-status">Мониторинг обработки</h1><p className="muted">Состояние live‑сбора вакансий</p></div>
      <div className="health-actions"><span className={`state-chip ${toneClass}`}>{pendingRestart?'Нужно применить настройки':running?'LIVE':'Проверьте систему'}</span><button className="button secondary" onClick={()=>onNavigate('/logs')}><Icon name="logs"/>Журнал событий</button></div>
    </div>
    <div className="health-details">{details.map(item=><div className="health-detail" key={item.label}><span className="detail-label">{item.label}</span><strong className="detail-value">{item.value}</strong><span className="detail-hint">{item.hint}</span></div>)}</div>
    {(errors.length>0 || pendingRestart || !running) && <div className="health-notice">{pendingRestart?'Сохранённые настройки ещё не применены. Перезапустите бота после проверки изменений.':!running?'Свежего heartbeat нет. Проверьте журнал событий и ошибки.':`${errors.length} ${errors.length===1?'ошибка требует':'ошибки требуют'} внимания.`}</div>}
  </section>;
}

function DashboardView({dashboard,metrics,errors,loading,onNavigate,onRefresh,onResolve}:{dashboard?:Dashboard;metrics?:Metrics;errors:AttentionError[];loading:boolean;onNavigate:(path:Route)=>void;onRefresh:()=>void;onResolve:(id:string)=>Promise<void>}) {
  return <div className="dashboard-stack"><StatusCard dashboard={dashboard} errors={errors} onNavigate={onNavigate}/><MetricCards metrics={metrics} loading={loading} error="" onRefresh={onRefresh}/><AttentionPanel errors={errors} loading={loading} onResolve={onResolve} onOpenErrors={()=>onNavigate('/errors')}/></div>;
}

export default function Page() {
  const [premium,setPremium]=useState<PremiumCapabilities|null>(null);
  const [ready,setReady]=useState(false); const [configured,setConfigured]=useState(false); const [settings,setSettings]=useState<Settings>(); const [dashboard,setDashboard]=useState<Dashboard>(); const [metrics,setMetrics]=useState<Metrics>(); const [attention,setAttention]=useState<AttentionError[]>([]); const [loading,setLoading]=useState(false); const [error,setError]=useState(''); const [progress,setProgress]=useState(''); const [pending,setPending]=useState(''); const [sourcesRefresh,setSourcesRefresh]=useState(0); const [route,setRoute]=useState<Route>('/'); const [theme,setTheme]=useState<'light'|'dark'>('light');
  const refresh = useCallback(async()=>{setLoading(true);setError('');try{const [nextSettings,nextDashboard,nextMetrics,nextErrors]=await Promise.all([api.settings(),api.dashboard(),api.metrics(),api.errors()]);setSettings(nextSettings);setDashboard(nextDashboard);setMetrics(nextMetrics);setAttention(nextErrors);void premiumApi.capabilities().then(setPremium).catch(()=>setPremium(null));}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось загрузить данные панели');}finally{setLoading(false);}},[]);
  useEffect(()=>{const change=()=>setRoute(currentRoute());change();window.addEventListener('popstate',change);api.status().then(async status=>{setConfigured(status.configured);if(status.authenticated) await refresh();setReady(true);}).catch(()=>setReady(true));return()=>window.removeEventListener('popstate',change);},[refresh]);
  useEffect(()=>{const stored=window.localStorage.getItem('admin-theme');if(stored==='dark')setTheme('dark');},[]);
  useEffect(()=>{document.documentElement.classList.toggle('dark',theme==='dark');document.documentElement.dataset.theme=theme;window.localStorage.setItem('admin-theme',theme);},[theme]);
  useEffect(()=>{const action=dashboard?.active_action;if(!action)return;setProgress('Операция выполняется…');waitForAction(action.id).then(async result=>{setProgress(actionResult(result));await refresh();setSourcesRefresh(value=>value+1);}).catch(reason=>setError(reason instanceof Error?reason.message:'Не удалось получить статус операции'));},[dashboard?.active_action?.id,refresh]);
  const go=(path:Route)=>navigate(path);
  if(!ready) return <main className="shell">Загружаем…</main>;
  if(!settings) return <LoginForm configured={configured} onLogin={async(password)=>{await api.login(password);await refresh();}}/>;
  const save=async(value:Partial<Settings>)=>{setError('');try{const saved=await api.save({...value,revision:settings.revision});setSettings(saved);setDashboard(current=>current?{...current,settings_revision:saved.revision}:current);}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось сохранить настройки');throw reason;}};
  const resolve=async(id:string)=>{try{await api.resolveError(id);await refresh();}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось обновить статус ошибки');}};
  const doAction=async()=>{try{const action=await api.action(pending);setPending('');setProgress('Операция поставлена в очередь…');const result=await waitForAction(action.id);setProgress(actionResult(result));await refresh();setSourcesRefresh(value=>value+1);}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось выполнить действие');setPending('');}};
  const logout=async()=>{await api.logout();setSettings(undefined);go('/')};
  const restartRequired=typeof dashboard?.heartbeat?.settings_revision==='number' && dashboard.heartbeat.settings_revision!==settings.revision;
  const header=<Header premiumEnabled={!!premium} route={route} onNavigate={go}/>;
  let content:React.ReactNode;
  if(route==='/search') content=<SearchPanel/>;
  else if(route==='/premium-search') content=<PremiumSearchPanel capabilities={premium}/>;
  else if(route==='/sources') content=<SourcesPanel onBack={()=>go('/')} onChanged={refresh} restartRequired={restartRequired} reloadKey={sourcesRefresh}/>;
  else if(route==='/groups') content=<VacancyGroupsPanel onBack={()=>go('/')} />;
  else if(route==='/settings') content=<SettingsForm settings={settings} onSave={save}/>;
  else if(route==='/prompt') content=<PromptEditor onBack={()=>go('/settings')}/>;
  else if(route==='/logs') content=<LogsPanel onBack={()=>go('/')} />;
  else if(route==='/errors') content=<section className="screen"><div className="section-title"><div><h1>Активные ошибки</h1><p className="muted">Показаны только неразрешённые, безопасно очищенные ошибки. Повтор пока не поддерживается.</p></div><button className="button secondary" onClick={()=>void refresh()} disabled={loading}>Обновить</button></div><AttentionPanel errors={attention} loading={loading} onResolve={resolve} showEmpty/></section>;
  else content=<DashboardView dashboard={dashboard} metrics={metrics} errors={attention} loading={loading} onNavigate={go} onRefresh={()=>void refresh()} onResolve={resolve}/>;
  return <div className="admin-layout">
    {header}
    <main className="admin-main">
      <header className="admin-topbar"><div className="breadcrumb"><span>Go Radar</span><Icon name="chevron"/><strong>{routeLabels[route]}</strong></div><div className="top-actions"><button className="icon-button" onClick={()=>setTheme(theme==='light'?'dark':'light')} aria-label={theme==='light'?'Включить тёмную тему':'Включить светлую тему'}><Icon name={theme==='light'?'moon':'sun'}/></button><button className="icon-button" onClick={()=>void logout()} aria-label="Выйти"><Icon name="logout"/></button></div></header>
      {error&&<p className="admin-alert" role="alert">{error}</p>}{progress&&<p className="admin-progress" role="status">{progress}</p>}
      <div className="page-content">{content}</div>
      {route==='/'&&<section className="control-panel"><div><h2>Управление обработкой</h2><p>Операции, влияющие на текущую работу бота.</p></div><div className="actions"><button className="button secondary" onClick={()=>setPending('sync_channels')}><Icon name="refresh"/>Синхронизировать источники</button><button className="button secondary" onClick={()=>setPending('history')}>Запустить историю</button><button className="button" onClick={()=>setPending('restart')}>Перезапустить бота</button></div></section>}
      {pending&&<ConfirmAction title={actionText[pending][0]} description={actionText[pending][1]} onConfirm={()=>void doAction()} onCancel={()=>setPending('')}/>}
    </main>
  </div>;
}

function Header({premiumEnabled=false,route,onNavigate}:{premiumEnabled?:boolean;route:Route;onNavigate:(path:Route)=>void}) {
  const sections:{title:string;items:Route[]}[]=[
    {title:'Рабочее пространство',items:['/','/search','/premium-search','/groups']},
    {title:'Система',items:['/sources','/logs','/errors']},
    {title:'Настройки',items:['/settings','/prompt']},
  ];
  const icons:Record<Route,IconName>={'/':'dashboard','/search':'search','/premium-search':'premium','/groups':'groups','/sources':'sources','/logs':'logs','/errors':'errors','/settings':'settings','/prompt':'prompt'};
  return <aside className="admin-sidebar">
    <button className="admin-brand" onClick={()=>onNavigate('/')}><span className="brand-mark"><Icon name="radar"/></span><span>Go Radar<small>CONTROL ROOM</small></span></button>
    {sections.map(section=><div className="sidebar-section" key={section.title}><p className="sidebar-label">{section.title}</p><nav className="sidebar-nav" aria-label={section.title==='Рабочее пространство'?'Навигация по админке':section.title}>{section.items.filter(path=>path!=='/premium-search'||premiumEnabled).map(path=><button key={path} className={route===path?'sidebar-link active':'sidebar-link'} onClick={()=>onNavigate(path)} aria-current={route===path?'page':undefined}><Icon name={icons[path]}/><span>{routeLabels[path]}</span></button>)}</nav></div>)}
    <div className="sidebar-footer"><div className="session-indicator"><span className="session-dot"/>Администраторская сессия активна</div><p>Безопасное управление Go Radar</p></div>
  </aside>;
}
