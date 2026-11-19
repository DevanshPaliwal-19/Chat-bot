import React, { useState } from 'react';
import { AlertTriangle, Check, X } from 'lucide-react';
import { PendingConfirmation } from '../types';

interface ConfirmTranscriptModalProps {
  confirmation: PendingConfirmation | null;
  onConfirm: (text: string) => void;
  onCancel: () => void;
}

export const ConfirmTranscriptModal: React.FC<ConfirmTranscriptModalProps> = ({
  confirmation,
  onConfirm,
  onCancel,
}) => {
  if (!confirmation) return null;

  const [text, setText] = useState(confirmation.transcript);

  const handleSend = () => {
    if (text.trim()) {
      onConfirm(text.trim());
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs">
      <div className="bg-white rounded-2xl max-w-md w-full p-5 shadow-2xl border border-slate-200">
        <div className="flex items-center gap-2.5 text-amber-600 mb-2">
          <AlertTriangle className="w-5 h-5" />
          <h3 className="font-semibold text-slate-900 text-sm">Verify voice transcript</h3>
        </div>

        <p className="text-xs text-slate-500 mb-3">
          We weren't 100% sure we heard you correctly. Please check and edit before sending:
        </p>

        <textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="w-full text-sm p-3 border border-slate-200 rounded-xl focus:outline-hidden focus:ring-2 focus:ring-blue-500"
          autoFocus
        />

        <div className="mt-4 flex justify-end gap-2 text-xs">
          <button
            onClick={onCancel}
            className="px-3.5 py-2 rounded-xl text-slate-600 hover:bg-slate-100 font-medium flex items-center gap-1"
          >
            <X className="w-3.5 h-3.5" /> Cancel
          </button>
          <button
            onClick={handleSend}
            disabled={!text.trim()}
            className="px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-700 text-white font-medium flex items-center gap-1.5 shadow-sm disabled:opacity-50"
          >
            <Check className="w-3.5 h-3.5" /> Send message
          </button>
        </div>
      </div>
    </div>
  );
};
