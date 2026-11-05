'use client';

import { useEffect, useState } from 'react';
import LiveMiniapp from './live';
import './miniapp.css';

type TelegramBridge = {
  initData?: string;
  ready?: () => void;
  expand?: () => void;
};

export default function MiniappPage() {
  const [initData, setInitData] = useState<string | null>(null);

  useEffect(() => {
    const bridge = (window as Window & { Telegram?: { WebApp?: TelegramBridge } }).Telegram?.WebApp;
    bridge?.ready?.();
    bridge?.expand?.();
    setInitData(bridge?.initData || '');
  }, []);

  if (initData === null) {
    return <main className="miniapp ma-live-loading">Загружаем Mini App…</main>;
  }
  if (!initData) {
    return <main className="miniapp ma-live-loading">
      <span className="ma-brand-mark" aria-hidden="true"><span/><span/><span/><span/></span>
      <h1>Откройте Mini App через бота в Telegram</h1>
      <p>Нажмите «Открыть приложение» слева от поля ввода в чате бота. Так Telegram передаст данные для входа.</p>
    </main>;
  }
  return <LiveMiniapp initData={initData}/>;
}
