'use client';

import { useEffect, useState, type FormEvent } from 'react';
import LiveMiniapp from './live';
import OrbitMark from './orbit-mark';
import { browserLogin, browserLogout, browserSession } from '../../lib/candidate-api';
import './miniapp.css';

type TelegramBridge = {
  initData?: string;
  ready?: () => void;
  expand?: () => void;
};

type Access = { mode: 'checking' | 'telegram' | 'browser' | 'login'; initData: string; csrfToken: string };

export default function MiniappPage() {
  const [access, setAccess] = useState<Access>({ mode: 'checking', initData: '', csrfToken: '' });
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const bridge = (window as Window & { Telegram?: { WebApp?: TelegramBridge } }).Telegram?.WebApp;
    bridge?.ready?.();
    bridge?.expand?.();
    if (bridge?.initData) {
      setAccess({ mode: 'telegram', initData: bridge.initData, csrfToken: '' });
      return;
    }
    void browserSession()
      .then(session => {
        if (!cancelled) setAccess({
          mode: session.authenticated && session.csrf_token ? 'browser' : 'login',
          initData: '', csrfToken: session.csrf_token ?? '',
        });
      })
      .catch(reason => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'Не удалось проверить вход');
          setAccess({ mode: 'login', initData: '', csrfToken: '' });
        }
      });
    return () => { cancelled = true; };
  }, []);

  const submitCode = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!/^\d{10}$/.test(code)) {
      setError('Введите 10 цифр из сообщения бота.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const csrfToken = await browserLogin(code);
      setCode('');
      setAccess({ mode: 'browser', initData: '', csrfToken });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось войти');
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    setBusy(true);
    try {
      await browserLogout(access.csrfToken);
      setAccess({ mode: 'login', initData: '', csrfToken: '' });
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось выйти');
    } finally {
      setBusy(false);
    }
  };

  if (access.mode === 'checking') {
    return <main className="miniapp ma-live-loading ma-orbit-splash"><div className="ma-splash-mark"><OrbitMark size={92} animated/></div><span>RADAR</span><p>Загружаем вакансии</p></main>;
  }
  if (access.mode === 'telegram') {
    return <LiveMiniapp initData={access.initData}/>;
  }
  if (access.mode === 'browser') {
    return <LiveMiniapp initData="" csrfToken={access.csrfToken} onLogout={() => void logout()}/>;
  }
  return <main className="miniapp ma-browser-login">
    <div className="ma-login-shell">
      <div className="ma-login-brand"><OrbitMark size={48}/><strong>radar<span>.</span></strong></div>
      <section className="ma-login-card" aria-labelledby="ma-login-title">
        <span className="ma-login-overline">ВХОД</span>
        <h1 id="ma-login-title">Подходящие вакансии.<br/>В одном месте.</h1>
        <p>Войдите через Telegram. Профили, вакансии и сохранённое синхронизируются с ботом.</p>
        <ol className="ma-login-steps">
          <li><span>1</span><div>Открой чат с ботом и отправь <strong>/login</strong></div></li>
          <li><span>2</span><div>Скопируй одноразовый код из его ответа</div></li>
          <li><span>3</span><div>Введи код ниже — он действует 10 минут</div></li>
        </ol>
        <form onSubmit={event => void submitCode(event)}>
          <label htmlFor="ma-browser-code">Код из Telegram</label>
          <input id="ma-browser-code" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{10}" maxLength={10} placeholder="10 цифр" value={code} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 10))}/>
          {error && <p className="ma-login-error" role="alert">{error}</p>}
          <button type="submit" disabled={busy || code.length !== 10}>{busy ? 'Проверяем код…' : 'Войти на сайт →'}</button>
        </form>
        <small>Код выдаётся только в личном чате с ботом и работает один раз.</small>
      </section>
    </div>
  </main>;
}
