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
  return <section className="screen"><div className="section-title"><div><h1>Группы дублей</h1><p className="muted">Группировка консервативна: похожий текст или стек сами по себе не объединяют вакансии.</p></div><div className="actions"><button className="button secondary" onClick={()=>void load()}>Обновить</button><button className="button secondary" onClick={onBack}>Назад</button></div></div>{error&&<p className="error" role="alert">{error}</p>}<div className="stack">{groups.length===0?<p className="muted">Групп пока нет. Исторические вакансии намеренно не мигрируются автоматически.</p>:groups.map(group=><button className="card link-card" key={group.group_id} onClick={()=>void open(group.group_id)}><strong>{group.title_key||'Название не указано'} · {group.company_key||'компания не указана'}</strong><span>{group.publication_count} публикаций · {date(group.first_seen_at)} — {date(group.last_seen_at)}</span></button>)}</div>{selected&&<section className="card"><div className="section-title"><div><h2>Источники группы</h2><p className="muted">Каноническая публикация не разъединяется. Для связанной публикации действие запрещает её повторное автоматическое объединение с этой группой.</p></div><button className="button secondary" onClick={()=>setSelected(undefined)}>Закрыть</button></div><div className="stack">{selected.publications.map(item=><article className="card" key={item.vacancy_id}><strong>{item.is_canonical?'Каноническая':'Связанная'} · {item.merge_reason}</strong><span className="muted">{item.channel_name||'Канал не указан'} · {date(item.published_at)}</span><div className="actions"><a className="button secondary" href={item.post_link} target="_blank" rel="noreferrer">Открыть источник</a>{!item.is_canonical&&<button className="button danger" onClick={()=>setPending(item.vacancy_id)}>Разъединить</button>}</div></article>)}</div></section>}{pending&&<ConfirmAction title="Разъединить публикацию" description="Запись станет отдельной группой. Повторное автоматическое объединение этой пары будет заблокировано." onConfirm={()=>void confirm()} onCancel={()=>setPending(undefined)}/>}</section>;
}
