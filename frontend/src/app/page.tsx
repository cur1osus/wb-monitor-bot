'use client';

import { useEffect, useState } from 'react';
import DashboardPage from '@/pages/dashboard';

interface TelegramWebApp {
  ready: () => void;
  expand: () => void;
  initData?: string;
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
  const [isTelegram, setIsTelegram] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

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
      
      // Check if opened from Telegram (has initData)
      if (tg.initData) {
        setIsTelegram(true);
      }
    }
    
    setIsLoading(false);
  }, []);

  if (isLoading) {
    return (
      <main className="flex-1 p-4 md:p-6 max-w-5xl mx-auto w-full">
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
        </div>
      </main>
    );
  }

  if (!isTelegram) {
    return (
      <main className="flex-1 p-4 md:p-6 max-w-5xl mx-auto w-full">
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <div className="text-6xl mb-4">🤖</div>
          <h1 className="text-2xl font-bold mb-4 text-gray-900 dark:text-white">
            WB Monitor Dashboard
          </h1>
          <p className="text-gray-600 dark:text-gray-400 mb-6 max-w-md">
            Этот дашборд предназначен для использования в Telegram Web App.
          </p>
          <p className="text-gray-600 dark:text-gray-400 mb-8 max-w-md">
            Пожалуйста, откройте через бота Telegram:
            <br />
            <a
              href="https://t.me/monitoring24by7bot"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline"
            >
              @monitoring24by7bot
            </a>
          </p>
          <a
            href="https://t.me/monitoring24by7bot"
            target="_blank"
            rel="noopener noreferrer"
            className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            Открыть в Telegram
          </a>
        </div>
      </main>
    );
  }

  return (
    <main className="flex-1 p-4 md:p-6 max-w-5xl mx-auto w-full">
      <DashboardPage />
    </main>
  );
}
