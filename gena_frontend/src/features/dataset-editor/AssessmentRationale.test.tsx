import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import i18n from '@/shared/i18n';
import type { Question } from '@/shared/types/documents';
import { AssessmentRationale } from './AssessmentRationale';

const t = (key: string, opts?: Record<string, unknown>) => String(i18n.t(key, opts as never));

const Q: Question = {
  question_id: 'q1',
  provocativeness: '3',
  difficulty: '2',
  sensitivity_explanation: 'Touches a contested topic.',
  difficulty_explanation: 'Needs two facts from the text.',
};

describe('AssessmentRationale', () => {
  it('shows the model rating kept by the server, not the user pick', () => {
    render(<AssessmentRationale question={{ ...Q, provocativeness_original: '1' }} />);
    expect(screen.getByText(t('results:sensitivityOriginal', { level: t('results:levelLow') }))).toBeTruthy();
    // difficulty was never changed, so the live value is the original
    expect(screen.getByText(t('results:difficultyOriginal', { level: t('results:levelMedium') }))).toBeTruthy();
  });

  it('falls back to the live level for questions edited before originals were kept', () => {
    render(<AssessmentRationale question={Q} />);
    expect(screen.getByText(t('results:sensitivityOriginal', { level: t('results:levelHigh') }))).toBeTruthy();
  });

  it('renders nothing without an explanation to show', () => {
    const { container } = render(<AssessmentRationale question={{ question_id: 'q2', provocativeness: '1' }} />);
    expect(container.firstChild).toBeNull();
  });
});
