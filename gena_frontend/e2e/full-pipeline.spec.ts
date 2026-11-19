import path from 'path';
import { fileURLToPath } from 'url';
import { test, expect } from '@playwright/test';

// ESM: __dirname is not defined, derive it from import.meta.url.
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Dev is deployed with VITE_DEFAULT_LOCALE=ru; force English so text
// assertions are stable across dev (ru) and main (en). The app reads the
// locale from localStorage['gena_locale'] before rendering.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem('gena_locale', 'en');
    } catch {
      /* ignore */
    }
  });
});

/**
 * Full preprocessing pipeline E2E skeleton.
 *
 * Requires backend services when GENA_E2E_SERVICES=1:
 *   - dataset_api, agent_api, chunker (or mocked via PLAYWRIGHT_BASE_URL proxy)
 *
 * By default tests are skipped so CI/local runs without the stack stay green.
 */
const SERVICES_AVAILABLE = process.env.GENA_E2E_SERVICES === '1';
const PREPROCESSING_PATH = '/data_preprocessing';
const SAMPLE_DOC = path.join(__dirname, 'fixtures', 'sample.txt');

test.describe('Full preprocessing pipeline', () => {
  test.skip(!SERVICES_AVAILABLE, 'Set GENA_E2E_SERVICES=1 when backend stack is up');

  test.beforeEach(async ({ page }) => {
    await page.goto(PREPROCESSING_PATH);
    await expect(page.getByRole('heading', { name: 'Create questions', level: 1 })).toBeVisible();
  });

  test('page loads preprocessing controls', async ({ page }) => {
    await expect(page.getByText(/dataset|upload|chunk/i).first()).toBeVisible();
  });

  test('upload → generate redirects to queue overview', async ({ page, request }) => {
    // The default name is "<file> — <date>", so a second run on the same day
    // trips the duplicate-name guard and the button never enables. Use a unique
    // name and delete the dataset afterwards (the API cascades to its queue).
    const runName = `e2e sample ${Date.now()}`;
    try {
      const fileInput = page.locator('input[type="file"]');
      await expect(fileInput).toBeVisible();
      await fileInput.setInputFiles(SAMPLE_DOC);
      await expect(page.getByText(/File uploaded:/i)).toBeVisible();
      const nameInput = page.locator('label:has-text("Document name") + input');
      await expect(nameInput).toBeVisible();
      await nameInput.fill(runName);
      const generate = page.getByRole('button', { name: /Generate questions/i });
      await expect(generate).toBeEnabled({ timeout: 30_000 });
      await generate.click();
      await expect(page).toHaveURL(/queue_overview/, { timeout: 120_000 });
    } finally {
      // Best effort: with auth enabled this needs an expert token and may 401.
      try {
        const list = await request.get('/api/dataset/datasets/');
        if (list.ok()) {
          const datasets = (await list.json()) as Array<{ _id: string; name: string }>;
          for (const ds of datasets.filter((d) => d.name === runName)) {
            await request.delete(`/api/dataset/datasets/${ds._id}`);
          }
        }
      } catch {
        /* cleanup is best effort */
      }
    }
  });

  test('navigates to progress from sidebar', async ({ page }) => {
    const queueLink = page.getByRole('link', { name: /progress/i });
    await expect(queueLink).toBeVisible();
    await queueLink.click();
    await expect(page).toHaveURL(/queue_manager/);
  });
});

test.describe('Preprocessing UI (always runs)', () => {
  test('preprocessing route renders without backend', async ({ page }) => {
    await page.goto(PREPROCESSING_PATH);
    await expect(page.getByRole('heading', { name: 'Create questions', level: 1 })).toBeVisible();
    await expect(page.locator('input[type="file"]')).toBeVisible();
    await expect(page.getByText(/dataset|upload|chunk/i).first()).toBeVisible();
  });
});
