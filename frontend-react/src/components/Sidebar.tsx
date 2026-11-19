import React from 'react';
import { PlusCircle, Trash2, Users, Database, Cpu, CheckCircle2, AlertTriangle, X } from 'lucide-react';
import { BackendConfig, HealthStatus } from '../types';

interface SidebarProps {
  config: BackendConfig | null;
  health: HealthStatus | null;
  backendOk: boolean;
  isOpen: boolean;
  onClose: () => void;
  onNewChat: () => void;
  onClearChat: () => void;
  onOpenLead: (isFollowUp?: boolean) => void;
  isBusy: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  config,
  health,
  backendOk,
  isOpen,
  onClose,
  onNewChat,
  onClearChat,
  onOpenLead,
  isBusy,
}) => {
  const kbReady = health?.knowledge_base?.ready ?? false;
  const llmOk = health?.llm?.status === 'ok';

  return (
    <>
      {/* Mobile Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-slate-900/40 z-40 md:hidden backdrop-blur-sm"
          onClick={onClose}
        />
      )}

      <aside
        className={`fixed md:static inset-y-0 left-0 z-50 w-72 bg-white border-r border-slate-200 flex flex-col justify-between p-4 transition-transform duration-200 ease-in-out md:translate-x-0 ${
          isOpen ? 'translate-x-0 shadow-2xl md:shadow-none' : '-translate-x-full'
        }`}
      >
        <div className="flex flex-col gap-6">
          {/* Header & Close Button */}
          <div className="flex items-center justify-between pb-2 border-b border-slate-100">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-blue-600 flex items-center justify-center text-white font-bold text-base shadow-sm">
                🔷
              </div>
              <div>
                <h2 className="font-semibold text-slate-900 text-sm">
                  {config?.assistant_name || 'Osiya'}
                </h2>
                <p className="text-xs text-slate-500">
                  {config?.company_name || 'Osiyatech'}
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="md:hidden p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Action Buttons */}
          <div className="flex flex-col gap-2">
            <button
              onClick={onNewChat}
              disabled={isBusy}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-xl transition-colors disabled:opacity-50"
            >
              <PlusCircle className="w-4 h-4 text-blue-600" />
              <span>New conversation</span>
            </button>

            <button
              onClick={onClearChat}
              disabled={isBusy}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-xl transition-colors disabled:opacity-50"
            >
              <Trash2 className="w-4 h-4 text-slate-500" />
              <span>Clear conversation</span>
            </button>

            <button
              onClick={() => onOpenLead(true)}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-xl transition-colors"
            >
              <Users className="w-4 h-4 text-blue-600" />
              <span>Talk to the team</span>
            </button>
          </div>

          {/* System Health / Status info */}
          <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 text-xs flex flex-col gap-2">
            <div className="font-medium text-slate-700 pb-1 border-b border-slate-200/60 flex items-center justify-between">
              <span>System Status</span>
              {backendOk ? (
                <span className="flex items-center gap-1 text-emerald-600 font-semibold">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span> Active
                </span>
              ) : (
                <span className="flex items-center gap-1 text-amber-600 font-semibold">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500"></span> Offline
                </span>
              )}
            </div>

            <div className="flex items-center justify-between text-slate-600">
              <span className="flex items-center gap-1.5">
                <Database className="w-3.5 h-3.5 text-slate-400" /> Knowledge Base
              </span>
              {kbReady ? (
                <span className="text-emerald-600 flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3" /> Ready
                </span>
              ) : (
                <span className="text-amber-600 flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3" /> Not indexed
                </span>
              )}
            </div>

            <div className="flex items-center justify-between text-slate-600">
              <span className="flex items-center gap-1.5">
                <Cpu className="w-3.5 h-3.5 text-slate-400" /> AI Model
              </span>
              {llmOk ? (
                <span className="text-emerald-600 flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3" /> Ready
                </span>
              ) : (
                <span className="text-amber-600 flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3" /> Limited
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Disclaimer footer */}
        <div className="text-[11px] leading-relaxed text-slate-400 pt-4 border-t border-slate-100">
          <p>
            Osiya answers from verified company documentation. Please do not share passwords or confidential credentials.
          </p>
        </div>
      </aside>
    </>
  );
};
