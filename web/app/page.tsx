'use client';

import { useCallback, useEffect, useState } from 'react';
import { AttentionPanel } from '../components/attention-panel';
import { ConfirmAction } from '../components/confirm-action';
import { LoginForm } from '../components/login-form';
import { LogsPanel } from '../components/logs-panel';
import { MetricCards } from '../components/metric-cards';
import { PromptEditor } from '../components/prompt-editor';
import { SettingsForm } from '../components/settings-form';
import { SourcesPanel } from '../components/sources-panel';
import { VacancyGroupsPanel } from '../components/vacancy-groups-panel';
import { api, type AttentionError, type Metrics, type Settings } from '../lib/api';

type Dashboard = {
  settings_revision:number;
  latest_export_at?:string|null;
  channel_count:number;
  heartbeat?:{status?:string;updated_at?:string;settings_revision?:number;queue_size?:number;received_messages?:number;keyword_matches?:number;saved_matches?:number};
  operations:{at:string;event:string;action?:string}[];
  secret_status:{telegram:boolean;mistral:boolean;google_sheets:boolean};
};
type Route = '/'|'/sources'|'/groups'|'/settings'|'/prompt'|'/logs'|'/errors';
const routeLabels:Record<Route,string> = {'/':'Дашборд','/sources':'Источники','/groups':'Группы','/settings':'Настройки','/prompt':'LLM-инструкции','/logs':'Логи','/errors':'Ошибки'};
const actionText: Record<string,[string,string]> = {
  restart:['Перезапустить бота','Очередь будет корректно завершена, затем бот применит сохранённые настройки.'],
  history:['Запустить историю','Live-мониторинг временно остановится: один Telegram session нельзя использовать одновременно.'],
  sync_channels:['Синхронизировать папку','Бот кратко отключится от Telegram, прочитает папку и применит её состав.'],
};

function currentRoute():Route {
  const path = window.location.pathname.replace(/\/$/, '') || '/';
  return Object.prototype.hasOwnProperty.call(routeLabels,path) ? path as Route : '/';
}

function navigate(path:Route) { window.history.pushState({},'',path); window.dispatchEvent(new PopStateEvent('popstate')); }
function formatDate(value?:string|null) { return value ? new Date(value).toLocaleString('ru-RU') : 'нет данных'; }

function StatusCard({dashboard, errors, onNavigate}:{dashboard?:Dashboard;errors:AttentionError[];onNavigate:(path:Route)=>void}) {
  const heartbeat=dashboard?.heartbeat;
  const heartbeatAge=heartbeat?.updated_at ? Date.now()-new Date(heartbeat.updated_at).getTime() : Number.POSITIVE_INFINITY;
  const heartbeatFresh=heartbeatAge<=180000;
  const running=heartbeat?.status==='running' && heartbeatFresh;
  const paused=heartbeat?.status==='paused';
  const label=running?'Активен':paused?'Приостановлен':'Нет heartbeat';
  const tone=running?'ok':paused?'muted':'error';
  const pendingRestart=typeof heartbeat?.settings_revision==='number' && heartbeat.settings_revision !== dashboard?.settings_revision;
  const action = !running ? 'Проверьте ошибки и логи, затем подтвердите перезапуск при необходимости.' : pendingRestart ? 'Есть сохранённые изменения: подтвердите перезапуск, чтобы применить revision.' : errors.length ? 'Откройте активные ошибки и проверьте безопасные детали.' : 'Мониторинг получает сообщения и записывает агрегированные показатели.';
  return <section className="card span-12 status-card" aria-labelledby="system-status"><div className="section-title"><div><p className={tone}><strong id="system-status">● {label}</strong></p><h1>Состояние обработки</h1><p className="muted">Heartbeat: {formatDate(heartbeat?.updated_at)} · revision {dashboard?.settings_revision??0}{pendingRestart?' · ожидает применения':''}</p></div><button className="button secondary" onClick={()=>onNavigate('/logs')}>Открыть логи</button></div><div className="status-summary"><div><strong>Что произошло</strong><p>{running?'Бот сообщает heartbeat и готов к live-обработке.':paused?'Мониторинг сохранён в паузе и не получает новые сообщения.':'Свежего статуса от live-процесса нет.'}</p></div><div><strong>Почему важно</strong><p>{pendingRestart?'Сохранённая конфигурация ещё не используется ботом.':!running?'Вакансии могут не обрабатываться, пока процесс не вернётся в норму.':'Очередь и ошибки ниже помогают заметить деградацию до потери сообщений.'}</p></div><div><strong>Что можно сделать</strong><p>{action}</p></div></div></section>;
}

