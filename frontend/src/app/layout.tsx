import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "WB Monitor",
  description: "Мониторинг товаров Wildberries",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ru">
      <head>
        {/* Telegram Web App SDK must be loaded synchronously */}
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script src="https://telegram.org/js/telegram-web-app.js" />
        <meta name="color-scheme" content="light dark" />
        <meta
          name="viewport"
          content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"
        />
      </head>
      <body>
        <TelegramInit />
        {children}
      </body>
    </html>
  );
}

// Small client component to initialize Telegram SDK
function TelegramInit() {
  return (
    <script
      dangerouslySetInnerHTML={{
        __html: `
          if (window.Telegram?.WebApp) {
            window.Telegram.WebApp.ready();
            window.Telegram.WebApp.expand();
          }
        `,
      }}
    />
  );
}
