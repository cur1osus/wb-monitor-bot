export interface HistoryPoint {
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
  in_stock: boolean | null;
  is_active: boolean;
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
