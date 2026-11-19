import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import i18n from '@/shared/i18n';
import { ModelHealthBanner } from './ModelHealthBanner';

const t = (key: string, opts?: Record<string, unknown>) => String(i18n.t(key, opts as never));

const up = { id: 'up', name: 'Qwen up', available: true };
const down = { id: 'down', name: 'Gemma down', available: false, reason: 'not served' };

describe('ModelHealthBanner', () => {
  it('counts and names only the available models, never the unavailable ones', () => {
    const { container } = render(<ModelHealthBanner health={[up, down]} />);
    expect(screen.getByText(t('progress:modelsAvailable', { n: 1 }))).toBeTruthy();
    expect(screen.getByText('Qwen up')).toBeTruthy();
    expect(container.textContent).not.toContain('Gemma down');
  });

  it('warns when no model is available, without listing the down ones', () => {
    const { container } = render(<ModelHealthBanner health={[down]} />);
    expect(screen.getByText(t('progress:modelsUnavailable'))).toBeTruthy();
    expect(container.textContent).not.toContain('Gemma down');
  });

  it('says there is no health data when the list is empty', () => {
    render(<ModelHealthBanner health={[]} />);
    expect(screen.getByText(t('progress:noModelHealth'))).toBeTruthy();
  });
});
