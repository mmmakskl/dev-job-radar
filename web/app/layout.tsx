import type { Metadata } from 'next';
import './styles.css';

export const metadata: Metadata = {
  title: 'Go Radar — управление',
  description: 'Безопасная панель управления Telegram-парсером вакансий',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru" suppressHydrationWarning><body>{children}</body></html>;
}
