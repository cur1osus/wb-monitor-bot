'use client';

import { useEffect, useState, useCallback } from 'react';
import { TrackCard, StatsCard, LoadingDashboard, EmptyState } from '@/components';
import type { DashboardData } from '@/types';
import { getDashboard } from '@/lib/api';

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const dashboardData = await getDashboard();
      setData(dashboardData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleToggleTrack = useCallback((trackId: number, newStatus: boolean) => {
    setData((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        tracks: prev.tracks.map((track) =>
          track.id === trackId ? { ...track, is_active: newStatus } : track
        ),
      };
    });
  }, []);

  if (loading) {
    return <LoadingDashboard />;
  }

  if (error) {
    return (
      <div className="py-12">
        <EmptyState
          icon="❌"
          title="Ошибка загрузки"
          message={error}
        />
        <button
          onClick={loadData}
          className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          Повторить
        </button>
      </div>
    );
  }

  if (!data || data.tracks.length === 0) {
    return (
      <EmptyState
        icon="📦"
        title="Нет товаров"
        message="Добавьте товары в боте, чтобы они появились здесь"
      />
    );
  }

  const totalTracks = data.tracks.length;
  const activeTracks = data.tracks.filter((t) => t.is_active).length;
  const inStockTracks = data.tracks.filter((t) => t.in_stock).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
          👋 Привет, {data.user.first_name || data.user.username || 'Пользователь'}!
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Тариф: <span className="font-medium uppercase">{data.user.plan}</span>
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <StatsCard icon="📦" label="Всего товаров" value={totalTracks} />
        <StatsCard icon="🔔" label="Активных" value={activeTracks} />
        <StatsCard icon="✅" label="В наличии" value={inStockTracks} />
      </div>

      {/* Tracks Grid */}
      <div className="grid gap-4">
        {data.tracks.map((track) => (
          <TrackCard
            key={track.id}
            track={track}
            onToggle={handleToggleTrack}
          />
        ))}
      </div>
    </div>
  );
}
