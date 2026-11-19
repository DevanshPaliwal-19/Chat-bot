import React from 'react';
import { MessageSquare, Mic, UserCheck, Menu, Wifi, WifiOff } from 'lucide-react';
import { BackendConfig, HealthStatus, InteractionMode } from '../types';

interface HeaderProps {
  config: BackendConfig | null;
  health: HealthStatus | null;
  backendOk: boolean;
  mode: InteractionMode;
  onModeChange: (mode: InteractionMode) => void;
  onToggleSidebar: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  config,
  health,
  backendOk,
  mode,
  onModeChange,
  onToggleSidebar,
}) => {
  return (
    <header className="h-16 border-b border-slate-200 bg-white/80 backdrop-blur px-4 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-3">
        <button
          onClick={onToggleSidebar}
          className="md:hidden p-2 text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-lg"
          aria-label="Toggle menu"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold text-sm shadow-sm">
            🔷
          </div>
          <div>
            <h1 className="font-semibold text-slate-900 text-sm leading-tight">
              {config?.assistant_name || 'Osiya'}
            </h1>
            <p className="text-xs text-slate-500">
              {config?.company_name || 'Osiyatech'} AI Assistant
            </p>
          </div>
        </div>
      </div>

      {/* Mode Picker Tabs */}
      <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200 text-xs font-medium">
        <button
          onClick={() => onModeChange('text')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-all ${
            mode === 'text'
              ? 'bg-white text-blue-700 shadow-sm font-semibold'
              : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          <MessageSquare className="w-3.5 h-3.5" />
          <span>Text</span>
        </button>

        <button
          onClick={() => onModeChange('voice')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-all ${
            mode === 'voice'
              ? 'bg-white text-blue-700 shadow-sm font-semibold'
              : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          <Mic className="w-3.5 h-3.5" />
          <span>Voice</span>
        </button>

        <button
          onClick={() => onModeChange('avatar')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-all ${
            mode === 'avatar'
              ? 'bg-white text-blue-700 shadow-sm font-semibold'
              : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          <UserCheck className="w-3.5 h-3.5" />
          <span>Avatar</span>
        </button>
      </div>

      {/* Health Status Indicator */}
      <div className="hidden sm:flex items-center gap-2 text-xs">
        {backendOk ? (
          <div className="flex items-center gap-1.5 text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full border border-emerald-200">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span>Online</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 text-amber-700 bg-amber-50 px-2.5 py-1 rounded-full border border-amber-200">
            <WifiOff className="w-3.5 h-3.5 text-amber-500" />
            <span>Disconnected</span>
          </div>
        )}
      </div>
    </header>
  );
};
