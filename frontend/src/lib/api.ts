import type { DashboardData } from '@/types';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || '';

interface TelegramWebApp {
  initData?: string;
}

interface TelegramWindow extends Window {
  Telegram?: {
    WebApp?: TelegramWebApp;
  };
}

function getTelegramInitData(): string | null {
  if (typeof window === 'undefined') return null;
  
  const tg = (window as TelegramWindow).Telegram?.WebApp;
  return tg?.initData || null;
}

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const initData = getTelegramInitData();
  
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(initData && { 'X-Telegram-Init-Data': initData }),
    ...options?.headers,
  };

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function getDashboard(): Promise<DashboardData> {
  return fetchApi<DashboardData>('/api/tracks');
}

export async function toggleTrack(trackId: number): Promise<{ status: string }> {
  return fetchApi<{ status: string }>('/api/action', {
    method: 'POST',
    body: JSON.stringify({
      event: 'toggle',
      track_id: trackId,
    }),
  });
}
