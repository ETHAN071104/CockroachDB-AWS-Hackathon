import type { ReactNode } from "react";
import { AlertTriangle, BookOpen, LoaderCircle, RotateCcw } from "lucide-react";

import { Button } from "./Button";

export interface LoadingStateProps {
  message?: string;
  compact?: boolean;
  className?: string;
}

export function LoadingState({
  message = "Loading…",
  compact = false,
  className = "",
}: LoadingStateProps) {
  return (
    <div
      className={[
        "state",
        "state--loading",
        compact ? "state--compact" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      role="status"
      aria-live="polite"
    >
      <LoaderCircle className="state__spinner" aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}

export interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  compact?: boolean;
  className?: string;
}

export function EmptyState({
  title,
  description,
  action,
  icon,
  compact = false,
  className = "",
}: EmptyStateProps) {
  return (
    <div
      className={[
        "state",
        "state--empty",
        compact ? "state--compact" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span className="state__icon" aria-hidden="true">
        {icon ?? <BookOpen />}
      </span>
      <h2 className="state__title">{title}</h2>
      {description ? <div className="state__description">{description}</div> : null}
      {action ? <div className="state__action">{action}</div> : null}
    </div>
  );
}

export interface ErrorStateProps {
  title?: string;
  message: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
  compact?: boolean;
  className?: string;
}

export function ErrorState({
  title = "Agentbook could not load this content",
  message,
  onRetry,
  retryLabel = "Try again",
  compact = false,
  className = "",
}: ErrorStateProps) {
  return (
    <div
      className={[
        "state",
        "state--error",
        compact ? "state--compact" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      role="alert"
    >
      <span className="state__icon" aria-hidden="true">
        <AlertTriangle />
      </span>
      <h2 className="state__title">{title}</h2>
      <div className="state__description">{message}</div>
      {onRetry ? (
        <div className="state__action">
          <Button
            variant="secondary"
            onClick={onRetry}
            icon={<RotateCcw size={18} aria-hidden="true" />}
          >
            {retryLabel}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

