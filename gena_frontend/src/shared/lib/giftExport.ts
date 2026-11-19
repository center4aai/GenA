import type { Question } from '../types/documents';
import { coerceOptionsLike } from './optionsFormat';

function escapeGift(text: string): string {
  return String(text ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/~/g, '\\~')
    .replace(/=/g, '\\=')
    .replace(/#/g, '\\#')
    .replace(/\{/g, '\\{')
    .replace(/\}/g, '\\}')
    .replace(/:/g, '\\:');
}

function optionEntries(opts: unknown): [number, string][] {
  const coerced = coerceOptionsLike(opts, opts);
  if (typeof coerced !== 'object' || coerced === null || Array.isArray(coerced)) return [];
  return Object.entries(coerced as Record<string, string>)
    .filter(([, v]) => v != null && v !== 'None')
    .map(([k, v]) => {
      const num = parseInt(k.replace('option_', ''), 10);
      return [Number.isNaN(num) ? 0 : num, String(v)] as [number, string];
    })
    .sort(([a], [b]) => a - b);
}

function correctIndices(correctAnswer: unknown, optionTexts: [number, string][]): Set<number> {
  const raw = String(correctAnswer ?? '').trim();
  if (!raw) return new Set();
  const set = new Set<number>();
  const byLetter: Record<string, number> = {};
  for (const [n] of optionTexts) {
    if (n >= 1 && n <= 26) byLetter[String.fromCharCode(64 + n)] = n;
  }
  for (const part of raw.split(/[,;]+/)) {
    const p = part.trim();
    if (!p) continue;
    const letter = p.toUpperCase();
    if (byLetter[letter]) {
      set.add(byLetter[letter]);
      continue;
    }
    const n = parseInt(p.replace(/^option_/i, ''), 10);
    if (!Number.isNaN(n)) {
      set.add(n);
      continue;
    }
    const match = optionTexts.find(([, text]) => text.trim() === p);
    if (match) set.add(match[0]);
  }
  return set;
}

/** Build Moodle GIFT text from questions (client-side, no deps). */
export function buildGiftExport(questions: Question[], category?: string): string {
  const blocks: string[] = [];
  // Questions whose correct answer cannot be resolved are left out rather than
  // emitted as an invalid block that makes Moodle reject the entire import.
  const skipped: string[] = [];
  if (category) {
    blocks.push(`$CATEGORY: ${escapeGift(category)}`);
  }

  questions.forEach((q, i) => {
    const title = `::Q${i + 1}::`;
    const stem = escapeGift(String(q.task ?? '').trim() || `Question ${i + 1}`);
    const type = String(q.question_type ?? 'open').toLowerCase();
    const entries = optionEntries(q.options);
    const correct = correctIndices(q.correct_answer, entries);

    if (type === 'open' || type === 'open_question' || entries.length === 0) {
      const ans = String(q.correct_answer ?? '').trim();
      if (ans) {
        blocks.push(`${title}\n${stem}{\n=${escapeGift(ans)}\n}`);
      } else {
        blocks.push(`${title}\n${stem}{}`);
      }
      return;
    }

    if (type === 'multi' || type === 'multiple' || type === 'multiple_choice') {
      const nCorrect = Math.max(1, [...correct].filter((idx) => entries.some(([n]) => n === idx)).length);
      const nWrong = Math.max(1, entries.length - nCorrect);
      const weight = Math.round((100 / nCorrect) * 100000) / 100000;
      // Moodle needs negative weights on distractors: leaving them at 0 let a
      // student tick every option and still score 100%.
      const penalty = Math.round((100 / nWrong) * 100000) / 100000;
      const lines = entries.map(([n, text]) => {
        if (correct.has(n)) return `~%${weight}%${escapeGift(text)}`;
        return `~%-${penalty}%${escapeGift(text)}`;
      });
      blocks.push(`${title}\n${stem}{\n${lines.join('\n')}\n}`);
      return;
    }

    // single choice (one)
    if (![...correct].some((idx) => entries.some(([n]) => n === idx))) {
      // No resolvable correct option: a block with only «~» lines makes Moodle
      // reject the whole file, so skip the question and let the caller report it.
      skipped.push(String(q.task ?? '').slice(0, 80));
      return;
    }
    const lines = entries.map(([n, text]) => {
      if (correct.has(n)) return `=${escapeGift(text)}`;
      return `~${escapeGift(text)}`;
    });
    blocks.push(`${title}\n${stem}{\n${lines.join('\n')}\n}`);
  });

  if (skipped.length) {
    // A GIFT comment: visible to the teacher, ignored by the importer.
    blocks.push(
      `// skipped ${skipped.length} question(s) with an unresolvable correct answer`,
    );
  }
  return blocks.join('\n\n') + '\n';
}

export function downloadGift(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.endsWith('.gift') ? filename : `${filename}.gift`;
  a.click();
  URL.revokeObjectURL(url);
}
