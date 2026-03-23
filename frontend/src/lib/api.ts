import type { DashboardData } from "@/types";

function getInitData(): string {
  if (typeof window === "undefined") return "";
  return window.Telegram?.WebApp?.initData || "";
}

export async function fetchDashboard(): Promise<DashboardData> {
  const res = await fetch("/api/tracks", {
    headers: {
      "X-Telegram-Init-Data": getInitData(),
      "Content-Type": "application/json",
    },
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

export async function toggleTrack(trackId: number): Promise<void> {
  const res = await fetch("/api/action", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": getInitData(),
    },
    body: JSON.stringify({ event: "toggle", track_id: trackId }),
  });

  if (!res.ok) {
    throw new Error("Failed to toggle track");
  }
}
