"use client";

interface StatsBarProps {
  total: number;
  active: number;
  inStock: number;
}

export default function StatsBar({ total, active, inStock }: StatsBarProps) {
  return (
    <div className="stats-bar">
      <div className="stat-card">
        <span className="stat-icon">📦</span>
        <span className="stat-value">{total}</span>
        <span className="stat-label">Всего</span>
      </div>
      <div className="stat-card">
        <span className="stat-icon">🔔</span>
        <span className="stat-value">{active}</span>
        <span className="stat-label">Активных</span>
      </div>
      <div className="stat-card">
        <span className="stat-icon">✅</span>
        <span className="stat-value">{inStock}</span>
        <span className="stat-label">В наличии</span>
      </div>
    </div>
  );
}
