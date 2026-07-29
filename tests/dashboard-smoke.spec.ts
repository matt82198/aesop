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

/**
 * Mission-Control MVP proofs (HealthHeader status badges, grouped agents,
 * no-horizontal-scroll at the 3 evaluation viewports).
 */
test.describe('Mission-Control MVP', () => {
  test('HealthHeader is visible with status-first zones and agent status badges', async ({ page }) => {
    await page.goto('/#/');
    await page.waitForLoadState('networkidle');

    const header = page.locator('[data-testid="health-header"]');
    await expect(header).toBeVisible();

    // Status-first 3-zone layout (fleet | system | controls)
    await expect(page.locator('[data-testid="health-zone-fleet"]')).toBeVisible();
    await expect(page.locator('[data-testid="health-zone-system"]')).toBeVisible();
    await expect(page.locator('[data-testid="health-zone-controls"]')).toBeVisible();

    // Agent status breakdown badges (real counts derived from the live agents array)
    await expect(page.locator('[data-testid="health-agents-running"]')).toBeVisible();
    await expect(page.locator('[data-testid="health-agents-idle"]')).toBeVisible();
    await expect(page.locator('[data-testid="health-agents-issues"]')).toBeVisible();

    // Cost snapshot cell (real /api/cost data or an honest "n/a" empty-state)
    await expect(page.locator('[data-testid="health-cost"]')).toBeVisible();
  });

  test('AgentsPanel renders status-grouped sections (Running / Idle / Warnings)', async ({ page }) => {
    await page.goto('/#/');
    await page.waitForLoadState('networkidle');

    const summaryCards = page.locator('[data-testid^="agents-summary-card-"]');
    // Running, Idle, Warnings — always 3 summary cards regardless of fleet state
    await expect(summaryCards).toHaveCount(3);

    for (const status of ['running', 'idle', 'warnings']) {
      await expect(page.locator(`[data-testid="agents-summary-card-${status}"]`)).toBeVisible();
    }
  });

  test('Wave Progress card is visible with phase and pass-rate bar', async ({ page }) => {
    await page.goto('/#/');
    await page.waitForLoadState('networkidle');

    const waveProgress = page.locator('[data-testid="wave-telemetry-progress"]');
    await expect(waveProgress).toBeVisible();
    await expect(page.locator('.wave-progress-bar')).toBeVisible();
  });

  test('no horizontal scroll at 1440px, 768px, or 375px', async ({ page }) => {
    const viewports = [
      { width: 1440, height: 900 },
      { width: 768, height: 1024 },
      { width: 375, height: 812 },
    ];

    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.goto('/#/');
      await page.waitForLoadState('networkidle');

      const overflows = await page.evaluate(() => document.body.scrollWidth > window.innerWidth);
      expect(overflows, `horizontal overflow at ${viewport.width}px`).toBeFalsy();
    }
  });
});
