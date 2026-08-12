import React, { useState } from 'react';
import type { AttentionError } from '../lib/api';

const labels:Record<string,string> = {telegram:'Telegram',llm:'LLM',google_sheets:'Google Sheets',configuration:'Конфигурация',source:'Источник',pipeline:'Pipeline',other:'Другое'};
const states:Record<string,string> = {new:'новая',repeating:'повторяется',resolved:'решена'};
export function AttentionPanel({ errors, loading, onResolve }: {errors:AttentionError[];loading:boolean;onResolve:(id:string)=>Promise<void>}) {
  const [opened,setOpened] = useState<string>();
  if (!loading && !errors.length) return null;
  return <article className="card span-12 attention" aria-labelledby="attention-title"><div className="section-title"><div><h2 id="attention-title">Требуют внимания</h2><p className="muted">Показываются только актуальные безопасно очищенные ошибки.</p></div></div>{loading ? <p className="muted">Проверяем ошибки…</p> : <div className="stack">{errors.map((item)=><div className="attention-item" key={item.id}><div><strong>{labels[item.component] ?? item.component}</strong> <span className="status">{states[item.status]}</span><p>{item.summary}</p><small className="muted">{new Date(item.last_seen_at).toLocaleString('ru-RU')}{item.count > 1 ? ` · повторов: ${item.count}` : ''}</small>{opened===item.id && <pre className="technical">{item.details}</pre>}</div><div className="actions"><button className="button secondary" aria-expanded={opened===item.id} onClick={()=>setOpened(opened===item.id?undefined:item.id)}>Детали</button><button className="button secondary" onClick={()=>onResolve(item.id)}>Обновить статус</button></div></div>)}</div>}</article>;
}
