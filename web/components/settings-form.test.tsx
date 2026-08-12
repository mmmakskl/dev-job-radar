import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SettingsForm } from './settings-form';

const settings: any = { telegram:{folder_name:'Вакансии',history_days:7,monitoring_enabled:true,additional_channels:[],channels:[]}, filters:{keywords:['go'],exclude_keywords:[],text_hash_ttl_days:30,queue_maxsize:1000,workers:1}, mistral:{model:'mistral-small-latest',temperature:.1,max_attempts:2}, sheets:{output_timezone:'Europe/Moscow',full_title:'Полные',short_title:'Краткие'} };
describe('SettingsForm', () => { it('shows validation error for empty keywords', async () => { render(<SettingsForm settings={settings} onSave={vi.fn()} />); fireEvent.change(screen.getByLabelText('Ключевые слова через запятую'), {target:{value:''}}); fireEvent.click(screen.getByRole('button',{name:'Сохранить настройки'})); expect(await screen.findByText('Укажите хотя бы одно ключевое слово')).toBeInTheDocument(); }); });
