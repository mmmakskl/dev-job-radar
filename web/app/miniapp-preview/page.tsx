'use client';

import { useState } from 'react';
import './preview.css';

export default function MiniappPreview() {
  const [width, setWidth] = useState<'compact' | 'wide'>('compact');

  return <main className="mp-preview">
    <div className="mp-topbar">
      <div className="mp-brand"><span className="mp-brand-mark">✦</span><span><strong>radar.</strong><small>Предпросмотр Mini App</small></span></div>
      <div className="mp-controls" aria-label="Размер окна Mini App">
        <button type="button" className={width === 'compact' ? 'active' : ''} onClick={() => setWidth('compact')}>Узкое окно</button>
        <button type="button" className={width === 'wide' ? 'active' : ''} onClick={() => setWidth('wide')}>Широкое окно</button>
      </div>
      <a className="mp-full-link" href="/miniapp/" target="_blank" rel="noreferrer">Открыть отдельно <span aria-hidden="true">↗</span></a>
    </div>
    <div className="mp-workspace">
      <aside className="mp-chats" aria-label="Имитация списка чатов Telegram">
        <div className="mp-chats-head"><span>☰</span><strong>Чаты</strong><span>⌕</span></div>
        <div className="mp-chat selected"><span className="mp-chat-avatar">✦</span><span><strong>radar. вакансии</strong><small>Твоя карьера, твои правила</small></span><em>сейчас</em></div>
        <div className="mp-chat"><span className="mp-chat-avatar faded">К</span><span><strong>Карьера в IT</strong><small>Новая вакансия в канале</small></span></div>
        <div className="mp-chat"><span className="mp-chat-avatar faded">G</span><span><strong>Go Community</strong><small>Обсуждение вакансий</small></span></div>
      </aside>
      <section className="mp-chat-room" aria-label="Предпросмотр Mini App в окне Telegram">
        <div className="mp-chat-header"><span className="mp-chat-avatar small">✦</span><span><strong>radar. вакансии</strong><small>бот</small></span><span className="mp-header-dots">•••</span></div>
        <div className="mp-chat-canvas"><div className="mp-chat-bubble">Привет! Я помогу найти вакансии под твой профиль.<br/>Открой приложение, чтобы управлять поиском.</div><div className="mp-chat-pill">✦ Открыть приложение</div></div>
      </section>
      <div className={`mp-app-window ${width}`}>
        <div className="mp-app-top"><span className="mp-app-grip"/><div><strong>radar.</strong><small>Mini App · интерактивное демо</small></div><a href="/miniapp/" target="_blank" rel="noreferrer" aria-label="Открыть Mini App отдельно">↗</a></div>
        <iframe title="Интерактивный предпросмотр radar Mini App" src="/miniapp/"/>
      </div>
    </div>
    <p className="mp-caption">Предпросмотр показывает размер и поведение Mini App на ПК. Окружение Telegram стилизовано для демонстрации.</p>
  </main>;
}
