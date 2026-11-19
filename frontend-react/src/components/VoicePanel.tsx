import React from 'react';
import { Mic, Square, Volume2, X } from 'lucide-react';
import { InteractionMode } from '../types';

interface VoicePanelProps {
  mode: InteractionMode;
  isRecording: boolean;
  recordingTime: number;
  isPlayingAudio: boolean;
  onStartRecord: () => void;
  onStopRecord: () => void;
  onCancelRecord: () => void;
  onStopAudio: () => void;
  isBusy: boolean;
}

export const VoicePanel: React.FC<VoicePanelProps> = ({
  mode,
  isRecording,
  recordingTime,
  isPlayingAudio,
  onStartRecord,
  onStopRecord,
  onCancelRecord,
  onStopAudio,
  isBusy,
}) => {
  if (mode === 'text') return null;

  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  return (
    <div className="bg-gradient-to-b from-blue-50/60 to-slate-50 border border-blue-100/80 rounded-2xl p-4 my-3 flex flex-col items-center justify-center transition-all shadow-xs">
      {/* Avatar Display for 'avatar' mode */}
      {mode === 'avatar' && (
        <div className="relative mb-3">
          <div
            className={`w-28 h-28 rounded-full overflow-hidden border-4 transition-all duration-300 shadow-md ${
              isPlayingAudio
                ? 'border-blue-500 ring-4 ring-blue-300/60 scale-105'
                : isRecording
                ? 'border-red-400 ring-4 ring-red-200'
                : 'border-white'
            }`}
          >
            <img
              src="/avatar.jpg"
              alt="AI Assistant Avatar"
              className="w-full h-full object-cover"
              onError={(e) => {
                // Fallback placeholder if image not found
                e.currentTarget.src = 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80';
              }}
            />
          </div>

          {isPlayingAudio && (
            <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 bg-blue-600 text-white text-[10px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1 shadow-xs">
              <Volume2 className="w-3 h-3 animate-pulse" /> Speaking
            </div>
          )}
        </div>
      )}

      {/* Audio Wave animation when AI is speaking */}
      {isPlayingAudio && (
        <div className="flex items-center gap-3 my-2 bg-white px-3 py-1.5 rounded-full border border-blue-200 shadow-xs">
          <div className="flex items-center gap-1">
            <span className="wave-bar"></span>
            <span className="wave-bar"></span>
            <span className="wave-bar"></span>
            <span className="wave-bar"></span>
            <span className="wave-bar"></span>
          </div>
          <span className="text-xs text-blue-800 font-medium">Assistant speaking…</span>
          <button
            onClick={onStopAudio}
            className="text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 px-2 py-0.5 rounded-md font-medium"
          >
            Stop
          </button>
        </div>
      )}

      {/* Recording controls */}
      <div className="flex flex-col items-center gap-2 mt-1">
        {isRecording ? (
          <div className="flex items-center gap-3">
            <button
              onClick={onCancelRecord}
              className="p-2.5 rounded-full bg-slate-200 hover:bg-slate-300 text-slate-700 transition-colors"
              title="Cancel recording"
            >
              <X className="w-5 h-5" />
            </button>

            <button
              onClick={onStopRecord}
              className="px-5 py-3 rounded-full bg-red-600 hover:bg-red-700 text-white flex items-center gap-2 font-medium shadow-md animate-pulse transition-transform hover:scale-105"
            >
              <Square className="w-4 h-4 fill-white" />
              <span>Stop & Send ({formatTime(recordingTime)})</span>
            </button>
          </div>
        ) : (
          <button
            onClick={onStartRecord}
            disabled={isBusy}
            className="px-6 py-3 rounded-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white flex items-center gap-2.5 font-medium shadow-md transition-all hover:scale-105 active:scale-95"
          >
            <Mic className="w-5 h-5" />
            <span>Tap to speak</span>
          </button>
        )}

        <p className="text-xs text-slate-500 mt-1">
          {isRecording
            ? 'Listening… tap Stop when finished'
            : isPlayingAudio
            ? 'Speaking reply (tap Stop or start talking to interrupt)'
            : 'Click microphone to record a voice question'}
        </p>
      </div>
    </div>
  );
};
