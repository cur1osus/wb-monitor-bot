"use client";

import { useState, useCallback } from "react";
import {
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  YAxis,
} from "recharts";
import type { Track } from "@/types";
import { formatPrice, formatRating, formatDate } from "@/lib/utils";
import { toggleTrack } from "@/lib/api";

interface TrackCardProps {
  track: Track;
  onRefresh: () => void;
}

interface TooltipPayload {
  value: number | null;
  payload: { date: string };
}

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
}) {
  if (!active || !payload?.length) return null;
  const item = payload[0];
  return (
    <div
      style={{
        background: "var(--secondary-bg)",
        border: "1px solid rgba(0,0,0,0.08)",
        borderRadius: 8,
        padding: "6px 10px",
        fontSize: 12,
        pointerEvents: "none",
      }}
    >
      <div style={{ color: "var(--hint)", marginBottom: 2 }}>
        {formatDate(item.payload.date)}
      </div>
      <div style={{ fontWeight: 700 }}>{formatPrice(item.value)}</div>
    </div>
  );
}

export default function TrackCard({ track, onRefresh }: TrackCardProps) {
  const [loading, setLoading] = useState(false);

  const handleToggle = useCallback(async () => {
    if (loading) return;

    // Haptic feedback
    window.Telegram?.WebApp?.HapticFeedback?.impactOccurred("light");

    setLoading(true);
    try {
      await toggleTrack(track.id);
      onRefresh();
    } catch {
      window.Telegram?.WebApp?.showAlert("Не удалось изменить статус. Попробуйте ещё раз.");
    } finally {
      setLoading(false);
    }
  }, [loading, track.id, onRefresh]);

  const chartData = track.history.filter((p) => p.price !== null);
  const hasChart = chartData.length >= 2;

  // Compute price delta vs first snapshot
  let priceDelta: string | null = null;
  if (chartData.length >= 2 && track.price !== null) {
    const firstPrice = chartData[0].price!;
    const diff = track.price - firstPrice;
    if (Math.abs(diff) >= 1) {
      priceDelta = (diff > 0 ? "+" : "") + Math.round(diff).toLocaleString("ru-RU") + " ₽";
    }
  }

  return (
    <div className={`track-card${track.is_active ? "" : " paused"}`}>
      {/* Header */}
      <div className="track-header">
        <div className="track-title">{track.title}</div>
        {track.in_stock !== null && (
          <span
            className={`track-badge ${track.in_stock ? "in-stock" : "out-of-stock"}`}
          >
            {track.in_stock ? "В наличии" : "Нет"}
          </span>
        )}
      </div>

      {/* Meta */}
      <div className="track-meta">
        <div className="meta-item">
          <span className="meta-label">Цена</span>
          <span className="meta-value">{formatPrice(track.price)}</span>
          {priceDelta && (
            <span
              style={{
                fontSize: 12,
                color: priceDelta.startsWith("+") ? "#e53e3e" : "#30a651",
                fontWeight: 600,
              }}
            >
              {priceDelta}
            </span>
          )}
        </div>
        <div className="meta-item">
          <span className="meta-label">Рейтинг</span>
          <span className="meta-value">{formatRating(track.rating)}</span>
        </div>
      </div>

      {/* Price chart */}
      {hasChart && (
        <div className="chart-area">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
              <YAxis
                domain={["auto", "auto"]}
                hide
              />
              <Tooltip content={<ChartTooltip />} />
              <Line
                type="monotone"
                dataKey="price"
                stroke="var(--btn)"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: "var(--btn)" }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Actions */}
      <div className="track-actions">
        <button
          className={`btn ${track.is_active ? "btn-danger" : "btn-primary"}`}
          onClick={handleToggle}
          disabled={loading}
        >
          {loading ? "…" : track.is_active ? "Пауза" : "Включить"}
        </button>
        <a
          href={track.url}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-secondary"
        >
          Открыть WB
        </a>
      </div>
    </div>
  );
}
