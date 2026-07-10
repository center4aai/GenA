import { Link } from 'react-router-dom';

const STEPS = [
  {
    title: '1. Upload document',
    desc: 'Upload a .docx / .txt / .pdf and it will be split into chunks automatically.',
    to: '/data_preprocessing',
  },
  {
    title: '2. Choose settings',
    desc: 'Pick question types, select generation and validation models (gate + scoring), then Generate.',
    to: '/data_preprocessing',
  },
  {
    title: '3. Track progress',
    desc: 'Watch processing status in real time and see model health.',
    to: '/queue_manager',
  },
  {
    title: '4. View results',
    desc: 'Browse generated questions, edit inline, compare versions, and export XLSX.',
    to: '/dataset_editor',
  },
];

const PAGES = [
  {
    to: '/data_preprocessing',
    title: 'Data Preprocessing',
    desc: 'Upload a document, configure generation parameters, run direct processing or queue-based generation.',
  },
  {
    to: '/dataset_editor',
    title: 'Results & Editor',
    desc: 'Browse generated questions, edit inline, compare dataset versions, export Generation Results and Full Pipeline Data to XLSX.',
  },
  {
    to: '/queue_manager',
    title: 'Queue Manager',
    desc: 'Monitor task queues and dataset progress, see model health, retry failed tasks.',
  },
  {
    to: '/statistics',
    title: 'Statistics',
    desc: 'Aggregated analytics across datasets: question type distribution, sensitivity levels, validation rates.',
  },
  {
    to: '/dynamic_implementation',
    title: 'Dynamic Implementation',
    desc: 'Shuffle and rephrase questions using LLM to create alternative dataset variants.',
  },
  {
    to: '/docs',
    title: 'Documentation',
    desc: 'Step-by-step guide, chunk gate, weighted validation scoring, sensitivity levels.',
  },
];

export function HomePage() {
  return (
    <div>
      <div className="flex flex-col items-center text-center">
        <img
          src="/logo.png"
          alt="GenA logo"
          className="mb-6 h-40 w-auto sm:h-48 lg:h-56"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = 'none';
          }}
        />
        <h1 className="text-4xl font-bold text-slate-900">GenA 2.0</h1>
        <p className="page-subtitle mx-auto mt-2 max-w-3xl">
          Generation &amp; Sensitivity Assessment — Russian-language framework for automated
          question generation with sensitivity and quality control.
        </p>
      </div>

      <hr className="my-8 border-slate-200" />

      <h2 className="mb-2 text-xl font-semibold">Quick Start</h2>
      <p className="mb-4 text-slate-600">
        Four steps from a document to a ready dataset. Click a step to go directly to the page.
      </p>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step) => (
          <div key={step.title} className="card flex min-h-[180px] flex-col bg-stone-50">
            <h4 className="font-semibold">{step.title}</h4>
            <p className="mt-2 flex-1 text-sm text-slate-600">{step.desc}</p>
            <Link to={step.to} className="mt-3 text-sm font-medium text-gena-primary hover:underline">
              Go to {step.title.split('. ')[1]} →
            </Link>
          </div>
        ))}
      </div>

      <hr className="my-8 border-slate-200" />

      <h2 className="mb-4 text-xl font-semibold">Pages</h2>
      <div className="space-y-3">
        {PAGES.map((page) => (
          <div key={page.to} className="grid gap-2 sm:grid-cols-4">
            <Link to={page.to} className="font-semibold text-gena-primary hover:underline">
              {page.title}
            </Link>
            <p className="text-sm text-slate-500 sm:col-span-3">{page.desc}</p>
          </div>
        ))}
      </div>

      <hr className="my-8 border-slate-200" />
      <Link to="/docs" className="text-gena-primary hover:underline">
        Full documentation →
      </Link>

      <details className="mt-8 card">
        <summary className="cursor-pointer font-semibold">About GenA</summary>
        <div className="prose prose-sm mt-4 max-w-none text-slate-600">
          <p>
            <strong>GenA</strong> (Generation &amp; Sensitivity Assessment) is a dynamic Russian-language
            framework designed to bridge the gap between classical question generation and practical
            requirements for legally accurate, socially safe questions.
          </p>
          <p>
            <strong>Three question types:</strong> single-choice, multiple-choice, open-ended.
          </p>
          <p>
            <strong>Sensitivity levels (1-3):</strong>
          </p>
          <ul className="list-disc pl-5">
            <li>Level 1 — neutral topics, no potential for conflict.</li>
            <li>Level 2 — controversial topics with differing but non-extreme viewpoints.</li>
            <li>Level 3 — highly sensitive cultural, historical, or political topics.</li>
          </ul>
          <p>
            Chunks are filtered by an automated gate before generation; each generated question is
            scored with weighted validation (decimal totals). Experts can then review, edit, and approve
            the results.
          </p>
        </div>
      </details>
    </div>
  );
}
