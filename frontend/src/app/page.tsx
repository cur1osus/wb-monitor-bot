'use client';

import { useEffect } from 'react';
import DashboardPage from '@/pages/dashboard';

interface TelegramWebApp {
  ready: () => void;
  expand: () => void;
  themeParams?: {
    bg_color?: string;
    text_color?: string;
  };
}

interface TelegramWindow extends Window {
  Telegram?: {
    WebApp?: TelegramWebApp;
  };
}

export default function Home() {
  useEffect(() => {
    // Initialize Telegram Web App
    const tg = (window as TelegramWindow).Telegram?.WebApp;
    if (tg) {
      tg.ready();
      tg.expand();
      
      // Apply Telegram theme
      document.documentElement.style.setProperty(
        '--tg-theme-bg-color',
        tg.themeParams?.bg_color || '#ffffff'
      );
      document.documentElement.style.setProperty(
        '--tg-theme-text-color',
        tg.themeParams?.text_color || '#000000'
      );
    }
  }, []);

  return (
    <main className="flex-1 p-4 md:p-6 max-w-5xl mx-auto w-full">
      <DashboardPage />
    </main>
  );
}
