import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AttentionPanel } from './attention-panel';
import { MetricCards } from './metric-cards';
import { LogsPanel } from './logs-panel';
import { PromptEditor } from './prompt-editor';
import { SourcesPanel } from './sources-panel';

const { api } = vi.hoisted(() => ({ api: {
  prompt: vi.fn().mockResolvedValue({instructions:'Определи Go-вакансии и исключи резюме кандидатов.',default_instructions:'Значение по умолчанию достаточно длинное.',is_custom:true,restart_required:true,variables:[]}),
  savePrompt: vi.fn().mockResolvedValue({instructions:'Новое безопасное значение инструкций для распознавания Go-вакансий.',is_custom:true,restart_required:true}),
  resetPrompt: vi.fn().mockResolvedValue({instructions:'Значение по умолчанию достаточно длинное.',is_custom:false,restart_required:true}),
  sources: vi.fn().mockResolvedValue({items:[],total:0,restart_required:true}),
  logs: vi.fn().mockResolvedValue({items:[{at:'2026-08-12T10:00:00Z',level:'ERROR',component:'llm',message:'Безопасная запись'}],total:1,offset:0,limit:30}),
  addSource: vi.fn().mockResolvedValue({item:{label:'@go_jobs'},restart_required:true}), verifySource: vi.fn(), changeSource: vi.fn(), deleteSource: vi.fn(),
} }));
vi.mock('../lib/api', async () => ({ ...(await vi.importActual('../lib/api')), api }));

describe('admin feature components', () => {
  it('renders metrics and a safe attention error', () => {
    render(<><MetricCards loading={false} error="" onRefresh={vi.fn()} metrics={{date:'2026-08-12',timezone:'Europe/Moscow',description:'Служебные события.',counts:{posts_processed:4,vacancies_added:1,skipped:2,errors:1},reasons:{candidate_resume:2}}} /><AttentionPanel loading={false} onResolve={vi.fn()} errors={[{id:'one',component:'llm',summary:'LLM временно недоступен',details:'Безопасные детали',status:'new',count:1,first_seen_at:'2026-08-12T00:00:00Z',last_seen_at:'2026-08-12T00:00:00Z'}]} /></>);
    expect(screen.getByText('Постов обработано сегодня')).toBeInTheDocument();
    expect(screen.getByText('Требуют внимания')).toBeInTheDocument();
    expect(screen.getByText(/резюме кандидата/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Безопасные детали'}));
    expect(screen.getByText('Безопасные детали', {selector: 'pre'})).toBeInTheDocument();
  });

  it('validates and displays source empty state', async () => {
    render(<SourcesPanel onBack={vi.fn()} onChanged={vi.fn().mockResolvedValue(undefined)} />);
    expect(await screen.findByText(/Источники по этому запросу не найдены/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Новый источник'), {target:{value:'@go_jobs'}});
    fireEvent.click(screen.getByRole('button',{name:'Проверить и добавить'}));
    expect(api.addSource).toHaveBeenCalledWith('@go_jobs');
    fireEvent.change(screen.getByLabelText('Новый источник'), {target:{value:'https://t.me/+private'}});
    fireEvent.click(screen.getByRole('button',{name:'Проверить и добавить'}));
    expect(screen.getByText(/Введите публичный @username или ссылку t\.me\/username/)).toBeInTheDocument();
  });

  it('filters the logs view without exposing raw log files', async () => {
    render(<LogsPanel onBack={vi.fn()} />);
    expect(await screen.findByText('Безопасная запись')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Уровень лога'), {target:{value:'ERROR'}});
    expect(api.logs).toHaveBeenCalled();
  });

  it('saves and confirms reset of LLM instructions', async () => {
    render(<PromptEditor onBack={vi.fn()} />);
    const editor = await screen.findByLabelText('Инструкции для LLM');
    fireEvent.change(editor,{target:{value:'Новое безопасное значение инструкций для распознавания Go-вакансий.'}});
    fireEvent.click(screen.getByRole('button',{name:'Сохранить инструкции'}));
    expect(api.savePrompt).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'Восстановить значение по умолчанию'}));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
