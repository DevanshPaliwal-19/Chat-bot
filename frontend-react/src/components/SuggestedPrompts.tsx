import React from 'react';
import { Sparkles } from 'lucide-react';

interface SuggestedPromptsProps {
  prompts: string[];
  onSelect: (prompt: string) => void;
  disabled?: boolean;
}

export const SuggestedPrompts: React.FC<SuggestedPromptsProps> = ({
  prompts,
  onSelect,
  disabled = false,
}) => {
  if (!prompts || prompts.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 my-4">
      <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500 px-1">
        <Sparkles className="w-3.5 h-3.5 text-blue-500" />
        <span>Suggested questions:</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {prompts.map((p, idx) => (
          <button
            key={idx}
            disabled={disabled}
            onClick={() => onSelect(p)}
            className="text-xs text-left bg-white hover:bg-blue-50/70 border border-slate-200 hover:border-blue-300 text-slate-700 hover:text-blue-700 py-2 px-3.5 rounded-xl transition-all shadow-xs disabled:opacity-50"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
};
