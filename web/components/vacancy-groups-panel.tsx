'use client';

import { useEffect, useState } from 'react';
import { api, type VacancyGroup, type VacancyGroupDetail } from '../lib/api';
import { ConfirmAction } from './confirm-action';

function date(value:string) { return new Date(value).toLocaleString('ru-RU'); }

export function VacancyGroupsPanel({onBack}:{onBack:()=>void}) {
  const [groups,setGroups]=useState<VacancyGroup[]>([]);
  const [selected,setSelected]=useState<VacancyGroupDetail>();
  const [pending,setPending]=useState<string>();
  const [error,setError]=useState('');
  const load=async()=>{try { setError(''); const response=await api.vacancyGroups(); setGroups(response.items); } catch(reason) { setError(reason instanceof Error?reason.message:'Не удалось загрузить группы'); }};
  const open=async(groupId:string)=>{try { setError(''); setSelected(await api.vacancyGroup(groupId)); } catch(reason) { setError(reason instanceof Error?reason.message:'Не удалось открыть группу'); }};
  useEffect(()=>{void load();},[]);
  const confirm=async()=>{if(!selected||!pending)return;try{await api.unlinkVacancyPublication(selected.group_id,pending);setPending(undefined);await load();setSelected(await api.vacancyGroup(selected.group_id));}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось разъединить публикацию');setPending(undefined);}};
  return <section className="screen groups-screen"><div className="section-title"><div><h1>Группы дублей</h1><p className="muted">Совпадения сгруппированы по подтверждённым публикациям.</p></div><div className="actions"><button className="button secondary" onClick={()=>void load()}>Обновить</button><button className="button secondary" onClick={onBack}>Назад</button></div></div>{error&&<p className="admin-alert" role="alert">{error}</p>}{groups.length===0?<div className="empty">Групп пока нет</div>:<div className="group-list">{groups.map(group=><button className="group-row" key={group.group_id} onClick={()=>void open(group.group_id)}><span className="group-mark" aria-hidden="true">{group.publication_count}</span><span className="group-copy"><strong>{group.title_key||'Название не указано'}</strong><span>{group.company_key||'Компания не указана'}</span><small>{date(group.first_seen_at)} — {date(group.last_seen_at)}</small></span><span className="group-count">{group.publication_count} {group.publication_count===1?'публикация':'публикаций'}</span><span className="group-open" aria-hidden="true">↗</span></button>)}</div>}{selected&&<section className="card group-detail"><div className="section-title"><div><h2>Публикации группы</h2><p className="muted">Каноническая публикация остаётся связанной. Для остальных доступно ручное разъединение.</p></div><button className="button secondary" onClick={()=>setSelected(undefined)}>Закрыть</button></div><div className="group-publications">{selected.publications.map(item=><article className="publication-row" key={item.vacancy_id}><div><strong>{item.is_canonical?'Каноническая':'Связанная'} публикация</strong><span>{item.merge_reason}</span><small>{item.channel_name||'Канал не указан'} · {date(item.published_at)}</small></div><div className="actions"><a className="button secondary" href={item.post_link} target="_blank" rel="noreferrer">Открыть</a>{!item.is_canonical&&<button className="button secondary" onClick={()=>setPending(item.vacancy_id)}>Разъединить</button>}</div></article>)}</div></section>}{pending&&<ConfirmAction title="Разъединить публикацию" description="Запись станет отдельной группой. Повторное автоматическое объединение этой пары будет заблокировано." onConfirm={()=>void confirm()} onCancel={()=>setPending(undefined)}/>}</section>;
}
