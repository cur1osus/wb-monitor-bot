export interface HistoryPoint {
  date: string;
  price: number | null;
}

export interface Track {
  id: number;
  wb_item_id: number;
  title: string;
  url: string;
  image_url: string;
  price: number | null;
  rating: number | null;
  reviews: number | null;
  in_stock: boolean | null;
  is_active: boolean;
  watch_stock: boolean;
  watch_price_fluctuation: boolean;
  watch_qty: boolean;
  watch_sizes: string[];
  last_sizes: string[];
  history: HistoryPoint[];
}

export interface DashboardUser {
  id: number;
  username: string | null;
  first_name: string | null;
  plan: string;
}

export interface DashboardData {
  user: DashboardUser;
  tracks: Track[];
}

export interface TrackSettings {
  watch_stock?: boolean;
  watch_price_fluctuation?: boolean;
  watch_qty?: boolean;
  watch_sizes?: string[];
}
