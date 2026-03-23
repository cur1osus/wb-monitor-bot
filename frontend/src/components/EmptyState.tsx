"use client";

interface EmptyStateProps {
  icon?: string;
  title: string;
  text?: string;
}

export default function EmptyState({
  icon = "🔍",
  title,
  text,
}: EmptyStateProps) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <div className="empty-title">{title}</div>
      {text && <div className="empty-text">{text}</div>}
    </div>
  );
}
