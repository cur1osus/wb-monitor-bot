"use client";

import { useState, useCallback, useRef } from "react";
import {
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  YAxis,
} from "recharts";
import type { Track, ReviewInsights, SimilarResult } from "@/types";
import { formatPrice, formatRating } from "@/lib/utils";
import { toggleTrack, deleteTrack, patchTrackSettings, fetchReviews, fetchSimilar } from "@/lib/api";

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

type View = "main" | "settings" | "confirm_delete" | "reviews" | "similar";

export default function TrackCard({ track, isPro, onRefresh }: TrackCardProps) {
  const [view, setView] = useState<View>("main");
  const [busy, setBusy] = useState(false);
  const [imgError, setImgError] = useState(false);

  // Reviews state
  const [reviewData, setReviewData] = useState<ReviewInsights | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  // Similar state
  const [similarData, setSimilarData] = useState<SimilarResult | null>(null);
  const [similarLoading, setSimilarLoading] = useState(false);
  const [similarError, setSimilarError] = useState<string | null>(null);
  const [similarMode, setSimilarMode] = useState<"cheap" | "similar">("cheap");
  const similarModeRef = useRef<"cheap" | "similar">("cheap");

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

  const handleOpenReviews = useCallback(async () => {
    haptic();
    setView("reviews");
    if (reviewData) return; // already loaded
    setReviewLoading(true);
    setReviewError(null);
    try {
      const data = await fetchReviews(track.id);
      setReviewData(data);
    } catch (e) {
      setReviewError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setReviewLoading(false);
    }
  }, [track.id, reviewData]);

  const handleOpenSimilar = useCallback(async (mode: "cheap" | "similar") => {
    haptic();
    setSimilarMode(mode);
    similarModeRef.current = mode;
    setView("similar");
    setSimilarData(null);
    setSimilarLoading(true);
    setSimilarError(null);
    try {
      const data = await fetchSimilar(track.id, mode);
      // Only set if mode hasn't changed while loading
      if (similarModeRef.current === mode) {
        setSimilarData(data);
      }
    } catch (e) {
      if (similarModeRef.current === mode) {
        setSimilarError(e instanceof Error ? e.message : "Ошибка поиска");
      }
    } finally {
      if (similarModeRef.current === mode) {
        setSimilarLoading(false);
      }
    }
  }, [track.id]);

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

  const goMain = () => { haptic(); setView("main"); };

  return (
    <div className={`track-card${track.is_active ? "" : " paused"}`}>
      {/* === MAIN VIEW === */}
      {view === "main" && (
        <div className="view-anim">
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

          {/* Main actions */}
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

          {/* Feature buttons */}
          <div className="track-features">
            <button
              className="btn btn-feature"
              onClick={handleOpenReviews}
              disabled={busy}
            >
              📝 Анализ отзывов
            </button>
            <button
              className="btn btn-feature"
              onClick={() => handleOpenSimilar("cheap")}
              disabled={busy}
            >
              💸 Найти дешевле
            </button>
          </div>
        </div>
      )}

      {/* === SETTINGS VIEW === */}
      {view === "settings" && (
        <div className="view-anim">
          <div className="settings-header">
            <span className="settings-title">⚙️ Настройки</span>
            <button className="btn-close" onClick={goMain}>✕</button>
          </div>
          <div className="settings-title-text">{track.title}</div>

          <div className="settings-list">
            <label className="settings-row" onClick={() => handleToggleSetting("watch_stock", track.watch_stock)}>
              <div className="settings-row-info">
                <span className="settings-row-label">📦 Появление в наличии</span>
                <span className="settings-row-hint">Уведомлять когда товар появится</span>
              </div>
              <div className={`toggle ${track.watch_stock ? "on" : ""}`} />
            </label>

            <label className="settings-row" onClick={() => handleToggleSetting("watch_price_fluctuation", track.watch_price_fluctuation)}>
              <div className="settings-row-info">
                <span className="settings-row-label">💰 Изменение цены</span>
                <span className="settings-row-hint">Уведомлять при росте или падении</span>
              </div>
              <div className={`toggle ${track.watch_price_fluctuation ? "on" : ""}`} />
            </label>

            <label className={`settings-row${!isPro ? " locked" : ""}`}
              onClick={() => isPro && handleToggleSetting("watch_qty", track.watch_qty)}>
              <div className="settings-row-info">
                <span className="settings-row-label">📊 Количество остатков {!isPro && <span className="pro-badge">PRO</span>}</span>
                <span className="settings-row-hint">Уведомлять об изменении остатков</span>
              </div>
              <div className={`toggle ${track.watch_qty ? "on" : ""}${!isPro ? " disabled" : ""}`} />
            </label>

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

          <button className="btn btn-secondary" style={{ width: "100%", marginTop: 4 }} onClick={goMain}>
            ← Назад
          </button>
        </div>
      )}

      {/* === CONFIRM DELETE VIEW === */}
      {view === "confirm_delete" && (
        <div className="confirm-delete view-anim">
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
              onClick={goMain}
              disabled={busy}
            >
              Отмена
            </button>
          </div>
        </div>
      )}

      {/* === REVIEWS VIEW === */}
      {view === "reviews" && (
        <div className="view-anim">
          <div className="settings-header">
            <span className="settings-title">📝 Анализ отзывов</span>
            <button className="btn-close" onClick={goMain}>✕</button>
          </div>
          <div className="settings-title-text">{track.title}</div>

          {reviewLoading && (
            <div className="feature-loading">
              <div className="spinner-ring" />
              <span>Анализируем отзывы…</span>
              <span className="feature-loading-hint">Это может занять ~15 сек</span>
            </div>
          )}

          {reviewError && (
            <div className="feature-error">⚠️ {reviewError}</div>
          )}

          {reviewData && !reviewLoading && (
            <>
              {(reviewData.positive_total > 0 || reviewData.negative_total > 0) && (
                <div className="reviews-stats">
                  <div className="review-stat-chip positive">
                    <span className="review-stat-icon">👍</span>
                    <span className="review-stat-num">{reviewData.positive_total.toLocaleString("ru-RU")}</span>
                    <span className="review-stat-label">положит.</span>
                  </div>
                  <div className="review-stat-chip negative">
                    <span className="review-stat-icon">👎</span>
                    <span className="review-stat-num">{reviewData.negative_total.toLocaleString("ru-RU")}</span>
                    <span className="review-stat-label">отрицат.</span>
                  </div>
                </div>
              )}

              {reviewData.strengths.length > 0 && (
                <div className="insights-section">
                  <div className="insights-label success-label">✅ Достоинства</div>
                  <div className="insights-chips">
                    {reviewData.strengths.map((s, i) => (
                      <span key={i} className="insight-chip positive">{s}</span>
                    ))}
                  </div>
                </div>
              )}

              {reviewData.weaknesses.length > 0 && (
                <div className="insights-section">
                  <div className="insights-label error-label">❌ Недостатки</div>
                  <div className="insights-chips">
                    {reviewData.weaknesses.map((w, i) => (
                      <span key={i} className="insight-chip negative">{w}</span>
                    ))}
                  </div>
                </div>
              )}

              {reviewData.strengths.length === 0 && reviewData.weaknesses.length === 0 && (
                <div className="feature-empty">Отзывов недостаточно для анализа</div>
              )}
            </>
          )}

          <button className="btn btn-secondary" style={{ width: "100%", marginTop: 12 }} onClick={goMain}>
            ← Назад
          </button>
        </div>
      )}

      {/* === SIMILAR VIEW === */}
      {view === "similar" && (
        <div className="view-anim">
          <div className="settings-header">
            <span className="settings-title">
              {similarMode === "cheap" ? "💸 Найти дешевле" : "🔄 Похожие"}
            </span>
            <button className="btn-close" onClick={goMain}>✕</button>
          </div>
          <div className="settings-title-text">{track.title}</div>

          {/* Mode tabs */}
          <div className="similar-mode-tabs">
            <button
              className={`mode-tab ${similarMode === "cheap" ? "active" : ""}`}
              onClick={() => { if (similarMode !== "cheap") handleOpenSimilar("cheap"); }}
              disabled={similarLoading}
            >
              💸 Дешевле
            </button>
            <button
              className={`mode-tab ${similarMode === "similar" ? "active" : ""}`}
              onClick={() => { if (similarMode !== "similar") handleOpenSimilar("similar"); }}
              disabled={similarLoading}
            >
              🔄 Похожие
            </button>
          </div>

          {similarLoading && (
            <div className="feature-loading">
              <div className="spinner-ring" />
              <span>{similarMode === "cheap" ? "Ищем дешевле…" : "Ищем похожие…"}</span>
              <span className="feature-loading-hint">Проверяем актуальные цены и наличие</span>
            </div>
          )}

          {similarError && (
            <div className="feature-error">⚠️ {similarError}</div>
          )}

          {similarData && !similarLoading && similarData.items.length === 0 && (
            <div className="feature-empty">
              {similarMode === "cheap" ? "Дешевле не нашлось" : "Похожих товаров не нашлось"}
            </div>
          )}

          {similarData && !similarLoading && similarData.items.length > 0 && (
            <>
              {similarMode === "cheap" && similarData.base_price && (
                <div className="similar-base-price">
                  Текущая цена: <strong>{formatPrice(parseFloat(similarData.base_price))}</strong>
                </div>
              )}
              <div className="similar-list">
                {similarData.items.map((item, i) => (
                  <a
                    key={item.wb_item_id}
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="similar-item"
                  >
                    <div className="similar-num">{i + 1}</div>
                    <div className="similar-info">
                      <div className="similar-title">
                        {item.brand ? <strong>{item.brand} </strong> : null}{item.title}
                      </div>
                      <div className="similar-price">{formatPrice(parseFloat(item.price))}</div>
                    </div>
                    <div className="similar-arrow">›</div>
                  </a>
                ))}
              </div>
            </>
          )}

          <button className="btn btn-secondary" style={{ width: "100%", marginTop: 12 }} onClick={goMain}>
            ← Назад
          </button>
        </div>
      )}
    </div>
  );
}
