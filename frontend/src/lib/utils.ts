import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatPrice(price: number | null): string {
  if (price === null || price === undefined) return '—';
  return `${price.toLocaleString('ru-RU')} ₽`;
}

export function formatRating(rating: number | null): string {
  if (rating === null || rating === undefined) return '—';
  return rating.toFixed(1);
}

export function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'short',
  });
}

export function formatDateTime(dateString: string): string {
  return new Date(dateString).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}
