import { BackendConfig, HealthStatus, LeadFormData } from '../types';

export class ApiError extends Error {
  code: string;
  status?: number;
  retryable: boolean;
  retryAfter?: number;

  constructor(code: string, message: string, status?: number, retryable = false, retryAfter?: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.retryAfter = retryAfter;
  }
}

// In Vite development, requests to /api, /config, /health can be proxied, or point directly to the backend
const BASE_URL = import.meta.env.VITE_BACKEND_URL || '';

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let code = 'http_error';
    let message = 'Something went wrong. Please try again.';
    try {
      const data = await res.json();
      if (data.error) {
        code = data.error.code || code;
        message = data.error.message || message;
      }
    } catch {
      // fallback
    }

    const retryAfterHeader = res.headers.get('Retry-After');
    const retryAfter = retryAfterHeader && !isNaN(Number(retryAfterHeader)) ? parseInt(retryAfterHeader, 10) : undefined;
    const retryable = res.status === 429 || res.status >= 500;

    throw new ApiError(code, message, res.status, retryable, retryAfter);
  }
  return res.json() as Promise<T>;
}

export const api = {
  async getConfig(): Promise<BackendConfig> {
    try {
      const res = await fetch(`${BASE_URL}/config`);
      return await handleResponse<BackendConfig>(res);
    } catch (err) {
      if (err instanceof ApiError) throw err;
      throw new ApiError('backend_unreachable', "I can't reach the assistant service right now.", undefined, true);
    }
  },

  async getHealth(): Promise<HealthStatus> {
    const res = await fetch(`${BASE_URL}/health`);
    return await handleResponse<HealthStatus>(res);
  },

  async chat(payload: {
    session_id: string;
    conversation_id?: string | null;
    message: string;
    interaction_mode?: string;
    client_message_id?: string;
    interrupted_previous?: boolean;
    transcript_confidence?: number;
  }) {
    const res = await fetch(`${BASE_URL}/api/v1/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return await handleResponse<{
      conversation_id: string;
      answer: string;
      sources: any[];
      suggested_actions: any[];
      response_status: string;
      interaction_mode: string;
      lead_status?: string;
      conversation_restarted?: boolean;
    }>(res);
  },

  async processAudio(
    audioBlob: Blob,
    params: {
      sessionId: string;
      conversationId?: string | null;
      mode: string;
      clientMessageId: string;
      interrupted: boolean;
      filename?: string;
      withAudio?: boolean;
      confirmLowConfidence?: boolean;
    }
  ) {
    const formData = new FormData();
    formData.append('file', audioBlob, params.filename || 'recording.wav');
    formData.append('session_id', params.sessionId);
    formData.append('interaction_mode', params.mode);
    formData.append('client_message_id', params.clientMessageId);
    formData.append('with_audio', String(params.withAudio ?? true));
    formData.append('confirm_low_confidence', String(params.confirmLowConfidence ?? true));
    formData.append('interrupted_previous', String(params.interrupted));
    if (params.conversationId) {
      formData.append('conversation_id', params.conversationId);
    }

    const res = await fetch(`${BASE_URL}/api/v1/audio/process`, {
      method: 'POST',
      body: formData,
    });

    return await handleResponse<{
      transcript: string;
      transcript_confidence?: number;
      needs_confirmation?: boolean;
      chat: any;
      audio_base64?: string;
      audio_mime?: string;
      tts_error?: string;
    }>(res);
  },

  async synthesize(sessionId: string, text: string): Promise<{ blob: Blob; mime: string }> {
    const res = await fetch(`${BASE_URL}/api/v1/audio/synthesize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        text: text.slice(0, 4000),
      }),
    });

    if (!res.ok) {
      throw new ApiError('tts_error', 'Failed to synthesize speech', res.status);
    }

    const blob = await res.blob();
    const mime = res.headers.get('content-type') || 'audio/wav';
    return { blob, mime };
  },

  async createLead(leadData: LeadFormData & { session_id: string; conversation_id?: string | null }) {
    const res = await fetch(`${BASE_URL}/api/v1/leads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(leadData),
    });
    return await handleResponse<{ status: string; message: string; lead_id?: string }>(res);
  },

  async deleteConversation(sessionId: string, conversationId: string) {
    try {
      await fetch(`${BASE_URL}/api/v1/conversations/${conversationId}`, {
        method: 'DELETE',
        headers: { 'X-Session-ID': sessionId },
      });
    } catch {
      // Ignored for graceful UI resets
    }
  },
};
