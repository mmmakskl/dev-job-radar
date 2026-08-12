'use client';

import { useEffect, useState } from 'react';
import { AttentionPanel } from '../components/attention-panel';
import { ConfirmAction } from '../components/confirm-action';
import { LoginForm } from '../components/login-form';
import { LogsPanel } from '../components/logs-panel';
import { MetricCards } from '../components/metric-cards';
import { PromptEditor } from '../components/prompt-editor';
import { SettingsForm } from '../components/settings-form';
import { SourcesPanel } from '../components/sources-panel';
import { api, type AttentionError, type Metrics, type Settings } from '../lib/api';

type Dashboard = { settings_revision:number; channel_count:number; heartbeat?:{status?:string;updated_at?:string;queue_size?:number;received_messages?:number;keyword_matches?:number;saved_matches?:number}; operations:{at:string;event:string;action?:string}[]; secret_status:{telegram:boolean;mistral:boolean;google_sheets:boolean} };
type Screen = 'dashboard'|'logs'|'sources'|'prompt';
const actionText: Record<string,[string,string]> = { restart:['Перезапустить бота','Текущая очередь корректно завершится, затем бот поднимется с сохранёнными настройками.'], history:['Запустить историю','Live-мониторинг временно остановится: один Telegram session нельзя использовать одновременно.'], sync_channels:['Синхронизировать папку','Бот кратко отключится от Telegram, прочитает папку и применит её состав.'] };

export default function Page() {
  const [ready,setReady]=useState(false); const [configured,setConfigured]=useState(false); const [settings,setSettings]=useState<Settings>(); const [dashboard,setDashboard]=useState<Dashboard>(); const [metrics,setMetrics]=useState<Metrics>(); const [attention,setAttention]=useState<AttentionError[]>([]); const [metricLoading,setMetricLoading]=useState(false); const [metricError,setMetricError]=useState(''); const [error,setError]=useState(''); const [pending,setPending]=useState(''); const [screen,setScreen]=useState<Screen>('dashboard');
  const refreshDashboard = async () => { const [nextSettings,nextDashboard] = await Promise.all([api.settings(),api.dashboard()]); setSettings(nextSettings); setDashboard(nextDashboard); };
  const refreshMetrics = async () => { setMetricLoading(true);setMetricError('');try{const [nextMetrics,nextErrors]=await Promise.all([api.metrics(),api.errors()]);setMetrics(nextMetrics);setAttention(nextErrors)}catch(reason){setMetricError(reason instanceof Error?reason.message:'Не удалось загрузить данные')}finally{setMetricLoading(false)} };
  const load = async () => { await Promise.all([refreshDashboard(),refreshMetrics()]); };
  useEffect(()=>{ api.status().then(async(status)=>{setConfigured(status.configured); if(status.authenticated) await load(); setReady(true);}).catch(()=>setReady(true)); },[]);
  const authenticated=Boolean(settings);
  if(!ready) return <main className="shell">Загружаем…</main>;
  if(!authenticated) return <LoginForm configured={configured} onLogin={async(password)=>{await api.login(password); await load();}} />;
  async function save(value:Partial<Settings>) { try { setError(''); const next=await api.save(value); setSettings(next); } catch(reason) { setError(reason instanceof Error ? reason.message : 'Не удалось сохранить настройки'); } }
  async function doAction() { try { await api.action(pending); setPending(''); await refreshDashboard(); } catch(reason) { setError(reason instanceof Error ? reason.message : 'Не удалось выполнить действие'); setPending(''); } }
  async function resolveError(id:string) { try { await api.resolveError(id); await refreshMetrics(); } catch(reason) { setError(reason instanceof Error ? reason.message : 'Не удалось обновить статус ошибки'); } }
  const heartbeat=dashboard?.heartbeat;
  const header=<Header onScreen={setScreen} onLogout={async()=>{await api.logout();setSettings(undefined)}} />;
  if(screen==='logs') return <main className="shell">{header}<LogsPanel onBack={()=>setScreen('dashboard')} /></main>;
  if(screen==='sources') return <main className="shell">{header}<SourcesPanel onBack={()=>setScreen('dashboard')} onChanged={refreshDashboard} /></main>;
  if(screen==='prompt') return <main className="shell">{header}<PromptEditor onBack={()=>setScreen('dashboard')} /></main>;
  return <main className="shell">{header}{error&&<p className="error" role="alert">{error}</p>}<nav className="subnav" aria-label="Разделы панели"><button className="button secondary" onClick={()=>setScreen('sources')}>Источники ({dashboard?.channel_count??0})</button><button className="button secondary" onClick={()=>setScreen('prompt')}>LLM-инструкции</button><button className="button secondary" onClick={()=>setScreen('logs')}>Логи</button></nav><section className="grid"><MetricCards metrics={metrics} loading={metricLoading} error={metricError} onRefresh={()=>void refreshMetrics()} /><AttentionPanel errors={attention} loading={metricLoading} onResolve={resolveError}/><article className="card span-4"><div className={heartbeat?.status==='running'?'ok':'muted'}>{heartbeat?.status==='running'?'● Бот активен':'● Нет свежего статуса'}</div><h2>Режим мониторинга</h2><p className="muted">Конфигурация revision {dashboard?.settings_revision??0}</p></article><article className="card span-4"><h2>Интеграции</h2><p>Telegram: {dashboard?.secret_status.telegram?'настроен':'не настроен'}</p><p>Mistral: {dashboard?.secret_status.mistral?'настроен':'не настроен'}</p><p>Google Sheets: {dashboard?.secret_status.google_sheets?'настроен':'не настроен'}</p></article><article className="card span-4"><h2>Действия</h2><div className="actions"><button className="button" onClick={()=>setPending('sync_channels')}>Синхронизировать</button><button className="button secondary" onClick={()=>setPending('history')}>История</button><button className="button danger" onClick={()=>setPending('restart')}>Перезапуск</button></div></article><article className="card span-12"><h2>Telegram и фильтрация</h2>{settings&&<SettingsForm settings={settings} onSave={save}/>}</article><article className="card span-6"><h2>Статус процесса</h2><p>Очередь: {heartbeat?.queue_size??'—'}</p><p>Получено: {heartbeat?.received_messages??'—'} · с ключевыми словами: {heartbeat?.keyword_matches??'—'} · сохранено: {heartbeat?.saved_matches??'—'}</p><p className="muted">Обновлено: {heartbeat?.updated_at?new Date(heartbeat.updated_at).toLocaleString('ru-RU'):'нет данных'}</p></article><article className="card span-6"><h2>История операций</h2>{dashboard?.operations.length?<ul>{dashboard.operations.map((item,index)=><li key={`${item.at}-${index}`}>{new Date(item.at).toLocaleString('ru-RU')} — {item.event}{item.action?`: ${item.action}`:''}</li>)}</ul>:<p className="muted">Операций пока нет</p>}</article></section>{pending&&<ConfirmAction title={actionText[pending][0]} description={actionText[pending][1]} onConfirm={doAction} onCancel={()=>setPending('')}/>}</main>;
}

function Header({onScreen,onLogout}:{onScreen:(screen:Screen)=>void;onLogout:()=>Promise<void>}) { return <header className="top"><div><button className="brand link" onClick={()=>onScreen('dashboard')}>Go Radar</button><div className="muted">Безопасное управление сбором вакансий</div></div><div className="actions"><button className="button secondary" onClick={()=>document.documentElement.classList.toggle('dark')}>Тема</button><button className="button secondary" onClick={()=>void onLogout()}>Выйти</button></div></header>; }
