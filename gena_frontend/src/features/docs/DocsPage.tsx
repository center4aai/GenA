import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { PageHeader } from '@/shared/ui/PageHeader';
import { usePageToc } from '@/shared/lib/toc';

const SECTIONS = [
  { id: 'about', label: 'About GenA' },
  { id: 'sensitivity', label: 'Sensitivity levels' },
  { id: 'types', label: 'Question types' },
  { id: 'howto', label: 'How to use GenA' },
  { id: 'gate', label: 'Chunk gate' },
  { id: 'chunks-storage', label: 'Chunks storage' },
  { id: 'validation', label: 'Validation' },
];

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="card scroll-mt-6">
      <h2 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h2>
      <div className="mt-4 space-y-4 text-[15px] leading-relaxed text-slate-600">{children}</div>
    </section>
  );
}

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.85em] text-slate-800">{children}</code>
  );
}

function Callout({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-r-lg border-l-4 border-gena-primary bg-gena-primary/5 px-4 py-3 text-sm text-slate-700">
      {children}
    </div>
  );
}

function Step({ n, title, children, action }: { n: number; title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex gap-4">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gena-primary/10 text-sm font-semibold text-gena-primary">
        {n}
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="font-semibold text-slate-900">{title}</h3>
        <div className="mt-1 space-y-2 text-[15px] leading-relaxed text-slate-600">{children}</div>
        {action && <div className="mt-3">{action}</div>}
      </div>
    </div>
  );
}

