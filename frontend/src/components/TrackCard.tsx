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
import { formatPrice, formatRating } from "@/lib/utils";
import { toggleTrack, deleteTrack, patchTrackSettings } from "@/lib/api";

interface TrackCardProps {
  track: Track;
  isPro: boolean;
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
    <div className="chart-tooltip">
      <div className="chart-tooltip-date">
        {new Date(item.payload.date).toLocaleDateString("ru-RU", {
          day: "numeric",
          month: "short",
        })}
      </div>
      <div className="chart-tooltip-price">{formatPrice(item.value)}</div>
    </div>
  );
}

type View = "main" | "settings" | "confirm_delete";

export default function TrackCard({ track, isPro, onRefresh }: TrackCardProps) {
  const [view, setView] = useState<View>("main");
  const [busy, setBusy] = useState(false);
  const [imgError, setImgError] = useState(false);

  const haptic = (style: "light" | "medium" = "light") => {
    window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(style);
  };

  const withBusy = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
    } catch {
      window.Telegram?.WebApp?.showAlert("Что-то пошло не так. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  };

  const handleToggle = useCallback(() => {
    haptic();
    withBusy(async () => {
      await toggleTrack(track.id);
      onRefresh();
    });
  }, [track.id, onRefresh, busy]);

  const handleDelete = useCallback(() => {
    haptic("medium");
    withBusy(async () => {
      await deleteTrack(track.id);
      onRefresh();
    });
  }, [track.id, onRefresh, busy]);

  const handleToggleSetting = useCallback(
    (key: "watch_stock" | "watch_price_fluctuation" | "watch_qty", current: boolean) => {
      haptic();
      withBusy(async () => {
        await patchTrackSettings(track.id, { [key]: !current });
        onRefresh();
      });
    },
    [track.id, onRefresh, busy]
  );

  const handleSizeToggle = useCallback(
    (size: string) => {
      haptic();
      const current = new Set(track.watch_sizes);
      if (current.has(size)) current.delete(size);
      else current.add(size);
      withBusy(async () => {
        await patchTrackSettings(track.id, { watch_sizes: [...current] });
        onRefresh();
      });
    },
    [track.id, track.watch_sizes, onRefresh, busy]
  );

  const chartData = track.history.filter((p) => p.price !== null);
  const hasChart = chartData.length >= 2;

  // Price delta
  let priceDelta: string | null = null;
  let deltaPositive = false;
  if (chartData.length >= 2 && track.price !== null) {
    const firstPrice = chartData[0].price!;
    const diff = track.price - firstPrice;
    if (Math.abs(diff) >= 1) {
      deltaPositive = diff > 0;
      priceDelta = (diff > 0 ? "▲ " : "▼ ") + Math.abs(Math.round(diff)).toLocaleString("ru-RU") + " ₽";
    }
  }

  return (
    <div className={`track-card${track.is_active ? "" : " paused"}`}>
      {/* === MAIN VIEW === */}
      {view === "main" && (
        <>
          {/* Product image + header */}
          <div className="track-header">
            {!imgError ? (
              <img
                src={track.image_url}
                alt={track.title}
                className="track-img"
                onError={() => setImgError(true)}
              />
            ) : (
              <div className="track-img-fallback">📦</div>
            )}
            <div className="track-header-info">
              <div className="track-title">{track.title}</div>
              <div className="track-badges">
                {track.in_stock !== null && (
                  <span className={`track-badge ${track.in_stock ? "in-stock" : "out-of-stock"}`}>
                    {track.in_stock ? "✓ В наличии" : "✗ Нет"}
                  </span>
                )}
                {!track.is_active && (
                  <span className="track-badge paused-badge">⏸ Пауза</span>
                )}
              </div>
            </div>
          </div>

          {/* Price / rating / reviews */}
          <div className="track-meta">
            <div className="meta-item">
              <span className="meta-label">Цена</span>
              <span className="meta-value">{formatPrice(track.price)}</span>
              {priceDelta && (
                <span className={`meta-delta ${deltaPositive ? "delta-up" : "delta-down"}`}>
                  {priceDelta}
                </span>
              )}
            </div>
            {track.rating !== null && (
              <div className="meta-item">
                <span className="meta-label">Рейтинг</span>
                <span className="meta-value">{formatRating(track.rating)}</span>
              </div>
            )}
            {track.reviews !== null && (
              <div className="meta-item">
                <span className="meta-label">Отзывы</span>
                <span className="meta-value">{track.reviews.toLocaleString("ru-RU")}</span>
              </div>
            )}
          </div>

          {/* Chart */}
          {hasChart && (
            <div className="chart-area">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
                  <YAxis domain={["auto", "auto"]} hide />
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
              className={`btn ${track.is_active ? "btn-secondary" : "btn-primary"}`}
              onClick={handleToggle}
              disabled={busy}
            >
              {busy ? "…" : track.is_active ? "⏸ Пауза" : "▶ Включить"}
            </button>
            <button
              className="btn btn-icon"
              title="Настройки"
              onClick={() => { haptic(); setView("settings"); }}
              disabled={busy}
            >
              ⚙️
            </button>
            <a
              href={track.url}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-icon"
              title="Открыть на WB"
            >
              🔗
            </a>
            <button
              className="btn btn-icon btn-delete-icon"
              title="Удалить"
              onClick={() => { haptic("medium"); setView("confirm_delete"); }}
              disabled={busy}
            >
              🗑
            </button>
          </div>
        </>
      )}

      {/* === SETTINGS VIEW === */}
      {view === "settings" && (
        <>
          <div className="settings-header">
            <span className="settings-title">⚙️ Настройки</span>
            <button className="btn-close" onClick={() => setView("main")}>✕</button>
          </div>
          <div className="settings-title-text">{track.title}</div>

          <div className="settings-list">
            {/* watch_stock */}
            <label className="settings-row" onClick={() => handleToggleSetting("watch_stock", track.watch_stock)}>
              <div className="settings-row-info">
                <span className="settings-row-label">📦 Появление в наличии</span>
                <span className="settings-row-hint">Уведомлять когда товар появится</span>
              </div>
              <div className={`toggle ${track.watch_stock ? "on" : ""}`} />
            </label>

            {/* watch_price_fluctuation */}
            <label className="settings-row" onClick={() => handleToggleSetting("watch_price_fluctuation", track.watch_price_fluctuation)}>
              <div className="settings-row-info">
                <span className="settings-row-label">💰 Изменение цены</span>
                <span className="settings-row-hint">Уведомлять при росте или падении</span>
              </div>
              <div className={`toggle ${track.watch_price_fluctuation ? "on" : ""}`} />
            </label>

            {/* watch_qty — PRO only */}
            <label className={`settings-row${!isPro ? " locked" : ""}`}
              onClick={() => isPro && handleToggleSetting("watch_qty", track.watch_qty)}>
              <div className="settings-row-info">
                <span className="settings-row-label">📊 Количество остатков {!isPro && <span className="pro-badge">PRO</span>}</span>
                <span className="settings-row-hint">Уведомлять об изменении остатков</span>
              </div>
              <div className={`toggle ${track.watch_qty ? "on" : ""}${!isPro ? " disabled" : ""}`} />
            </label>

            {/* watch_sizes */}
            {track.last_sizes.length > 0 && (
              <div className="settings-sizes">
                <div className="settings-row-label">📏 Отслеживаемые размеры</div>
                <div className="settings-row-hint" style={{ marginBottom: 8 }}>
                  {track.watch_sizes.length === 0 ? "Все размеры" : `Выбрано: ${track.watch_sizes.join(", ")}`}
                </div>
                <div className="sizes-grid">
                  {track.last_sizes.map((size) => {
                    const selected = track.watch_sizes.includes(size);
                    return (
                      <button
                        key={size}
                        className={`size-btn ${selected ? "selected" : ""}`}
                        onClick={() => handleSizeToggle(size)}
                        disabled={busy}
                      >
                        {size}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          <button className="btn btn-secondary" style={{ width: "100%", marginTop: 4 }} onClick={() => setView("main")}>
            ← Назад
          </button>
        </>
      )}

      {/* === CONFIRM DELETE VIEW === */}
      {view === "confirm_delete" && (
        <div className="confirm-delete">
          <div className="confirm-icon">🗑️</div>
          <div className="confirm-title">Удалить товар?</div>
          <div className="confirm-subtitle">{track.title}</div>
          <div className="confirm-actions">
            <button
              className="btn btn-danger"
              onClick={handleDelete}
              disabled={busy}
            >
              {busy ? "…" : "Да, удалить"}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => setView("main")}
              disabled={busy}
            >
              Отмена
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
