'use client';

import { useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { Track } from '@/types';
import { cn, formatPrice, formatRating, formatDate } from '@/lib/utils';
import { toggleTrack } from '@/lib/api';

interface TrackCardProps {
  track: Track;
  onToggle?: (trackId: number, newStatus: boolean) => void;
}

export function TrackCard({ track, onToggle }: TrackCardProps) {
  const [isToggling, setIsToggling] = useState(false);

  const handleToggle = async () => {
    setIsToggling(true);
    try {
      await toggleTrack(track.id);
      onToggle?.(track.id, !track.is_active);
    } catch (error) {
      console.error('Failed to toggle track:', error);
    } finally {
      setIsToggling(false);
    }
  };

  const chartData = track.history.map((point) => ({
    date: point.date,
    price: point.price,
  }));

  return (
    <div
      className={cn(
        'rounded-xl border p-4 transition-all duration-200',
        'bg-white dark:bg-gray-800',
        'border-gray-200 dark:border-gray-700',
        'shadow-sm hover:shadow-md'
      )}
    >
      {/* Header */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-gray-900 dark:text-white truncate">
            {track.title}
          </h3>
          <a
            href={track.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
          >
            Открыть на WB
          </a>
        </div>
        <span
          className={cn(
            'px-2 py-1 rounded-full text-xs font-medium',
            track.in_stock
              ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
              : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
          )}
        >
          {track.in_stock ? 'В наличии' : 'Нет в наличии'}
        </span>
      </div>

      {/* Stats */}
      <div className="mb-4 flex items-center gap-4">
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400">Цена</div>
          <div className="text-lg font-bold text-gray-900 dark:text-white">
            {formatPrice(track.price)}
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400">Рейтинг</div>
          <div className="text-lg font-bold text-gray-900 dark:text-white">
            {formatRating(track.rating)}
          </div>
        </div>
        <div className="ml-auto">
          <button
            onClick={handleToggle}
            disabled={isToggling}
            className={cn(
              'px-4 py-2 rounded-lg text-sm font-medium transition-colors',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              track.is_active
                ? 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600'
                : 'bg-blue-600 text-white hover:bg-blue-700'
            )}
          >
            {isToggling ? (
              '...'
            ) : track.is_active ? (
              'Пауза'
            ) : (
              'Включить'
            )}
          </button>
        </div>
      </div>

      {/* Chart */}
      {chartData.length > 0 && (
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="#e5e7eb"
                vertical={false}
              />
              <XAxis
                dataKey="date"
                tickFormatter={formatDate}
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                dataKey="price"
                tickFormatter={(value) => `${value}`}
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                domain={['auto', 'auto']}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'rgba(255, 255, 255, 0.95)',
                  border: '1px solid #e5e7eb',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
                labelFormatter={(label) => formatDate(label as string)}
                formatter={(value) => [formatPrice(value as number), 'Цена']}
              />
              <Line
                type="monotone"
                dataKey="price"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
