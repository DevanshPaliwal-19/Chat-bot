export type InteractionMode = 'text' | 'voice' | 'avatar';

export interface SourceCitation {
  index: number;
  source_document: string;
  page_number?: number | null;
  title?: string | null;
  source_url?: string | null;
}

export interface SuggestedAction {
  type: 'open_lead_form' | 'request_human_handoff' | string;
  label: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  mode?: InteractionMode;
  status?: 'sent' | 'pending' | 'failed' | 'low_confidence' | 'no_knowledge' | 'notice';
  sources?: SourceCitation[];
  actions?: SuggestedAction[];
  voice?: boolean;
  local?: boolean;
  timestamp?: string;
}

export interface BackendConfig {
  company_name: string;
  assistant_name: string;
  company_website: string;
  features: {
    voice_input: boolean;
    voice_output: boolean;
  };
  limits: {
    max_audio_seconds: number;
  };
  human_handoff: {
    live_notification: boolean;
    contact_email?: string | null;
  };
}

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'error';
  knowledge_base?: {
    ready: boolean;
    chunks_count?: number;
  };
  llm?: {
    status: string;
    model?: string;
  };
}

export interface LeadFormData {
  name: string;
  email: string;
  company?: string;
  phone?: string;
  project_type?: string;
  requirements?: string;
  timeline?: string;
  budget?: string;
  request_human_follow_up: boolean;
  consent: boolean;
}

export interface PendingConfirmation {
  transcript: string;
  mode: InteractionMode;
  confidence?: number;
  interrupted?: boolean;
}
