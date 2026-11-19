import React, { useState } from 'react';
import { ChevronDown, ChevronUp, ExternalLink, Mic, AlertCircle, FileText, Users, Send } from 'lucide-react';
import { Message, SourceCitation, SuggestedAction } from '../types';

interface MessageItemProps {
  message: Message;
  isLastAssistant: boolean;
  onOpenLead: (isFollowUp?: boolean) => void;
}

export const MessageItem: React.FC<MessageItemProps> = ({
  message,
  isLastAssistant,
  onOpenLead,
}) => {
  const isUser = message.role === 'user';
  const [sourcesOpen, setSourcesOpen] = useState(false);

  return (
    <div className={`flex gap-3 my-4 ${isUser ? 'justify-end' : 'justify-start'}`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center shrink-0 text-sm shadow-xs font-bold">
          🔷
        </div>
      )}

      <div className={`max-w-[85%] md:max-w-[75%] flex flex-col gap-1.5 ${isUser ? 'items-end' : 'items-start'}`}>
        {/* Message Bubble */}
        <div
          className={`px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap break-words shadow-xs ${
            isUser
              ? 'bg-blue-600 text-white rounded-tr-xs'
              : 'bg-white border border-slate-200/80 text-slate-800 rounded-tl-xs'
          }`}
        >
          {message.content}
        </div>

        {/* Badges */}
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          {message.voice && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 text-slate-600 border border-slate-200">
              <Mic className="w-3 h-3 text-blue-500" /> voice
            </span>
          )}
          {message.status === 'pending' && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-blue-50 text-blue-600 border border-blue-200">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse"></span> sending…
            </span>
          )}
          {message.status === 'failed' && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-red-50 text-red-600 border border-red-200">
              <AlertCircle className="w-3 h-3" /> not sent
            </span>
          )}
          {!isUser && (message.status === 'low_confidence' || message.status === 'no_knowledge') && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-50 text-amber-700 border border-amber-200">
              <AlertCircle className="w-3 h-3 text-amber-500" /> not found in verified documentation
            </span>
          )}
        </div>

        {/* Sources Accordion */}
        {!isUser && message.sources && message.sources.length > 0 && (
          <div className="w-full mt-1">
            <button
              onClick={() => setSourcesOpen(!sourcesOpen)}
              className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800 transition-colors py-1 px-1.5 rounded"
            >
              <FileText className="w-3.5 h-3.5 text-blue-500" />
              <span>Verified Sources ({message.sources.length})</span>
              {sourcesOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>

            {sourcesOpen && (
              <div className="mt-1.5 space-y-2 bg-slate-50/80 border border-slate-200 rounded-xl p-2.5 text-xs">
                {message.sources.map((src: SourceCitation, idx) => (
                  <div key={idx} className="pb-1.5 border-b border-slate-200/60 last:border-0 last:pb-0">
                    <div className="font-semibold text-slate-700">
                      [{src.index}] {src.title || src.source_document}
                    </div>
                    <div className="text-slate-500 flex items-center justify-between mt-0.5">
                      <span>
                        {src.source_document}
                        {src.page_number ? `, page ${src.page_number}` : ''}
                      </span>
                      {src.source_url && (
                        <a
                          href={src.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 hover:underline flex items-center gap-0.5"
                        >
                          open <ExternalLink className="w-2.5 h-2.5" />
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Suggested Actions (shown only on the latest assistant response) */}
        {!isUser && isLastAssistant && message.actions && message.actions.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {message.actions.map((act: SuggestedAction, idx) => (
              <button
                key={idx}
                onClick={() => {
                  if (act.type === 'request_human_handoff') {
                    onOpenLead(true);
                  } else {
                    onOpenLead(false);
                  }
                }}
                className="inline-flex items-center gap-1.5 text-xs font-medium bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200 px-3 py-1.5 rounded-xl transition-colors shadow-xs"
              >
                <Users className="w-3.5 h-3.5" />
                <span>{act.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {isUser && (
        <div className="w-8 h-8 rounded-full bg-slate-200 text-slate-700 flex items-center justify-center shrink-0 text-sm shadow-xs font-bold">
          🙂
        </div>
      )}
    </div>
  );
};
