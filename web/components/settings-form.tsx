'use client';

import React from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import type { Settings } from '../lib/api';

const schema = z.object({
  folder_name: z.string().trim().min(1, 'Введите название папки').max(80),
  keywords: z.string().trim().min(1, 'Укажите хотя бы одно ключевое слово'),
  exclude_keywords: z.string().max(500),
  history_days: z.coerce.number().int().min(1).max(90),
  temperature: z.coerce.number().min(0).max(1),
  timezone: z.string().trim().min(1),
  monitoring_enabled: z.boolean(),
});
type FormValues = z.infer<typeof schema>;
const words = (value:string) => value.split(',').map((item) => item.trim()).filter(Boolean);

export function SettingsForm({ settings, onSave }: { settings:Settings; onSave:(value:Partial<Settings>)=>Promise<void> }) {
  const { register, handleSubmit, formState:{errors,isSubmitting} } = useForm<FormValues>({ resolver:zodResolver(schema), defaultValues:{ folder_name:settings.telegram.folder_name, keywords:settings.filters.keywords.join(', '), exclude_keywords:settings.filters.exclude_keywords.join(', '), history_days:settings.telegram.history_days, temperature:settings.mistral.temperature, timezone:settings.sheets.output_timezone, monitoring_enabled:settings.telegram.monitoring_enabled } });
  return <form onSubmit={handleSubmit(async (value) => onSave({ telegram:{folder_name:value.folder_name, history_days:value.history_days, monitoring_enabled:value.monitoring_enabled} as any, filters:{...settings.filters, keywords:words(value.keywords), exclude_keywords:words(value.exclude_keywords)}, mistral:{...settings.mistral, temperature:value.temperature}, sheets:{...settings.sheets, output_timezone:value.timezone} }))}>
    <div className="grid"><label className="field span-6">Папка Telegram<input {...register('folder_name')} />{errors.folder_name && <span className="error">{errors.folder_name.message}</span>}</label><label className="field span-6">История, дней<input type="number" {...register('history_days')} />{errors.history_days && <span className="error">Введите от 1 до 90</span>}</label><label className="field span-6">Ключевые слова через запятую<input {...register('keywords')} />{errors.keywords && <span className="error">{errors.keywords.message}</span>}</label><label className="field span-6">Исключить слова<input {...register('exclude_keywords')} /></label><label className="field span-4">Mistral temperature<input type="number" step="0.1" {...register('temperature')} />{errors.temperature && <span className="error">Введите число от 0 до 1</span>}</label><label className="field span-4">Часовой пояс<input {...register('timezone')} /></label><label className="field span-12"><span><input type="checkbox" {...register('monitoring_enabled')} /> Включить live-мониторинг</span></label></div><button className="button" disabled={isSubmitting}>{isSubmitting ? 'Сохраняем…' : 'Сохранить настройки'}</button></form>;
}
