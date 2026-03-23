import { cn } from '@/lib/utils';

interface StatsCardProps {
  icon: string;
  label: string;
  value: string | number;
  className?: string;
}

export function StatsCard({ icon, label, value, className }: StatsCardProps) {
  return (
    <div
      className={cn(
        'rounded-xl border p-4',
        'bg-white dark:bg-gray-800',
        'border-gray-200 dark:border-gray-700',
        'shadow-sm',
        className
      )}
    >
      <div className="flex items-center gap-3">
        <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-blue-100 dark:bg-blue-900/30 text-2xl">
          {icon}
        </div>
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
          <div className="text-xl font-bold text-gray-900 dark:text-white">
            {value}
          </div>
        </div>
      </div>
    </div>
  );
}
