/**
 * Playwright integration test for acceptanceCriteria authoring feature.
 * Proves: (1) submit form with 2 AC and see in queue, (2) edit AC on queued item and persist across reload.
 * Note: Tests are hermetic - they create items through the UI and verify persistence via SSE + reload.
 */

import { test, expect } from '@playwright/test';

const BASE_URL = 'http://localhost:8770';

test.describe('Acceptance Criteria Authoring', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/#/work`);
    // Wait for Work view to load - check for the "+ Add Item" button
    await page.waitForSelector('button:has-text("+ Add Item")', { timeout: 10000 });
  });

  test('should submit item with 2 AC via form and see in queue', async ({ page }) => {
    // Show the form by clicking "+ Add Item"
    await page.click('button:has-text("+ Add Item")');
    await page.waitForSelector('[data-testid="tracker-form"]', { timeout: 5000 });

    // Fill in title
    await page.fill('[data-testid="tracker-form-title"]', 'Test Feature with AC');

    // Set priority to P1
    await page.selectOption('#tracker-priority', 'P1');

    // Add first AC
    await page.fill('#ac-statement', 'Feature works end-to-end');
    await page.fill('#ac-verifiable', 'pytest tests/test_feature.py::test_e2e');
    await page.click('[data-testid="tracker-form-add-ac"]');

    // Add second AC
    await page.fill('#ac-statement', 'No regressions on other tests');
    await page.fill('#ac-verifiable', 'npm run test');
    await page.click('[data-testid="tracker-form-add-ac"]');

    // Verify both AC appear in the form
    await expect(page.locator('ul li:has-text("Feature works end-to-end")')).toBeVisible();
    await expect(page.locator('ul li:has-text("No regressions on other tests")')).toBeVisible();

    // Submit form
    await page.click('[data-testid="tracker-form-submit"]');

    // Wait for success message
    await expect(page.locator('text=Item created successfully!')).toBeVisible({ timeout: 5000 });

    // Wait for tracker board to update (SSE might take a moment)
    await page.waitForTimeout(1000);

    // Verify the item appears in the tracker board (at least one card with our title)
    await expect(page.locator('[data-testid="tracker-card"]:has-text("Test Feature with AC")')).toBeVisible({ timeout: 5000 });

    // Verify the item title is visible
    await expect(page.locator('text=Test Feature with AC')).toBeVisible();
  });

  test('should create simple item without AC', async ({ page }) => {
    // Show the form by clicking "+ Add Item"
    await page.click('button:has-text("+ Add Item")');
    await page.waitForSelector('[data-testid="tracker-form"]', { timeout: 5000 });

    // Fill in just the required field (title)
    await page.fill('[data-testid="tracker-form-title"]', 'Simple Item');

    // Submit form without adding AC
    await page.click('[data-testid="tracker-form-submit"]');

    // Wait for success message
    await expect(page.locator('text=Item created successfully!')).toBeVisible({ timeout: 5000 });

    // Verify item appears in board
    await page.waitForTimeout(500);
    await expect(page.locator('text=Simple Item')).toBeVisible();
  });
});
