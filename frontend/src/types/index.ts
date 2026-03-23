export interface User {
  id: number;
  username: string | null;
  first_name: string | null;
  plan: string;
}

export interface PriceHistoryPoint {
  date: string;
  price: number | null;
}

export interface Track {
  id: number;
  wb_item_id: number;
  title: string;
  url: string;
  price: number | null;
  rating: number | null;
  in_stock: boolean;
  is_active: boolean;
  history: PriceHistoryPoint[];
}

export interface DashboardData {
  user: User;
  tracks: Track[];
}

export interface ApiResponse<T> {
  data?: T;
  error?: string;
}
