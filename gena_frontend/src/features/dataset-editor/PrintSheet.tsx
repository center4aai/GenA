import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { isCorrectOption, optionsEntries } from '@/shared/lib/optionsFormat';
import { normalizeQuestionType } from '@/shared/lib/questionFields';
import type { Question } from '@/shared/types/documents';

/**
 * The printable question sheet. Rendered into <body> outside the app shell
 * (whose h-screen/overflow-hidden layout clips printing to one screen) and
 * shown only while `body.print-questions` is set — see index.css.
 * Options are numbered 1..N like everywhere else in the app, so the answer key
 * reads "3 — 2, 4" rather than letters.
 */
export function PrintSheet({
  datasetName,
  version,
  questions,
}: {
  datasetName: string;
  version: number | null;
  questions: Question[];
}) {
  const { t } = useTranslation('results');
  if (typeof document === 'undefined') return null;

  const answers = questions.map((q) => {
    const entries = optionsEntries(q.options);
    if (normalizeQuestionType(q.question_type) === 'open' || !entries.length) {
      return String(q.correct_answer ?? '');
    }
    return entries
      .map((e, i) => (isCorrectOption(q.correct_answer, e.key, e.index) ? String(i + 1) : null))
      .filter(Boolean)
      .join(', ');
  });

  return createPortal(
    <section className="print-sheet" aria-hidden>
      <h1>{datasetName}</h1>
      {version != null && <p>{t('version', { n: version })}</p>}
      <ol>
        {questions.map((q, n) => {
          const entries = optionsEntries(q.options);
          return (
            <li key={q.question_id ?? n} className="print-question">
              <p>
                <strong>{n + 1}.</strong> {q.task}
              </p>
              {entries.length > 0 && (
                <ul>
                  {entries.map((e, i) => (
                    <li key={e.key}>
                      {i + 1}. {e.text}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
      <h2>{t('printAnswerKey')}</h2>
      <ol>
        {answers.map((a, n) => (
          <li key={n}>
            {n + 1} — {a}
          </li>
        ))}
      </ol>
    </section>,
    document.body,
  );
}

/** Hide the app, print the sheet, restore — with a fallback for browsers that skip afterprint. */
export function printQuestionSheet() {
  const body = document.body;
  body.classList.add('print-questions');
  const cleanup = () => body.classList.remove('print-questions');
  window.addEventListener('afterprint', cleanup, { once: true });
  try {
    window.print();
  } finally {
    window.setTimeout(cleanup, 1000);
  }
}
