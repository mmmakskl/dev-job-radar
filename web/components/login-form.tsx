'use client';

import React, { useState } from 'react';

export function LoginForm({ onLogin, configured }: { onLogin: (password: string) => Promise<void>; configured: boolean }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await onLogin(password); } catch (reason) { setError(reason instanceof Error ? reason.message : 'Ошибка входа'); }
    finally { setBusy(false); }
  }
  return <main className="shell" style={{maxWidth:480, paddingTop:80}}><section className="card"><p className="brand">Go Radar</p><h1>Вход в управление</h1>{!configured && <p className="error" role="alert">Администраторский пароль ещё не настроен на сервере.</p>}<form onSubmit={submit}><label className="field">Пароль администратора<input aria-label="Пароль администратора" type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>{error && <p className="error" role="alert">{error}</p>}<button className="button" disabled={busy || !configured}>{busy ? 'Входим…' : 'Войти'}</button></form></section></main>;
}