function DashboardView({dashboard,metrics,errors,loading,onNavigate,onRefresh,onResolve}:{dashboard?:Dashboard;metrics?:Metrics;errors:AttentionError[];loading:boolean;onNavigate:(path:Route)=>void;onRefresh:()=>void;onResolve:(id:string)=>Promise<void>}) {
  const heartbeat=dashboard?.heartbeat;
  return <><StatusCard dashboard={dashboard} errors={errors} onNavigate={onNavigate}/><section className="quick-grid span-12" aria-label="Ключевые показатели"><button className="card link-card" onClick={()=>onNavigate('/logs')}><span>Последний экспорт</span><strong>{formatDate(dashboard?.latest_export_at)}</strong><small>Последняя успешная запись вакансии</small></button><button className="card link-card" onClick={()=>onNavigate('/sources')}><span>Очередь</span><strong>{heartbeat?.queue_size ?? '—'}</strong><small>Источников: {dashboard?.channel_count??0}</small></button><button className="card link-card" onClick={()=>onNavigate('/errors')}><span>Пропущено сегодня</span><strong>{metrics?.counts.skipped ?? '—'}</strong><small>Открыть причины и ошибки</small></button><button className="card link-card" onClick={()=>onNavigate('/errors')}><span>Активные ошибки</span><strong>{errors.length}</strong><small>Требуют внимания</small></button></section><MetricCards metrics={metrics} loading={loading} error="" onRefresh={onRefresh}/><AttentionPanel errors={errors} loading={loading} onResolve={onResolve} onOpenErrors={()=>onNavigate('/errors')}/></>;
}

