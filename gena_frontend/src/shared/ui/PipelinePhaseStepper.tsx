import { useTranslation } from 'react-i18next';
import type { PipelinePhase } from '@/shared/lib/progress';

function PhaseCircle({ phase }: { phase: PipelinePhase }) {
  const { t } = useTranslation('common');
  if (phase.state === 'completed') {
    return (
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500 text-white shadow-sm">
        <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5" aria-hidden>
          <path
            fillRule="evenodd"
            d="M16.7 5.3a1 1 0 0 1 0 1.4l-7.5 7.5a1 1 0 0 1-1.4 0l-3.5-3.5a1 1 0 1 1 1.4-1.4l2.8 2.8 6.8-6.8a1 1 0 0 1 1.4 0z"
            clipRule="evenodd"
          />
        </svg>
      </div>
    );
  }
  if (phase.state === 'active') {
    return (
      <div className="flex h-10 w-10 items-center justify-center rounded-full border-2 border-emerald-500 bg-white text-emerald-600 shadow-sm">
        <span className="animate-pulse text-lg" aria-hidden>⏳</span>
      </div>
    );
  }
  if (phase.state === 'failed') {
    return (
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-red-500 text-white shadow-sm">
        <span className="text-lg font-bold" aria-hidden>×</span>
      </div>
    );
  }
  if (phase.state === 'disabled') {
    return (
      <div
        className="flex h-10 w-10 items-center justify-center rounded-full border-2 border-dashed border-slate-300 bg-slate-50 text-slate-400 shadow-sm"
        title={t('common:pipelineDisabled')}
      >
        <span className="text-lg font-semibold leading-none" aria-hidden>—</span>
      </div>
    );
  }
  return <div className="h-10 w-10 rounded-full border-2 border-slate-300 bg-slate-100" />;
}

function Connector({ fraction }: { fraction: number }) {
  const pct = Math.max(0, Math.min(100, fraction * 100));
  return (
    <div className="mx-1 h-1.5 flex-1 overflow-hidden rounded-full bg-slate-300/70">
      <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
    </div>
  );
}

/**
 * Unified pipeline progress bar (Chunking → Gate → Generation → Done) for the
 * Progress page. Connector after each phase fills with that phase's progress.
 */
export function PipelinePhaseStepper({
  phases,
  labels,
}: {
  phases: PipelinePhase[];
  labels: Record<string, string>;
}) {
  const { t } = useTranslation('common');
  return (
    <div className="w-full" aria-label={t('common:pipelineProgress')}>
      <div className="flex items-center">
        {phases.map((phase, i) => (
          <div key={phase.key} className={`flex items-center ${i < phases.length - 1 ? 'flex-1' : ''}`}>
            <PhaseCircle phase={phase} />
            {i < phases.length - 1 && (
              <Connector
                fraction={
                  phase.state === 'completed' || phase.state === 'disabled' || phase.state === 'failed'
                    ? 1
                    : phase.state === 'pending'
                      ? 0
                      : phase.fraction
                }
              />
            )}
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between">
        {phases.map((phase) => (
          <div key={phase.key} className="flex flex-col items-center text-center" style={{ minWidth: '5rem' }}>
            <span
              className={`text-xs font-medium ${
                phase.state === 'active'
                  ? 'text-emerald-700'
                  : phase.state === 'completed'
                    ? 'text-slate-700'
                    : phase.state === 'failed'
                      ? 'text-red-600'
                      : phase.state === 'disabled'
                        ? 'text-slate-400 line-through'
                        : 'text-slate-400'
              }`}
            >
              {labels[phase.key] ?? phase.key}
            </span>
            {phase.state === 'disabled' && (
              <span className="text-[11px] text-slate-400">{labels.disabled ?? t('common:pipelineOff')}</span>
            )}
            {phase.state !== 'disabled'
              && phase.key !== 'done'
              && phase.key !== 'chunking'
              && phase.total > 0 && (
              <span className="text-[11px] text-slate-500">
                {phase.completed}/{phase.total}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
