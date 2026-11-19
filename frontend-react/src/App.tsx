import React, { useState, useEffect, useRef } from 'react';
import { Send, AlertCircle, RefreshCw } from 'lucide-react';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { MessageItem } from './components/MessageItem';
import { SuggestedPrompts } from './components/SuggestedPrompts';
import { VoicePanel } from './components/VoicePanel';
import { LeadModal } from './components/LeadModal';
import { ConfirmTranscriptModal } from './components/ConfirmTranscriptModal';
import { useAudioRecorder } from './hooks/useAudioRecorder';
import { api, ApiError } from './services/api';
import {
  BackendConfig,
  HealthStatus,
  InteractionMode,
  LeadFormData,
  Message,
  PendingConfirmation,
} from './types';

function generateId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, '');
  }
  return Math.random().toString(36).substring(2) + Date.now().toString(36);
}

const DEFAULT_PROMPTS = [
  'What services do you offer?',
  "I'm planning a new project",
  'Tell me about your company',
];

export const App: React.FC = () => {
  // Session IDs
  const [sessionId] = useState<string>(() => {
    const cached = sessionStorage.getItem('osiya_session_id');
    if (cached) return cached;
    const fresh = generateId();
    sessionStorage.setItem('osiya_session_id', fresh);
    return fresh;
  });
  const [conversationId, setConversationId] = useState<string | null>(null);

  // App & Backend State
  const [config, setConfig] = useState<BackendConfig | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [backendOk, setBackendOk] = useState(true);
  const [mode, setMode] = useState<InteractionMode>('text');

  // UI State
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [leadModalOpen, setLeadModalOpen] = useState(false);
  const [leadDefaultFollowUp, setLeadDefaultFollowUp] = useState(false);
  const [confirmation, setConfirmation] = useState<PendingConfirmation | null>(null);

  // Chat State
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputText, setInputText] = useState('');
  const [isPending, setIsPending] = useState(false);
  const [lastError, setLastError] = useState<ApiError | null>(null);
  const [failedMessage, setFailedMessage] = useState<string | null>(null);

  // Voice & Audio Playback
  const [isPlayingAudio, setIsPlayingAudio] = useState(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);

  const {
    isRecording,
    recordingTime,
    startRecording,
    stopRecording,
    cancelRecording,
    error: recorderError,
  } = useAudioRecorder();

  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isPending]);

  // Load initial backend configuration
  useEffect(() => {
    const init = async () => {
      try {
        const [cfg, h] = await Promise.allSettled([api.getConfig(), api.getHealth()]);
        if (cfg.status === 'fulfilled') {
          setConfig(cfg.value);
          setBackendOk(true);

          // Add initial welcome message
          setMessages([
            {
              id: 'welcome',
              role: 'assistant',
              content: `Hi there! Welcome to ${cfg.value.company_name}.\n\nI'm ${cfg.value.assistant_name}, your AI assistant. Are you exploring our services, planning a new project, or looking for help with an existing solution?`,
              actions: [
                { type: 'open_lead_form', label: '🤝 Talk to our team' },
              ],
            },
          ]);
        } else {
          setBackendOk(false);
        }

        if (h.status === 'fulfilled') {
          setHealth(h.value);
        }
      } catch {
        setBackendOk(false);
      }
    };
    init();
  }, []);

  // Stop currently playing TTS audio
  const stopAudio = () => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current.currentTime = 0;
      currentAudioRef.current = null;
    }
    setIsPlayingAudio(false);
  };

  // Play audio base64 or blob
  const playAudio = (audioBase64?: string, mime?: string, audioBlob?: Blob) => {
    stopAudio();
    try {
      let audioUrl = '';
      if (audioBlob) {
        audioUrl = URL.createObjectURL(audioBlob);
      } else if (audioBase64) {
        const m = mime || 'audio/wav';
        audioUrl = `data:${m};base64,${audioBase64}`;
      } else {
        return;
      }

      const audio = new Audio(audioUrl);
      currentAudioRef.current = audio;
      setIsPlayingAudio(true);

      audio.onended = () => {
        setIsPlayingAudio(false);
        currentAudioRef.current = null;
      };

      audio.onerror = () => {
        setIsPlayingAudio(false);
        currentAudioRef.current = null;
      };

      audio.play().catch(() => {
        setIsPlayingAudio(false);
      });
    } catch {
      setIsPlayingAudio(false);
    }
  };

  // Send Text Message
  const handleSendText = async (textToSend?: string) => {
    const text = (textToSend ?? inputText).trim();
    if (!text || isPending) return;

    // Interrupt any active voice reply
    stopAudio();
    setLastError(null);
    setFailedMessage(null);

    const userMessageId = generateId();
    const newUserMsg: Message = {
      id: userMessageId,
      role: 'user',
      content: text,
      status: 'pending',
      mode,
    };

    setMessages((prev) => [...prev, newUserMsg]);
    if (!textToSend) setInputText('');
    setIsPending(true);

    try {
      const resp = await api.chat({
        session_id: sessionId,
        conversation_id: conversationId,
        message: text,
        interaction_mode: mode,
        client_message_id: generateId(),
        interrupted_previous: isPlayingAudio,
      });

      setConversationId(resp.conversation_id);

      // Mark user message as sent
      setMessages((prev) =>
        prev.map((m) => (m.id === userMessageId ? { ...m, status: 'sent' } : m))
      );

      // Add assistant response
      const assistantMsg: Message = {
        id: generateId(),
        role: 'assistant',
        content: resp.answer,
        sources: resp.sources,
        actions: resp.suggested_actions,
        status: resp.response_status as any,
        mode: resp.interaction_mode as InteractionMode,
      };
      setMessages((prev) => [...prev, assistantMsg]);

      // If in Voice or Avatar mode, synthesize speech reply
      if (mode === 'voice' || mode === 'avatar') {
        try {
          const { blob } = await api.synthesize(sessionId, resp.answer);
          playAudio(undefined, undefined, blob);
        } catch {
          // TTS failure is silent; the text message is already displayed
        }
      }
    } catch (err: any) {
      setLastError(err);
      setFailedMessage(text);
      setMessages((prev) =>
        prev.map((m) => (m.id === userMessageId ? { ...m, status: 'failed' } : m))
      );
    } finally {
      setIsPending(false);
    }
  };

  // Start Voice Recording
  const handleStartRecord = async () => {
    stopAudio();
    await startRecording();
  };

  // Stop Recording and Process Audio
  const handleStopRecord = async () => {
    const blob = await stopRecording();
    if (!blob || blob.size === 0) return;

    setIsPending(true);
    setLastError(null);

    try {
      const resp = await api.processAudio(blob, {
        sessionId,
        conversationId,
        mode,
        clientMessageId: generateId(),
        interrupted: isPlayingAudio,
        withAudio: true,
        confirmLowConfidence: true,
      });

      if (resp.needs_confirmation) {
        setConfirmation({
          transcript: resp.transcript,
          mode,
          confidence: resp.transcript_confidence,
          interrupted: false,
        });
        setIsPending(false);
        return;
      }

      // Add transcribed user voice message
      setMessages((prev) => [
        ...prev,
        {
          id: generateId(),
          role: 'user',
          content: resp.transcript,
          voice: true,
          status: 'sent',
          mode,
        },
      ]);

      if (resp.chat) {
        setConversationId(resp.chat.conversation_id);
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: 'assistant',
            content: resp.chat.answer,
            sources: resp.chat.sources,
            actions: resp.chat.suggested_actions,
            status: resp.chat.response_status,
            mode: resp.chat.interaction_mode,
          },
        ]);
      }

      // Auto play returned voice
      if (resp.audio_base64) {
        playAudio(resp.audio_base64, resp.audio_mime);
      }
    } catch (err: any) {
      setLastError(err);
    } finally {
      setIsPending(false);
    }
  };

  // Handle Low-Confidence Transcript Confirmation
  const handleConfirmTranscript = (confirmedText: string) => {
    setConfirmation(null);
    handleSendText(confirmedText);
  };

  // Reset Conversation
  const handleResetConversation = async (newSession = false) => {
    stopAudio();
    if (conversationId) {
      await api.deleteConversation(sessionId, conversationId);
    }
    setConversationId(null);
    setMessages([
      {
        id: generateId(),
        role: 'assistant',
        content: `Conversation refreshed! How can I help you today?`,
      },
    ]);
    if (newSession) {
      const fresh = generateId();
      sessionStorage.setItem('osiya_session_id', fresh);
      window.location.reload();
    }
  };

  // Lead Form submission
  const handleLeadSubmit = async (data: LeadFormData) => {
    try {
      const res = await api.createLead({
        ...data,
        session_id: sessionId,
        conversation_id: conversationId,
      });
      return { success: true, message: res.message || 'Details received! Someone from our team will follow up.' };
    } catch (err: any) {
      return { success: false, message: err.message || 'Failed to submit lead enquiry.' };
    }
  };

  const hasMessages = messages.length > 1; // more than just welcome

  return (
    <div className="flex h-screen w-full bg-slate-50 overflow-hidden text-slate-800">
      {/* Sidebar */}
      <Sidebar
        config={config}
        health={health}
        backendOk={backendOk}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNewChat={() => handleResetConversation(true)}
        onClearChat={() => handleResetConversation(false)}
        onOpenLead={(followUp) => {
          setLeadDefaultFollowUp(!!followUp);
          setLeadModalOpen(true);
        }}
        isBusy={isPending}
      />

      {/* Main Container */}
      <div className="flex-1 flex flex-col h-full min-w-0 bg-slate-100/50">
        <Header
          config={config}
          health={health}
          backendOk={backendOk}
          mode={mode}
          onModeChange={(m) => {
            stopAudio();
            setMode(m);
          }}
          onToggleSidebar={() => setSidebarOpen(true)}
        />

        {/* Scrollable Chat Area */}
        <div className="flex-1 overflow-y-auto px-4 md:px-8 py-6">
          <div className="max-w-3xl mx-auto flex flex-col justify-between min-h-full">
            <div>
              {/* Service Disconnected Notice */}
              {!backendOk && (
                <div className="mb-4 p-3.5 bg-amber-50 border border-amber-200 text-amber-800 rounded-2xl flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 text-amber-600 shrink-0" />
                    <span>I can't reach the assistant service right now. Please verify your backend server is running.</span>
                  </div>
                  <button
                    onClick={() => window.location.reload()}
                    className="flex items-center gap-1 font-semibold text-amber-900 hover:underline shrink-0"
                  >
                    <RefreshCw className="w-3 h-3" /> Retry
                  </button>
                </div>
              )}

              {/* Error Notice with Retry */}
              {lastError && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 text-xs rounded-2xl flex items-center justify-between">
                  <span>{lastError.message}</span>
                  {failedMessage && (
                    <button
                      onClick={() => handleSendText(failedMessage)}
                      className="px-2.5 py-1 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium"
                    >
                      Retry
                    </button>
                  )}
                </div>
              )}

              {/* Voice / Avatar interactive panel */}
              <VoicePanel
                mode={mode}
                isRecording={isRecording}
                recordingTime={recordingTime}
                isPlayingAudio={isPlayingAudio}
                onStartRecord={handleStartRecord}
                onStopRecord={handleStopRecord}
                onCancelRecord={cancelRecording}
                onStopAudio={stopAudio}
                isBusy={isPending}
              />

              {recorderError && (
                <div className="p-2 mb-3 bg-red-50 text-red-600 text-xs rounded-xl border border-red-200 text-center">
                  {recorderError}
                </div>
              )}

              {/* Chat Message List */}
              <div className="space-y-1">
                {messages.map((m, idx) => (
                  <MessageItem
                    key={m.id || idx}
                    message={m}
                    isLastAssistant={idx === messages.map((x) => x.role).lastIndexOf('assistant')}
                    onOpenLead={(followUp) => {
                      setLeadDefaultFollowUp(!!followUp);
                      setLeadModalOpen(true);
                    }}
                  />
                ))}

                {/* Typing Indicator */}
                {isPending && (
                  <div className="flex items-center gap-2 text-slate-500 text-xs py-2 px-1">
                    <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold shadow-xs">
                      🔷
                    </div>
                    <div className="bg-white border border-slate-200/80 px-3.5 py-2 rounded-2xl flex items-center gap-1">
                      <span className="typing-dot"></span>
                      <span className="typing-dot"></span>
                      <span className="typing-dot"></span>
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Suggested Prompts (shown when only welcome message exists) */}
              {!hasMessages && (
                <SuggestedPrompts
                  prompts={DEFAULT_PROMPTS}
                  onSelect={(p) => handleSendText(p)}
                  disabled={isPending}
                />
              )}
            </div>
          </div>
        </div>

        {/* Input Bar */}
        <div className="border-t border-slate-200 bg-white/90 backdrop-blur p-4 shrink-0">
          <div className="max-w-3xl mx-auto flex items-center gap-2.5">
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSendText();
                }
              }}
              placeholder={
                mode === 'text'
                  ? 'Type your message… (Press Enter to send)'
                  : 'Or type your message…'
              }
              maxLength={2000}
              disabled={isPending}
              className="flex-1 bg-slate-100 hover:bg-slate-100/80 focus:bg-white text-slate-900 text-sm px-4 py-3 rounded-2xl border border-transparent focus:border-blue-500 focus:outline-hidden transition-all"
            />

            <button
              onClick={() => handleSendText()}
              disabled={!inputText.trim() || isPending}
              className="p-3 rounded-2xl bg-blue-600 hover:bg-blue-700 disabled:opacity-40 disabled:hover:bg-blue-600 text-white transition-all shadow-xs shrink-0"
              aria-label="Send message"
            >
              <Send className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>

      {/* Modals */}
      <LeadModal
        isOpen={leadModalOpen}
        onClose={() => setLeadModalOpen(false)}
        onSubmit={handleLeadSubmit}
        defaultFollowUp={leadDefaultFollowUp}
      />

      <ConfirmTranscriptModal
        confirmation={confirmation}
        onConfirm={handleConfirmTranscript}
        onCancel={() => setConfirmation(null)}
      />
    </div>
  );
};

export default App;