export default function Page() {
  const [ready,setReady]=useState(false); const [configured,setConfigured]=useState(false); const [settings,setSettings]=useState<Settings>(); const [dashboard,setDashboard]=useState<Dashboard>(); const [metrics,setMetrics]=useState<Metrics>(); const [attention,setAttention]=useState<AttentionError[]>([]); const [loading,setLoading]=useState(false); const [error,setError]=useState(''); const [pending,setPending]=useState(''); const [route,setRoute]=useState<Route>('/'); const [theme,setTheme]=useState<'light'|'dark'>('light');
  const refresh = useCallback(async()=>{setLoading(true);setError('');try{const [nextSettings,nextDashboard,nextMetrics,nextErrors]=await Promise.all([api.settings(),api.dashboard(),api.metrics(),api.errors()]);setSettings(nextSettings);setDashboard(nextDashboard);setMetrics(nextMetrics);setAttention(nextErrors);}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось загрузить данные панели');}finally{setLoading(false);}},[]);
  useEffect(()=>{const change=()=>setRoute(currentRoute());change();window.addEventListener('popstate',change);api.status().then(async status=>{setConfigured(status.configured);if(status.authenticated) await refresh();setReady(true);}).catch(()=>setReady(true));return()=>window.removeEventListener('popstate',change);},[refresh]);
  useEffect(()=>{const stored=window.localStorage.getItem('admin-theme');if(stored==='dark')setTheme('dark');},[]);
  useEffect(()=>{document.documentElement.classList.toggle('dark',theme==='dark');window.localStorage.setItem('admin-theme',theme);},[theme]);
  const go=(path:Route)=>navigate(path);
  if(!ready) return <main className="shell">Загружаем…</main>;
  if(!settings) return <LoginForm configured={configured} onLogin={async(password)=>{await api.login(password);await refresh();}}/>;
  const save=async(value:Partial<Settings>)=>{setError('');try{const saved=await api.save(value);setSettings(saved);setDashboard(current=>current?{...current,settings_revision:saved.revision}:current);}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось сохранить настройки');throw reason;}};
  const resolve=async(id:string)=>{try{await api.resolveError(id);await refresh();}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось обновить статус ошибки');}};
  const doAction=async()=>{try{await api.action(pending);setPending('');await refresh();}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось выполнить действие');setPending('');}};
  const restartRequired=typeof dashboard?.heartbeat?.settings_revision==='number' && dashboard.heartbeat.settings_revision!==settings.revision;
  const header=<Header route={route} theme={theme} onToggleTheme={()=>setTheme(theme==='light'?'dark':'light')} onNavigate={go} onLogout={async()=>{await api.logout();setSettings(undefined);go('/');}}/>;
  let content:React.ReactNode;
  if(route==='/sources') content=<SourcesPanel onBack={()=>go('/')} onChanged={refresh} restartRequired={restartRequired}/>;
  else if(route==='/groups') content=<VacancyGroupsPanel onBack={()=>go('/')} />;
  else if(route==='/settings') content=<SettingsForm settings={settings} onSave={save}/>;
  else if(route==='/prompt') content=<PromptEditor onBack={()=>go('/settings')}/>;
  else if(route==='/logs') content=<LogsPanel onBack={()=>go('/')} />;
  else if(route==='/errors') content=<section className="screen"><div className="section-title"><div><h1>Активные ошибки</h1><p className="muted">Показаны только неразрешённые, безопасно очищенные ошибки. Повтор пока не поддерживается.</p></div><button className="button secondary" onClick={()=>void refresh()} disabled={loading}>Обновить</button></div><AttentionPanel errors={attention} loading={loading} onResolve={resolve} showEmpty/></section>;
  else content=<DashboardView dashboard={dashboard} metrics={metrics} errors={attention} loading={loading} onNavigate={go} onRefresh={()=>void refresh()} onResolve={resolve}/>;
  return <main className="shell">{header}{error&&<p className="error" role="alert">{error}</p>}<div className="page-content">{content}</div>{route==='/'&&<section className="card span-12"><h2>Управление обработкой</h2><p className="muted">Опасные операции потребуют явного подтверждения.</p><div className="actions"><button className="button" onClick={()=>setPending('sync_channels')}>Синхронизировать папку</button><button className="button secondary" onClick={()=>setPending('history')}>Запустить историю</button><button className="button danger" onClick={()=>setPending('restart')}>Перезапустить бота</button></div></section>}{pending&&<ConfirmAction title={actionText[pending][0]} description={actionText[pending][1]} onConfirm={()=>void doAction()} onCancel={()=>setPending('')}/>}</main>;
}

function Header({route,theme,onToggleTheme,onNavigate,onLogout}:{route:Route;theme:'light'|'dark';onToggleTheme:()=>void;onNavigate:(path:Route)=>void;onLogout:()=>void}) { return <header className="app-header"><div><button className="brand link" onClick={()=>onNavigate('/')}>Go Radar</button><p className="muted">Закрытое управление сбором вакансий</p></div><nav aria-label="Основная навигация">{(Object.keys(routeLabels) as Route[]).map(path=><button key={path} className={route===path?'nav-active':'nav-item'} onClick={()=>onNavigate(path)} aria-current={route===path?'page':undefined}>{routeLabels[path]}</button>)}</nav><div className="actions"><button className="button secondary" onClick={onToggleTheme} aria-label="Переключить цветовую тему">{theme==='light'?'Тёмная тема':'Светлая тема'}</button><button className="button secondary" onClick={onLogout}>Выйти</button></div></header>; }
