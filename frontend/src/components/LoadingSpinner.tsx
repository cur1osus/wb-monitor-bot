"use client";

export default function LoadingSpinner() {
  return (
    <div className="loading-screen">
      <div className="spinner-ring" />
      <span className="loading-label">Загрузка…</span>
    </div>
  );
}
