import Script from 'next/script';

export default function MiniappLayout({ children }: { children: React.ReactNode }) {
  return <><Script src="https://telegram.org/js/telegram-web-app.js" strategy="beforeInteractive"/>{children}</>;
}
