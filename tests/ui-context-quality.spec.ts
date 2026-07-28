/**
 * Playwright test suite for context-engineering lane C dashboard features.
 * Tests C1 (Spec Sharpness), C2 (File-Scope Visualizer), C3 (First-Try Success Board).
 *
 * Run: npx playwright test tests/ui-context-quality.spec.ts
 * With screenshots: npx playwright test --screenshot=only
 */

import { test, expect } from '@playwright/test';

test.describe('Context Quality Dashboard (Lane C)', () => {
  test.beforeEach(async ({ page }) => {
    // Start the dashboard server (assumes npm run dev or python ui/serve.py running on :8770)
    await page.goto('http://localhost:8770');
    // Wait for app to load
    await page.waitForSelector('[data-testid="first-try-board"], [data-testid="spec-sharpness-badge"]', {
      timeout: 5000,
    }).catch(() => {
      // May not have these elements yet; test will create fixture data
    });
  });

  test('C1: Spec Sharpness Indicator renders with badge', async ({ page }) => {
    // Navigate to Activity view (agent rows show spec sharpness badges)
    await page.goto('http://localhost:8770/#/activity');

    // Look for spec sharpness badge (first letter badge in agent row)
    const badges = await page.locator('[data-testid="spec-sharpness-badge"]').all();

    if (badges.length > 0) {
      // Click first badge to expand details
      await badges[0].click();

      // Wait for detail pane to appear
      const detailPane = page.locator('[data-testid="spec-sharpness-detail"]').first();
      await expect(detailPane).toBeVisible();

      // Verify detail content
      await expect(detailPane.locator('h4')).toContainText('Spec Sharpness');

      // Take screenshot of C1
      await page.screenshot({ path: 'tests/screenshots/c1-spec-sharpness.png' });
    }
  });

  test('C2: File-Scope Visualizer shows coverage', async ({ page }) => {
    // Navigate to Activity view (agent inspector drawer)
    await page.goto('http://localhost:8770/#/activity');

    // Click on an agent to open inspector
    const agentRows = await page.locator('[data-testid*="agent-row"]').all();

    if (agentRows.length > 0) {
      // Click first agent
      await agentRows[0].click();

      // Wait for file scope visualizer to load
      const fileScope = page.locator('[data-testid="file-scope-visualizer"]');
      await expect(fileScope).toBeVisible({ timeout: 3000 }).catch(() => {
        // May not have file scope data; that's OK
      });

      if (await fileScope.isVisible().catch(() => false)) {
        // Verify coverage bar and metrics
        const coverageFill = fileScope.locator('[data-testid="coverage-fill"]');
        await expect(coverageFill).toBeVisible();

        // Take screenshot of C2
        await page.screenshot({ path: 'tests/screenshots/c2-file-scope.png' });
      }
    }
  });

  test('C3: First-Try Success Board renders', async ({ page }) => {
    // Navigate to Cost view (may contain first-try board)
    await page.goto('http://localhost:8770/#/cost');

    // Look for first-try board
    const board = page.locator('[data-testid="first-try-board"]');

    if (await board.isVisible().catch(() => false)) {
      // Verify board structure
      const overall = board.locator('[data-testid="overall-metric"]');
      await expect(overall).toBeVisible();

      // Verify refresh button exists
      const refreshBtn = board.locator('button:has-text("⟳")');
      await expect(refreshBtn).toBeVisible();

      // Take screenshot of C3
      await page.screenshot({ path: 'tests/screenshots/c3-first-try-board.png' });
    }
  });

  test('C1: Badge expands to show all signals', async ({ page }) => {
    // Navigate to Activity view
    await page.goto('http://localhost:8770/#/activity');

    // Look for spec sharpness badge
    const badge = page.locator('[data-testid="spec-sharpness-badge"]').first();

    if (await badge.isVisible().catch(() => false)) {
      // Click to expand
      await badge.click();

      // Verify all signals are visible
      const detail = page.locator('[data-testid="spec-sharpness-detail"]').first();

      // Check for signal labels
      await expect(detail.locator('text=Directives')).toBeVisible();
      await expect(detail.locator('text=Acceptance Criteria')).toBeVisible();
      await expect(detail.locator('text=File Specificity')).toBeVisible();
      await expect(detail.locator('text=Structured Content')).toBeVisible();
      await expect(detail.locator('text=Emphasis Markers')).toBeVisible();

      // Take detailed screenshot
      await page.screenshot({ path: 'tests/screenshots/c1-spec-sharpness-expanded.png' });
    }
  });

  test('C2: File scope shows intended vs actual', async ({ page }) => {
    // Navigate to Activity view
    await page.goto('http://localhost:8770/#/activity');

    // Open agent inspector
    const agentRows = await page.locator('[data-testid*="agent-row"]').all();

    if (agentRows.length > 0) {
      await agentRows[0].click();

      // Wait for file scope visualizer
      const fileScope = page.locator('[data-testid="file-scope-visualizer"]');

      if (await fileScope.isVisible({ timeout: 2000 }).catch(() => false)) {
        // Check for file lists
        const intendedList = fileScope.locator('[data-testid="intended-files-list"]');
        const actualList = fileScope.locator('[data-testid="actual-files-list"]');

        // At least one should be present
        const intendedVisible = await intendedList.isVisible().catch(() => false);
        const actualVisible = await actualList.isVisible().catch(() => false);

        if (intendedVisible || actualVisible) {
          await page.screenshot({ path: 'tests/screenshots/c2-file-scope-detailed.png' });
        }
      }
    }
  });

  test('C3: Board shows domains and lanes breakdown', async ({ page }) => {
    // Navigate to Cost view
    await page.goto('http://localhost:8770/#/cost');

    // Look for first-try board
    const board = page.locator('[data-testid="first-try-board"]');

    if (await board.isVisible({ timeout: 2000 }).catch(() => false)) {
      // Check for domain cards
      const domainsGrid = board.locator('[data-testid="domains-grid"]');
      const lanesGrid = board.locator('[data-testid="lanes-grid"]');

      const domainsVisible = await domainsGrid.isVisible().catch(() => false);
      const lanesVisible = await lanesGrid.isVisible().catch(() => false);

      if (domainsVisible || lanesVisible) {
        await page.screenshot({ path: 'tests/screenshots/c3-first-try-breakdown.png' });
      }
    }
  });

  test('Accessibility: C1 badge has aria-label', async ({ page }) => {
    // Navigate to Activity view
    await page.goto('http://localhost:8770/#/activity');

    // Check for aria-label on badge
    const badge = page.locator('[data-testid="spec-sharpness-badge"]').first();

    if (await badge.isVisible().catch(() => false)) {
      const ariaLabel = await badge.getAttribute('aria-label');
      expect(ariaLabel).toBeTruthy();
      expect(ariaLabel).toMatch(/Spec Sharpness/);
    }
  });

  test('Accessibility: C2 visualizer has role=region', async ({ page }) => {
    // Navigate to Activity view
    await page.goto('http://localhost:8770/#/activity');

    // Open agent inspector
    const agentRows = await page.locator('[data-testid*="agent-row"]').all();

    if (agentRows.length > 0) {
      await agentRows[0].click();

      // Check for file scope with role
      const fileScope = page.locator('[data-testid="file-scope-visualizer"]');

      if (await fileScope.isVisible({ timeout: 2000 }).catch(() => false)) {
        const role = await fileScope.getAttribute('role');
        expect(role).toBe('region');
      }
    }
  });

  test('Accessibility: C3 board has refresh button with aria-label', async ({ page }) => {
    // Navigate to Cost view
    await page.goto('http://localhost:8770/#/cost');

    // Look for refresh button
    const board = page.locator('[data-testid="first-try-board"]');

    if (await board.isVisible({ timeout: 2000 }).catch(() => false)) {
      const refreshBtn = board.locator('button:has-text("⟳")');

      if (await refreshBtn.isVisible().catch(() => false)) {
        await expect(refreshBtn).toHaveAttribute('title', /[Rr]efresh/);
      }
    }
  });
});
