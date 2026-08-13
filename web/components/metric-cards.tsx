import React from 'react';
import type { Metrics } from '../lib/api';

const cards: Array<[keyof Metrics['counts'], string, string]> = [
  ['posts_processed', 'Постов обработано сегодня', 'Все сообщения, дошедшие до pipeline.'],
  ['vacancies_added', 'Вакансий добавлено сегодня', 'Успешно записаны в целевое хранилище.'],
  ['skipped', 'Пропущено', 'Дубликаты, нерелевантные и невалидные записи.'],
  ['errors', 'Ошибок сегодня', 'Ошибки обработки, зафиксированные pipeline.'],
];
const reasonLabels:Record<string,string> = {empty_text:'пустой текст',duplicate_link_or_id:'дубль ссылки/ID',duplicate_fingerprint:'дубль текста',include_prefilter:'без include-ключевого слова',exclude_keywords:'исключающее слово',candidate_resume:'резюме кандидата',live_queue_full:'переполненная очередь',llm_not_match:'LLM не подтвердил вакансию',llm_error:'ошибка LLM',export_error:'ошибка экспорта'};

export function MetricCards({ metrics, loading, error, onRefresh }: {metrics?:Metrics;loading:boolean;error:string;onRefresh:()=>void}) {
  return <section aria-labelledby="metrics-title" className="span-12"><div className="section-title"><div><h2 id="metrics-title">Сегодня</h2><p className="muted">{metrics?.description ?? 'Загружаем устойчивые счётчики обработки…'}</p></div><button className="button secondary" onClick={onRefresh} disabled={loading}>{loading?'Обновляем…':'Обновить'}</button></div>{error ? <p className="error" role="alert">Метрики временно недоступны: {error}</p> : <div className="metric-grid">{cards.map(([key,title,hint])=><article className="card metric" key={key}><p className="muted">{title}</p><strong>{loading && !metrics ? '—' : metrics?.counts[key] ?? 0}</strong><small>{hint}</small></article>)}</div>}{metrics && Object.keys(metrics.reasons).length>0 && <p className="muted small">Причины: {Object.entries(metrics.reasons).map(([reason,count])=>`${reasonLabels[reason]??reason} — ${count}`).join('; ')}.</p>}{metrics && <p className="muted small">Дата: {metrics.date}, часовой пояс: {metrics.timezone}.</p>}</section>;
}
