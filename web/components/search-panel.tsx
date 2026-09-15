'use client';

import { useEffect, useState } from 'react';
import { searchApi, type SearchCapabilities, type SearchParams, type SearchResult, type SearchResults, type SearchRun, type SearchSource } from '../lib/api';
import { ConfirmAction } from './confirm-action';

const sources: Record<SearchSource, string> = { telegram: 'Telegram · опубликованные', premium: 'Telegram Premium', threads: 'Threads' };
const states: Record<string, string> = { queued: 'В очереди', running: 'Выполняется', searching: 'Поиск', collecting: 'Загрузка постов', analyzing: 'Анализ вакансий', completed: 'Завершён', completed_with_errors: 'Завершён с частичными ошибками', cancelled: 'Отменён', failed: 'Ошибка источника', disabled: 'Отключён', skipped: 'Пропущен', rate_limited: 'Ожидание лимита', flood_wait: 'Ожидание лимита', accepted: 'Подходит', review: 'Нужна проверка', rejected: 'Отклонён', preview: 'Предпросмотр', saved: 'Сохранён', published: 'Опубликован', duplicate: 'Дубликат' };
const reasons: Record<string, string> = { permission_denied: 'Нет разрешения Threads на поиск публичных постов.', invalid_token: 'Токен Threads недействителен. Обновите его и перезапустите сервис.', token_not_configured: 'Токен Threads не настроен.', local_search_unavailable: 'Локальные опубликованные карточки недоступны.', daily_budget_exhausted: 'Дневной бюджет запросов Threads исчерпан.', invalid_threads_configuration: 'Проверьте настройки Threads в окружении.', classification_failed: 'Не удалось классифицировать текст. Нужна проверка.', feature_disabled: 'Источник отключён в настройках.', disabled: 'Источник отключён в настройках.', threads_disabled: 'Threads отключён в настройках.', premium_disabled: 'Premium поиск отключён в настройках.', missing_credentials: 'Источник не настроен.', threads_not_configured: 'Threads не настроен.', premium_required: 'Требуется Telegram Premium.', telegram_auth_required: 'Требуется авторизация Telegram.', free_quota_exhausted: 'Бесплатная квота исчерпана. Stars не списываются.', rate_limited: 'Достигнут лимит источника.', source_failed: 'Источник временно недоступен.', search_interrupted: 'Поиск прерван. Запустите обновление.', publisher_not_configured: 'Публикация не настроена.' };
const actionLabels = { save: 'Сохранить в таблицы', publish: 'Опубликовать в Candidate Bot', reject: 'Отклонить', mark_duplicate: 'Отметить дубликатом' };
type Action = keyof typeof actionLabels;
const active = (run?: SearchRun) => !!run && ['queued', 'running'].includes(run.status);
const formatDate = (value: string) => new Date(value).toLocaleString('ru-RU');
const safeReason = (value?: string | null) => value ? reasons[value] || 'Источник недоступен. Проверьте настройки и повторите поиск.' : '';
function originalLink(item: SearchResult): string | null {
  if (!item.permalink) return null;
  try { const url = new URL(item.permalink); return url.protocol === 'https:' ? url.href : null; } catch { return null; }
}
function runParams(run: SearchRun): SearchParams {
  return { query: run.query, sources: run.sources, track: 'go', mode: run.mode, result_limit: run.result_limit, period_days: run.period_days, include_review: run.include_review };
}

