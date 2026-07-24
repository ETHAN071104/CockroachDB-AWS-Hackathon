import { FileText, Presentation, Quote } from "lucide-react";

import type { PublicId } from "../api";

export interface SourceLineage {
  index?: number;
  document_id?: PublicId | null;
  notebook_id?: PublicId | null;
  filename: string;
  mime_type?: string | null;
  page_number?: number | null;
  slide_number?: number | null;
  chunk_index?: number | null;
  distance?: number | null;
  excerpt?: string | null;
}

export interface SourceCardProps {
  source: SourceLineage;
  className?: string;
}

export function SourceCard({ source, className = "" }: SourceCardProps) {
  const isPresentation = source.mime_type?.includes("presentation") ?? false;
  const location = source.slide_number
    ? `Slide ${source.slide_number}`
    : source.page_number
      ? `Page ${source.page_number}`
      : source.chunk_index != null
        ? `Chunk ${source.chunk_index + 1}`
        : null;
  const Icon = isPresentation ? Presentation : FileText;

  return (
    <article
      className={["source-card", className].filter(Boolean).join(" ")}
      aria-label={`Source ${source.index ?? ""}: ${source.filename}`.trim()}
    >
      <header className="source-card__header">
        <span className="source-card__icon" aria-hidden="true">
          <Icon size={18} />
        </span>
        <div className="source-card__identity">
          <p className="source-card__title">
            {source.index ? <span>[{source.index}] </span> : null}
            {source.filename}
          </p>
          <div className="source-card__meta">
            {location ? <span>{location}</span> : null}
            {source.document_id ? <span>Document {source.document_id}</span> : null}
            {source.notebook_id ? <span>Notebook {source.notebook_id}</span> : null}
          </div>
        </div>
      </header>
      {source.excerpt ? (
        <blockquote className="source-card__excerpt">
          <Quote size={16} aria-hidden="true" />
          <span>{source.excerpt}</span>
        </blockquote>
      ) : null}
    </article>
  );
}

