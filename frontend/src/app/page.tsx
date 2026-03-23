"use client";

import { useEffect, useState, useCallback } from "react";
import type { DashboardData } from "@/types";
import { fetchDashboard } from "@/lib/api";
import LoadingSpinner from "@/components/LoadingSpinner";
import StatsBar from "@/components/StatsBar";
import TrackCard from "@/components/TrackCard";
import EmptyState from "@/components/EmptyState";

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const result = await fetchDashboard();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки данных");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Re-fetch when user returns to the mini app
  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (!tg) return;

    const handler = () => load();
    tg.onEvent("viewportChanged", handler);

    const visHandler = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", visHandler);

    return () => {
      document.removeEventListener("visibilitychange", visHandler);
    };
  }, [load]);

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <div className="error-screen">
        <div className="error-icon">❌</div>
        <div className="error-title">Ошибка</div>
        <div className="error-message">{error}</div>
        <button
          className="btn btn-primary"
          style={{ marginTop: 16, minWidth: 160 }}
          onClick={() => {
            setLoading(true);
            setError(null);
            load();
          }}
        >
          Повторить
        </button>
      </div>
    );
  }

  if (!data) return null;

  const { user, tracks } = data;
  const activeTracks = tracks.filter((t) => t.is_active);
  const inStockTracks = tracks.filter((t) => t.in_stock);

  const planLabel = user.plan === "pro" ? "PRO" : "Free";
  const userName = user.first_name || user.username || "Пользователь";

  return (
    <div className="page-wrapper">
      {/* Header */}
      <div className="header">
        <div className="header-title">👋 Привет, {userName}!</div>
        <div className="header-subtitle">Тариф: {planLabel}</div>
      </div>

      {/* Stats */}
      <StatsBar
        total={tracks.length}
        active={activeTracks.length}
        inStock={inStockTracks.length}
      />

      {/* Tracks list */}
      {tracks.length === 0 ? (
        <EmptyState
          icon="🔍"
          title="Нет отслеживаемых товаров"
          text="Добавьте товары через бот — и они появятся здесь"
        />
      ) : (
        <>
          <div className="section-label">Товары ({tracks.length})</div>
          {tracks.map((track) => (
            <TrackCard key={track.id} track={track} onRefresh={load} />
          ))}
        </>
      )}
    </div>
  );
}
