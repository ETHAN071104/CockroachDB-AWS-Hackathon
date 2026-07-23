import type { ReactNode } from 'react';
import { AlertCircle, RotateCcw } from 'lucide-react';

import { toApiError } from '../api';
import { Button } from './Button';
import { Card } from './Card';
import { EmptyState } from './States';
import { Notice } from './Notice';

export interface StructuredErrorProps {
  error: unknown;
  onRetry?: () => void | Promise<unknown>;
  retryLabel?: string;
  alternateAction?: ReactNode;
  className?: string;
}

function ErrorContent({
  error,
  onRetry,
  retryLabel = 'Try again',
  alternateAction,
}: StructuredErrorProps) {
  const apiError = toApiError(error);
  const shouldShowReason =
    apiError.reason.trim().toLocaleLowerCase() !==
    apiError.title.trim().toLocaleLowerCase();

  return (
    <>
      {shouldShowReason ? (
        <p className="structured-error__reason">{apiError.reason}</p>
      ) : null}
      <p className="structured-error__action">{apiError.nextAction}</p>
      {apiError.requestId ? (
        <details className="structured-error__support">
          <summary>Support details</summary>
          <p>
            Request ID: <code>{apiError.requestId}</code>
          </p>
        </details>
      ) : null}
      {(apiError.retryable && onRetry) || alternateAction ? (
        <div className="structured-error__actions">
          {apiError.retryable && onRetry ? (
            <Button
              variant="secondary"
              icon={<RotateCcw size={18} aria-hidden="true" />}
              onClick={() => {
                void Promise.resolve(onRetry()).catch(() => undefined);
              }}
            >
              {retryLabel}
            </Button>
          ) : null}
          {alternateAction}
        </div>
      ) : null}
    </>
  );
}

export function ErrorNotice(props: StructuredErrorProps) {
  const apiError = toApiError(props.error);
  return (
    <Notice tone="error" title={apiError.title} className={props.className}>
      <ErrorContent {...props} />
    </Notice>
  );
}

export function RetryableErrorCard(props: StructuredErrorProps) {
  const apiError = toApiError(props.error);
  return (
    <Card
      className={['structured-error-card', props.className]
        .filter(Boolean)
        .join(' ')}
    >
      <div className="structured-error-card__heading">
        <AlertCircle aria-hidden="true" />
        <h2>{apiError.title}</h2>
      </div>
      <ErrorContent {...props} />
    </Card>
  );
}

export interface InlineFieldErrorProps {
  error: unknown;
  className?: string;
}

export function InlineFieldError({
  error,
  className = '',
}: InlineFieldErrorProps) {
  const apiError = toApiError(error);
  return (
    <p
      className={['inline-field-error', className].filter(Boolean).join(' ')}
      role="alert"
    >
      <strong>{apiError.title}.</strong> {apiError.reason}
    </p>
  );
}

export interface EmptyStateActionProps {
  title: string;
  reason: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function EmptyStateAction({
  title,
  reason,
  action,
  className,
}: EmptyStateActionProps) {
  return (
    <EmptyState
      title={title}
      description={reason}
      action={action}
      className={className}
    />
  );
}
