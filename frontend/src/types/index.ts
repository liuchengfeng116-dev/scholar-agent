export interface User {
  id: number;
  username: string;
  role: string;
  interests: { keywords: string[]; categories: string[] };
}

export interface DuplicateInfo {
  id: number;
  existing_paper_id: number;
  existing_title: string;
  existing_version: string;
  existing_filename: string;
  text_similarity: number;
  semantic_similarity: number;
  suggestion: string;
  suggestion_reason: string;
  diff_summary: string;
  decision: string;
}

export interface Paper {
  id: number;
  title: string;
  filename: string;
  authors: Array<{ name: string; affiliations?: string[]; email?: string } | string>;
  affiliations: string[];
  abstract: string;
  keywords: string[];
  arxiv_id: string;
  version: string;
  year: number | null;
  pub_date: string;
  venue: string;
  doi: string;
  github_url: string;
  tags: string[];
  note: string;
  page_count: number;
  has_text_layer: boolean;
  doc_type: string;
  source: string;
  status: string;
  progress: number;
  status_msg: string;
  stats: Record<string, number | boolean>;
  created_at?: string;
  duplicate?: DuplicateInfo;
}

export interface SectionItem {
  id: number;
  parent_id: number | null;
  level: number;
  number: string;
  title: string;
  page: number;
  kind: string;
}

export interface BlockItem {
  id: number;
  section_id: number | null;
  type: string;
  page: number;
  bbox: number[];
  content: string;
  latex: string;
  html: string;
  image_url: string;
}

export interface ReferenceItem {
  id: number;
  idx: number;
  raw_text: string;
  title: string;
  authors: string;
  year: number | null;
  venue: string;
  doi: string;
  arxiv_id: string;
  page: number;
  bbox: number[];
  cite_contexts: Array<{ page: number; bbox: number[]; snippet: string }>;
}

export interface CitationAnchor {
  ref: string;
  paper_id: number;
  paper_title: string;
  page: number;
  bbox: number[];
  section_path: string;
  snippet: string;
}

export interface TraceEvent {
  type: string;
  ts?: number;
  agent?: string;
  tool?: string;
  args?: Record<string, unknown>;
  ok?: boolean;
  elapsed?: number;
  preview?: string;
  intent?: string;
  reasoning?: string;
  steps?: Array<{ step: number; agent: string; goal: string; suggested_tool?: string }>;
  grounded_ratio?: number;
  warnings?: string[];
  short_turns?: number;
  long_items?: number;
  [key: string]: unknown;
}

export interface ChatMessage {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  citations?: CitationAnchor[];
  trace?: TraceEvent[];
  critic?: { grounded_ratio: number; claim_checks: Array<Record<string, string>>; warnings: string[] };
}