export function DocsPage() {
  usePageToc(SECTIONS);
  return (
    <div>
      <PageHeader
        title="Documentation"
        subtitle="Step-by-step guide, chunk gate, weighted validation scoring, and sensitivity levels."
      />

      <div className="mx-auto max-w-3xl">
        <div className="min-w-0 space-y-6">
          <Section id="about" title="About GenA">
            <p>
              GenA (Generation &amp; Sensitivity Assessment) is a dynamic Russian-language framework
              designed to address the gap between classical question generation (QG) and practical
              requirements for legally accurate, socially safe questions. It gives users a controllable,
              transparent tool while minimizing conflict risks associated with sensitive topics
              (cultural, historical, and linguistic).
            </p>
            <p className="font-medium text-slate-700">The framework provides three key contributions:</p>
            <ol className="list-decimal space-y-2 pl-5 marker:text-slate-400">
              <li>
                <strong className="text-slate-800">Generating diverse questions</strong> from Russian
                texts (socially significant sources such as encyclopedias, regulatory legal acts, and
                legal documents).
              </li>
              <li>
                <strong className="text-slate-800">Automatically assigning sensitivity levels</strong>{' '}
                that reflect conflict potential within sociocultural contexts.
              </li>
              <li>
                <strong className="text-slate-800">Human-in-the-loop validation.</strong> Dual-validation
                combined with human review lets users evaluate quality, accept or reject questions, and
                confirm or modify sensitivity levels.
              </li>
            </ol>
          </Section>

          <Section id="sensitivity" title="Sensitivity levels">
            <p>
              Each question receives a sensitivity annotation from 1 to 3, based on the perceived
              probability of conflict within a given sociocultural context at a fixed point in time:
            </p>
            <div className="overflow-hidden rounded-xl border border-slate-200">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <th className="px-4 py-2">Level</th>
                    <th className="px-4 py-2">Description</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  <tr>
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-emerald-700">1 — Low</td>
                    <td className="px-4 py-3">Neutral topics that do not foster discussion or differences of opinion.</td>
                  </tr>
                  <tr>
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-amber-700">2 — Medium</td>
                    <td className="px-4 py-3">
                      Controversial or ambiguous topics. Different viewpoints exist but are not radically
                      opposed; discussions may spark debate without serious conflict.
                    </td>
                  </tr>
                  <tr>
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-red-700">3 — High</td>
                    <td className="px-4 py-3">
                      Highly sensitive cultural, historical, or political topics. Responses may require
                      expressing personal opinions on contentious issues.
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p>
              Users evaluate both generation quality and sensitivity-level appropriateness, forming a
              feedback loop for continuous improvement.
            </p>
          </Section>

          <Section id="types" title="Question types">
            <p>Generation involves three question types:</p>
            <ul className="space-y-2">
              <li className="flex gap-2">
                <span className="text-gena-primary">•</span>
                <span><strong className="text-slate-800">Single-choice</strong> (<Code>one</Code>) — one correct answer from multiple options.</span>
              </li>
              <li className="flex gap-2">
                <span className="text-gena-primary">•</span>
                <span><strong className="text-slate-800">Multiple-choice</strong> (<Code>multi</Code>) — several correct answers from the options provided.</span>
              </li>
              <li className="flex gap-2">
                <span className="text-gena-primary">•</span>
                <span><strong className="text-slate-800">Open-ended</strong> (<Code>open</Code>) — no options provided.</span>
              </li>
            </ul>
          </Section>

          <Section id="howto" title="How to use GenA">
            <div className="space-y-6">
              <Step
                n={1}
                title="Upload a document"
                action={<Link to="/data_preprocessing" className="btn-primary inline-block">Open Data Preprocessing</Link>}
              >
                <p>
                  Go to <strong className="text-slate-800">Data Preprocessing</strong> and click the
                  upload area. Supported formats: <Code>.docx</Code>, <Code>.txt</Code>, <Code>.pdf</Code>.
                  The document is automatically split into logical chunks with a progress bar and chunk count.
                </p>
              </Step>

              <Step n={2} title="Select question types and configure generation">
                <p>Choose one or more question types (<Code>one</Code>, <Code>multi</Code>, <Code>open</Code>).</p>
                <Callout>
                  By default all three types are selected. Clearing the selection resets it to all three —
                  you cannot run generation with an empty list.
                </Callout>
                <ul className="list-disc space-y-1 pl-5 marker:text-slate-400">
                  <li>
                    <strong className="text-slate-800">Model selection</strong> — pick the LLM for
                    generation (questions, sensitivity, difficulty) and for validation (chunk gate + scoring).
                  </li>
                  <li>
                    <strong className="text-slate-800">Queue Mode (recommended)</strong> — tasks run in a
                    background queue you can track in Queue Manager.
                  </li>
                  <li>
                    <strong className="text-slate-800">Direct Processing</strong> — questions generated
                    immediately on the page (slower for large documents).
                  </li>
                </ul>
              </Step>

              <Step n={3} title="Preview chunks">
                <p>
                  Expand <strong className="text-slate-800">Preview chunks</strong> to inspect the
                  fragments the chunker produced. Adjacent chunks share a small overlap (last 1–2
                  sentences) so no information is lost at boundaries.
                </p>
              </Step>

              <Step n={4} title="Generate questions">
                <p>
                  Click <strong className="text-slate-800">Generate Questions</strong>. In Queue mode the
                  system runs these steps before creating tasks:
                </p>
                <ol className="list-decimal space-y-1 pl-5 marker:text-slate-400">
                  <li><strong className="text-slate-800">Chunk gate</strong> — each chunk is checked for suitability; failing chunks are discarded.</li>
                  <li><strong className="text-slate-800">Save chunks</strong> — only passing chunks are stored in a separate <Code>chunks</Code> collection.</li>
                  <li><strong className="text-slate-800">Create tasks</strong> — one task per valid chunk per type, with <Code>chunk_pre_validated=true</Code>.</li>
                </ol>
                <p>In Direct Processing the gate runs inside each generation call; rejected chunks produce empty results.</p>
              </Step>

              <Step
                n={5}
                title="Track progress"
                action={<Link to="/queue_manager" className="btn-secondary inline-block">Open Queue Manager</Link>}
              >
                <p>
                  Use <strong className="text-slate-800">Queue Manager</strong> to monitor queue
                  statistics, per-dataset progress bars, model health, and failed tasks (with one-click retry).
                </p>
              </Step>

              <Step
                n={6}
                title="View and edit results"
                action={<Link to="/dataset_editor" className="btn-secondary inline-block">Open Results &amp; Editor</Link>}
              >
                <p>
                  In <strong className="text-slate-800">Results &amp; Editor</strong> each question shows
                  its source text, task, options, correct answer, sensitivity (1–3), difficulty (1–3), and
                  validation score with a detailed breakdown. Edit inline, save new versions, compare
                  versions, and export.
                </p>
              </Step>

              <Step n={7} title="Download as XLSX">
                <p>
                  Use <strong className="text-slate-800">Download XLSX</strong> in Results &amp; Editor to
                  export generation results (and optional full-pipeline data) as a spreadsheet.
                </p>
              </Step>

              <Step
                n={8}
                title="Analyze statistics"
                action={<Link to="/statistics" className="btn-secondary inline-block">Open Statistics</Link>}
              >
                <p>
                  The <strong className="text-slate-800">Statistics</strong> page shows aggregated
                  analytics: question-type, sensitivity, and difficulty distributions, validation pass
                  rates, and processing-status breakdown.
                </p>
              </Step>
            </div>
          </Section>

          <Section id="gate" title="Chunk gate">
            <p>
              Before generation, every chunk is assessed by a <strong className="text-slate-800">gate</strong>{' '}
              using the validation model. Three binary criteria are checked:
            </p>
            <ul className="list-disc space-y-1 pl-5 marker:text-slate-400">
              <li><strong className="text-slate-800">c1 — Informative:</strong> does the chunk contain enough substance for a question?</li>
              <li><strong className="text-slate-800">c2 — Reference clarity:</strong> is the chunk understandable without external sources?</li>
              <li><strong className="text-slate-800">c3 — Multi suitability</strong> (only for <Code>multi</Code>): can the chunk support plausible wrong answers?</li>
            </ul>
            <p>
              If c1 or c2 fails, the chunk is discarded entirely. If c3 fails, only <Code>multi</Code>{' '}
              tasks are skipped — <Code>one</Code> and <Code>open</Code> can still proceed.
            </p>
            <Callout>
              In Queue mode the gate runs before tasks are created (rejected chunks never enter the queue).
              In Direct mode it runs inside each generation call; rejected chunks produce empty results.
            </Callout>
          </Section>

          <Section id="chunks-storage" title="Chunks storage">
            <p>
              Chunks are stored <strong className="text-slate-800">separately</strong> from datasets in a
              dedicated <Code>chunks</Code> collection, linked by <Code>dataset_id</Code>. Each record
              holds the text, fragment metadata, gate results, and the question types it is valid for.
              This keeps dataset queries (metadata, questions, versions) lightweight and lets chunks be
              re-processed independently.
            </p>
            <p>
              The <strong className="text-slate-800">Existing Datasets</strong> table on Data Preprocessing
              shows the chunk count (valid / total) per dataset.
            </p>
          </Section>

          <Section id="validation" title="Validation">
            <p>Every generated question goes through automated quality validation that checks:</p>
            <ul className="list-disc space-y-1 pl-5 marker:text-slate-400">
              <li>Factual correctness against the source text</li>
              <li>Answer completeness and consistency</li>
              <li>Question clarity and grammatical quality</li>
              <li>Option plausibility (for single/multi choice)</li>
            </ul>
            <p>
              <strong className="text-slate-800">Scoring.</strong> Sub-criteria are combined using
              weights (some count half, others are emphasised). Several criteria are{' '}
              <strong className="text-slate-800">critical multipliers</strong>: if any scores zero, the
              overall total becomes zero. The reported score is therefore a decimal (e.g.{' '}
              <Code>15.5 / 20.5</Code>), not just whole numbers.
            </p>
            <p>
              Validation produces a numeric score and a pass/fail verdict against a configurable threshold.
              Experts can then review, override, and refine the results in the editor.
            </p>
          </Section>

          <div className="pt-2">
            <Link to="/" className="text-sm font-medium text-gena-primary hover:underline">← Back to Home</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
