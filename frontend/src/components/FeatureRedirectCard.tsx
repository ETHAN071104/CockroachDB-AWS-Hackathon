import { ArrowRight, Route } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { FeatureRedirect } from '../api';
import { Badge } from './Badge';

export interface FeatureRedirectCardProps {
  redirect: FeatureRedirect;
}

export function featureRedirectPath(redirect: FeatureRedirect): string {
  const parameters = new URLSearchParams({
    view: redirect.target === 'coaching' ? 'coaching' : 'plan',
    prompt: redirect.original_prompt,
  });
  return `/study-actions?${parameters.toString()}`;
}

export function FeatureRedirectCard({ redirect }: FeatureRedirectCardProps) {
  return (
    <div className="feature-redirect" role="status">
      <div className="feature-redirect__heading">
        <Route size={20} aria-hidden="true" />
        <div>
          <Badge tone="info">Better study tool</Badge>
          <h3>{redirect.title}</h3>
        </div>
      </div>
      <p>{redirect.message}</p>
      {redirect.suggested_prompt ? (
        <p className="feature-redirect__suggestion">
          Suggested question: “{redirect.suggested_prompt}”
        </p>
      ) : null}
      <Link className="button button--primary" to={featureRedirectPath(redirect)}>
        <span>{redirect.action_label}</span>
        <ArrowRight size={18} aria-hidden="true" />
      </Link>
    </div>
  );
}
