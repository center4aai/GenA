import { test, expect } from '@playwright/test';

// Dev is deployed with VITE_DEFAULT_LOCALE=ru; force English so text
// assertions stay stable across dev (ru) and main (en).
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem('gena_locale', 'en');
    } catch {
      /* ignore */
    }
  });
});

test('home page loads', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /GenA 2\.0/i })).toBeVisible();
  await expect(page.getByText(/Quick Start/i)).toBeVisible();
});

test('docs page renders all sections', async ({ page }) => {
  await page.goto('/docs');
  await expect(page.getByRole('heading', { name: 'Documentation', level: 1 })).toBeVisible();
  for (const section of ['About GenA', 'Sensitivity levels', 'Question types', 'How to use GenA', 'Chunk gate', 'Chunks storage', 'Validation']) {
    await expect(page.getByRole('heading', { name: section, level: 2 })).toBeVisible();
  }
});
