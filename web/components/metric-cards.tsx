import React from 'react';
import type { Metrics } from '../lib/api';

const cards: Array<[keyof Metrics['counts'], string, string]> = [
  ['posts_processed', 'Постов обработано сегодня', 'Все сообщения, дошедшие до pipeline.'],
  ['vacancies_added', 'Вакансий добавлено сегодня', 'Успешно записаны в целевое хранилище.'],
  ['skipped', 'Пропущено', 'Дубликаты, нерелевантные и невалидные записи.'],
  ['errors', 'Ошибок сегодня', 'Ошибки обработки, зафиксированные pipeline.'],
];

export function MetricCards({ metrics, loading, error, onRefresh }: {metrics?:Metrics;loading:boolean;error:string;onRefresh:()=>void}) {
  return <section aria-labelledby="metrics-title" className="span-12"><div className="section-title"><div><h2 id="metrics-title">Сегодня</h2><p className="muted">{metrics?.description ?? 'Загружаем устойчивые счётчики обработки…'}</p></div><button className="button secondary" onClick={onRefresh} disabled={loading}>{loading?'Обновляем…':'Обновить'}</button></div>{error ? <p className="error" role="alert">Метрики временно недоступны: {error}</p> : <div className="metric-grid">{cards.map(([key,title,hint])=><article className="card metric" key={key}><p className="muted">{title}</p><strong>{loading && !metrics ? '—' : metrics?.counts[key] ?? 0}</strong><small>{hint}</small></article>)}</div>}{metrics && <p className="muted small">Дата: {metrics.date}, часовой пояс: {metrics.timezone}.</p>}</section>;
}
