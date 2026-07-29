import { test, expect } from '@playwright/test';

test('dashboard smoke test: server starts and loads root page without errors', async ({ page }) => {
  const consoleErrors: string[] = [];

  // Capture any console errors
  page.on('console', msg => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
    }
  });

  // Navigate to the dashboard root
  const response = await page.goto('/');

  // Verify the page loaded successfully
  expect(response?.status()).toBe(200);

  // Verify the page contains the dashboard shell
  const title = await page.title();
  expect(title).toBeTruthy();

  // Verify no console errors occurred
  expect(consoleErrors).toHaveLength(0);
});
