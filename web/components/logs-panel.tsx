'use client';

import React,{useEffect,useState} from 'react';
import {api,type LogsPage} from '../lib/api';

const read=()=>new URLSearchParams(window.location.search);
const update=(values:Record<string,string|number>)=>{
  const query=new URLSearchParams(Object.entries(values).filter(([,value])=>value!=='').map(([key,value])=>[key,String(value)]));
  window.history.replaceState({},'',`/logs${query.toString()?`?${query}`:''}`);
};

export function LogsPanel({onBack}:{onBack:()=>void}) {
  const initial=read();
  const [data,setData]=useState<LogsPage>();
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(true);
  const [level,setLevel]=useState(initial.get('level')??'');
  const [component,setComponent]=useState(initial.get('component')??'');
  const [period,setPeriod]=useState(initial.get('period')??'7d');
  const [search,setSearch]=useState(initial.get('search')??'');
  const [offset,setOffset]=useState(Number(initial.get('offset')??0));
  const load=async(nextOffset=offset)=>{
    setLoading(true);setError('');
    try{
      update({level,component,period,search,offset:nextOffset});
      setData(await api.logs({level,component,period,search,offset:nextOffset,limit:30}));
      setOffset(nextOffset);
    }catch(reason){setError(reason instanceof Error?reason.message:'Сервис логов недоступен');}
    finally{setLoading(false);}
  };
  useEffect(()=>{void load(0)},[level,component,period]);
  return <section className="screen logs-screen">
    <div className="screen-heading"><div><button className="text-button" onClick={onBack}>← Обзор</button><h1>Журнал событий</h1><p className="muted">События обработки с фильтрами по уровню, компоненту и периоду.</p></div><button className="button secondary" onClick={()=>void load(0)} disabled={loading}>{loading?'Обновляем…':'Обновить'}</button></div>
    <div className="log-filters">
      <label>Уровень<select aria-label="Уровень лога" value={level} onChange={event=>setLevel(event.target.value)}><option value="">Все уровни</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select></label>
      <label>Компонент<select aria-label="Компонент лога" value={component} onChange={event=>setComponent(event.target.value)}><option value="">Все компоненты</option><option value="telegram">Telegram</option><option value="llm">LLM</option><option value="google_sheets">Google Sheets</option><option value="configuration">Конфигурация</option><option value="source">Источник</option><option value="pipeline">Pipeline</option></select></label>
      <label>Период<select aria-label="Период логов" value={period} onChange={event=>setPeriod(event.target.value)}><option value="today">Сегодня</option><option value="7d">7 дней</option><option value="all">Все доступные</option></select></label>
      <label className="log-search">Поиск<input aria-label="Поиск по логам" value={search} onChange={event=>setSearch(event.target.value)} onKeyDown={event=>{if(event.key==='Enter')void load(0)}} placeholder="Текст события или компонента"/><button className="button secondary" onClick={()=>void load(0)}>Найти</button></label>
    </div>
    {error?<p className="error" role="alert">Логи временно недоступны: {error}</p>:loading&&!data?<p className="muted">Загружаем события…</p>:!data?.items.length?<div className="empty">За выбранный период безопасных событий пока нет.</div>:<>
      <section className="logs-terminal" aria-label="Поток логов">
        <div className="logs-terminal-head"><div className="terminal-mark" aria-hidden="true"><i/><i/><i/></div><span>ЖУРНАЛ ПРИЛОЖЕНИЯ</span><span className="log-total">{data.total} событий</span></div>
        <div className="logs-terminal-body">{data.items.map((item,index)=><article className={`log-entry level-${item.level.toLowerCase()}`} key={`${item.at}-${index}`}>
          <time>{new Date(item.at).toLocaleString('ru-RU')}</time><span className={`log-level ${item.level.toLowerCase()}`}>{item.level}</span><span className="log-component">{item.component}</span><p>{item.message}</p>
        </article>)}</div>
        <div className="logs-terminal-foot"><span>Показано {data.offset+1}–{Math.min(data.offset+data.items.length,data.total)} из {data.total}</span><span>Временная зона устройства</span></div>
      </section>
      <div className="pagination"><button className="button secondary" disabled={!offset||loading} onClick={()=>void load(Math.max(0,offset-30))}>Назад</button><button className="button secondary" disabled={loading||offset+30>=data.total} onClick={()=>void load(offset+30)}>Далее</button></div>
    </>}
  </section>;
}
