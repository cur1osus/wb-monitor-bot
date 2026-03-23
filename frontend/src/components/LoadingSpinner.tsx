export function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-blue-500 border-b-transparent" />
    </div>
  );
}

export function LoadingDashboard() {
  return (
    <div className="space-y-6">
      {/* Header skeleton */}
      <div className="h-8 w-48 animate-pulse rounded bg-gray-200 dark:bg-gray-700" />
      
      {/* Stats skeleton */}
      <div className="grid grid-cols-2 gap-4">
        {[1, 2].map((i) => (
          <div
            key={i}
            className="h-20 animate-pulse rounded-xl bg-gray-200 dark:bg-gray-700"
          />
        ))}
      </div>
      
      {/* Tracks skeleton */}
      <div className="space-y-4">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="h-64 animate-pulse rounded-xl bg-gray-200 dark:bg-gray-700"
          />
        ))}
      </div>
    </div>
  );
}