export function SearchPanel() {
  const [capabilities, setCapabilities] = useState<SearchCapabilities>();
  const [params, setParams] = useState<SearchParams>({ query: '', sources: [], track: 'go', mode: 'preview', result_limit: 50, period_days: 7, include_review: true });
  const [runs, setRuns] = useState<SearchRun[]>([]);
  const [id, setId] = useState('');
  const [run, setRun] = useState<SearchRun>();
  const [results, setResults] = useState<SearchResults>();
  const [offset, setOffset] = useState(0);
  const [includeReview, setIncludeReview] = useState(true);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<{ title: string; description: string; execute: () => Promise<void> }>();

  useEffect(() => {
    let disposed = false;
    void Promise.all([searchApi.capabilities(), searchApi.runs()]).then(([caps, history]) => {
      if (disposed) return;
      setCapabilities(caps);
      setParams(value => ({ ...value, sources: caps.sources.filter(source => source.enabled).map(source => source.key) }));
      setRuns(history.items);
      setId(history.items[0]?.id || '');
    }).catch(() => { if (!disposed) setError('Не удалось загрузить поиск. Обновите страницу.'); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    if (!id) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const [next, page] = await Promise.all([searchApi.run(id), searchApi.results(id, offset, includeReview)]);
        if (disposed) return;
        setRun(next); setResults(page); setError('');
        setRuns(items => items.map(item => item.id === next.id ? next : item));
        if (active(next) || page.items.some(item => ['queued', 'running'].includes(item.action_state))) timer = setTimeout(() => void load(), 2000);
      } catch { if (!disposed) { setError('Не удалось обновить результаты. Повторяем загрузку…'); timer = setTimeout(() => void load(), 2000); } }
    };
    void load();
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, [id, offset, includeReview, revision]);

  const create = (requested: SearchParams) => {
    const next = { ...requested, query: requested.query.trim().replace(/\s+/g, ' ') };
    if (next.query.length < 3 || next.query.length > 160 || !next.sources.length || !Number.isInteger(next.result_limit) || next.result_limit < 1 || next.result_limit > 100 || !Number.isInteger(next.period_days) || next.period_days < 1 || next.period_days > 30) {
      setError('Укажите запрос от 3 до 160 символов, хотя бы один источник, 1–100 результатов и 1–30 дней.'); return;
    }
    setConfirmation({ title: 'Запустить общий поиск', description: `${next.query} · ${next.sources.map(source => sources[source]).join(', ')}. ${next.mode === 'preview' ? 'Предпросмотр без записи.' : next.mode === 'save' ? 'Вакансии будут сохранены в Google Sheets.' : 'Вакансии будут сохранены и опубликованы в Candidate Bot.'}`, execute: async () => {
      const created = await searchApi.create(next);
      setRuns(items => [created, ...items.filter(item => item.id !== created.id)]);
      setRun(created); setResults(undefined); setId(created.id); setOffset(0); setIncludeReview(next.include_review); setNotice('Поиск поставлен в очередь.');
    } });
  };
  const action = (item: SearchResult, name: Action) => setConfirmation({ title: actionLabels[name], description: name === 'publish' ? 'Сохраним вакансию в таблицы и отправим карточку в Candidate Bot.' : name === 'save' ? 'Запишем вакансию в Google Sheets.' : 'Сохраним решение в истории поиска.', execute: async () => {
    await searchApi.action(item.id, name); setNotice('Действие принято. Обновляем статус…'); setRevision(value => value + 1);
  } });
  const execute = async () => {
    if (!confirmation || busy) return;
    setBusy(true); setError('');
    try { await confirmation.execute(); } catch { setError('Не удалось выполнить действие. Проверьте доступность источников и повторите.'); }
    finally { setBusy(false); setConfirmation(undefined); }
  };

  return <section className="screen premium-search">
    <header className="premium-hero"><p className="premium-eyebrow">Telegram · Premium · Threads</p><h1>Общий поиск вакансий</h1><p>Один запрос по выбранным источникам. Результаты появляются по мере готовности каждого источника.</p><p>Локальный Telegram ищет только опубликованные карточки Candidate Bot. Старые записи только из Sheets в этот поиск не входят.</p></header>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="premium-notice" role="status">{notice}</p>}
    <form className="card premium-launcher" onSubmit={event => { event.preventDefault(); create(params); }}>
      <h2>Настройте поиск</h2>
      <fieldset className="search-source-picker"><legend>Источники поиска</legend>{capabilities?.sources.map(source => <div key={source.key}><label className="checkbox"><input type="checkbox" checked={params.sources.includes(source.key)} disabled={!source.enabled || busy} onChange={event => setParams(value => ({ ...value, sources: event.target.checked ? [...value.sources, source.key] : value.sources.filter(key => key !== source.key) }))}/>{sources[source.key]}</label>{!source.enabled && <p className="muted">{safeReason(source.reason) || 'Источник отключён.'}</p>}</div>)}</fieldset>
      <div className="premium-form-grid"><label className="premium-query">Поисковый запрос<input value={params.query} placeholder="Например, Golang backend" minLength={3} maxLength={160} required onChange={event => setParams({ ...params, query: event.target.value })}/></label>
        <label>Трек<select value="go" disabled><option value="go">Go / Golang</option></select></label>
        <label>Результатов<input type="number" min={1} max={100} required value={params.result_limit} onChange={event => setParams({ ...params, result_limit: Number(event.target.value) })}/></label>
        <label>Возраст, дней<input type="number" min={1} max={30} required value={params.period_days} onChange={event => setParams({ ...params, period_days: Number(event.target.value) })}/></label>
        <label>Режим<select value={params.mode} onChange={event => setParams({ ...params, mode: event.target.value as SearchParams['mode'] })}><option value="preview">Предпросмотр</option><option value="save">Сохранить</option><option value="save_publish" disabled={!capabilities?.publisher_configured}>Сохранить и опубликовать</option></select></label>
      </div>
      <div className="premium-form-footer"><label className="checkbox"><input type="checkbox" checked={params.include_review} onChange={event => setParams({ ...params, include_review: event.target.checked })}/>Включать результаты для проверки</label><button className="button" disabled={busy || !capabilities || !params.sources.length}>Запустить поиск</button></div>
    </form>
    <section className="card premium-history"><label>История поиска<select value={id} onChange={event => { setId(event.target.value); setRun(undefined); setResults(undefined); setOffset(0); }}><option value="">Выберите запуск</option>{runs.map(item => <option key={item.id} value={item.id}>{item.query} · {formatDate(item.created_at)}</option>)}</select></label>{!runs.length && <p className="muted">Поисков ещё нет. Начните с предпросмотра.</p>}</section>
    {run && <>
      <section className="card premium-run" aria-label="Ход поиска"><div className="premium-run-heading"><h2>{run.query}</h2><span className="premium-badge" role="status">{states[run.status] || 'Обновление статуса'}</span></div>
        <div className="search-source-states">{run.sources.map(source => { const state = run.source_states[source]; return <div className="card" key={source} aria-label={`Статус ${sources[source]}`}><strong>{sources[source]}</strong><p role="status">{states[state?.status || 'queued'] || 'Обработка'}</p>{state?.reason && <p className="muted">{safeReason(state.reason)}</p>}{state?.retry_at && <p>Повтор после {formatDate(state.retry_at)}</p>}</div>; })}</div>
        <div className="actions"><button className="button secondary" disabled={busy} onClick={() => create(runParams(run))}>Обновить поиск</button>{active(run) && <button className="button secondary" disabled={busy} onClick={() => setConfirmation({ title: 'Отменить поиск', description: 'Уже полученные результаты останутся в истории.', execute: async () => { setRun(await searchApi.cancel(run.id)); setRevision(value => value + 1); } })}>Отменить поиск</button>}</div>
      </section>
      <section className="premium-result-section"><div className="premium-results-heading"><h2>Найденные вакансии <span>{results?.total ?? 0}</span></h2><label className="checkbox"><input type="checkbox" checked={includeReview} onChange={event => { setIncludeReview(event.target.checked); setOffset(0); }}/>Показывать неуверенные результаты</label></div>
        {!results?.items.length && <div className="card premium-empty">{active(run) ? 'Поиск продолжается. Результаты появятся здесь.' : 'Результатов пока нет.'}</div>}
        <div className="premium-results">{results?.items.map(item => {
          const title = item.title || 'Пост без названия'; const link = originalLink(item); const pending = ['queued', 'running'].includes(item.action_state); const immutable = ['published', 'duplicate'].includes(item.publication_status);
          return <article className="card premium-result-card" key={item.id}><div className="premium-result-top"><div><p className="premium-source">{sources[item.source]}{item.username ? ` · @${item.username}` : ''}</p><h3>{title}</h3>{item.company && <p className="premium-company">{item.company}</p>}</div><span className="premium-badge">{states[item.classification]}</span></div>
            <p className="premium-summary">{item.summary || item.text}</p><div className="premium-meta"><span>{formatDate(item.timestamp)}</span><span>{item.language.toUpperCase()}</span><span>{states[item.publication_status]}</span>{item.confidence !== null && <span>Уверенность: {Math.round(item.confidence * 100)}%</span>}</div>
            {item.found_queries.length > 0 && <p className="muted">Найдено по запросам: {item.found_queries.join(', ')}</p>}
            <div className="premium-result-footer">{link ? <a className="premium-telegram-link" href={link} target="_blank" rel="noreferrer">Открыть оригинал ↗</a> : <span className="muted">Ссылка на оригинал недоступна</span>}
              {!immutable && <label>Действия<select aria-label={`Действия с вакансией «${title}»`} defaultValue="" disabled={busy || pending} onChange={event => { if (event.target.value) action(item, event.target.value as Action); event.currentTarget.value = ''; }}><option value="">Выберите действие</option>{item.can_persist && item.publication_status !== 'saved' && <option value="save">{actionLabels.save}</option>}{item.can_persist && <option value="publish" disabled={!capabilities?.publisher_configured}>{actionLabels.publish}</option>}{item.publication_status === 'preview' && <><option value="reject">{actionLabels.reject}</option><option value="mark_duplicate">{actionLabels.mark_duplicate}</option></>}</select></label>}
            </div>{pending && <p role="status">Действие {item.action_state === 'queued' ? 'в очереди' : 'выполняется'}…</p>}{(item.action_error || item.action_state === 'failed') && <p className="error" role="alert">Не удалось выполнить действие. Повторите позже.</p>}
          </article>;
        })}</div>
        <div className="premium-pagination"><button className="button secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Назад</button><span>{results?.total ? `${offset + 1}–${Math.min(offset + 25, results.total)} из ${results.total}` : '—'}</span><button className="button secondary" disabled={offset + 25 >= (results?.total ?? 0)} onClick={() => setOffset(offset + 25)}>Далее</button></div>
      </section>
    </>}
    {confirmation && <ConfirmAction title={confirmation.title} description={busy ? 'Выполняется…' : confirmation.description} onConfirm={() => void execute()} onCancel={() => { if (!busy) setConfirmation(undefined); }}/ >}
  </section>;
}
