/**
 * Playwright integration test for acceptanceCriteria authoring feature.
 * Proves: (1) submit form with 2 AC and see in queue, (2) edit AC on queued item and persist across reload.
 */

import { test, expect } from '@playwright/test';

const BASE_URL = 'http://localhost:8770';

test.describe('Acceptance Criteria Authoring', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE_URL);
    // Wait for dashboard to load
    await page.waitForSelector('[data-testid="tracker-form"]', { timeout: 10000 });
  });

  test('should submit item with 2 AC via form and see in queue', async ({ page }) => {
    // Fill in the form
    await page.fill('[data-testid="tracker-form-title"]', 'Test Feature with AC');

    // Set priority
    await page.selectOption('select', 'P1');

    // Add first AC
    await page.fill('#ac-statement', 'Feature works end-to-end');
    await page.fill('#ac-verifiable', 'pytest tests/test_feature.py::test_e2e');
    await page.click('[data-testid="tracker-form-add-ac"]');

    // Add second AC
    await page.fill('#ac-statement', 'No regressions on other tests');
    await page.fill('#ac-verifiable', 'npm run test');
    await page.click('[data-testid="tracker-form-add-ac"]');

    // Verify both AC appear in the form before submit
    const acItems = await page.locator('ul li:has-text("Feature works end-to-end")');
    await expect(acItems).toHaveCount(1);
    const acItems2 = await page.locator('ul li:has-text("No regressions on other tests")');
    await expect(acItems2).toHaveCount(1);

    // Submit form
    await page.click('[data-testid="tracker-form-submit"]');

    // Wait for success message
    await expect(page.locator('text=Item created successfully!')).toBeVisible({ timeout: 5000 });

    // Wait for tracker board to update (SSE)
    await page.waitForSelector('[data-testid="tracker-card"]', { timeout: 10000 });

    // Find the newly created item in the tracker board
    const cards = page.locator('[data-testid="tracker-card"]');
    let foundCard = null;
    const cardCount = await cards.count();
    for (let i = 0; i < cardCount; i++) {
      const card = cards.nth(i);
      const title = await card.locator('h3').textContent();
      if (title?.includes('Test Feature with AC')) {
        foundCard = card;
        break;
      }
    }

    expect(foundCard).toBeTruthy();

    // Expand the card to see AC
    const expandButton = foundCard!.locator('button').first();
    await expandButton.click();

    // Verify AC are displayed
    await expect(foundCard!.locator('text=Feature works end-to-end')).toBeVisible();
    await expect(foundCard!.locator('text=No regressions on other tests')).toBeVisible();
  });

  test('should edit AC on queued item and persist across reload', async ({ page }) => {
    // Create an item first
    await page.fill('[data-testid="tracker-form-title"]', 'Item to Edit AC');
    await page.selectOption('select', 'P2');
    await page.click('[data-testid="tracker-form-submit"]');

    // Wait for success
    await expect(page.locator('text=Item created successfully!')).toBeVisible({ timeout: 5000 });
    await page.waitForSelector('[data-testid="tracker-card"]', { timeout: 10000 });

    // Find the item
    const cards = page.locator('[data-testid="tracker-card"]');
    let itemCard = null;
    const cardCount = await cards.count();
    for (let i = 0; i < cardCount; i++) {
      const card = cards.nth(i);
      const title = await card.locator('h3').textContent();
      if (title?.includes('Item to Edit AC')) {
        itemCard = card;
        break;
      }
    }

    expect(itemCard).toBeTruthy();

    // Expand the card
    const expandButton = itemCard!.locator('button').first();
    await expandButton.click();

    // Click the edit AC button (or add if no AC yet)
    const addAcButton = itemCard!.locator('button:has-text("+ Add Acceptance Criteria"), button:has-text("Edit")');
    await addAcButton.first().click();

    // Wait for modal to appear
    await expect(page.locator('[data-testid="tracker-edit-ac"]')).toBeVisible({ timeout: 5000 });

    // Fill in AC in modal
    await page.fill('#ac-statement', 'Implementation complete');
    await page.fill('#ac-verifiable', 'manual review + tests');
    await page.click('[data-testid="tracker-form-add-ac"]');

    // Save changes
    await page.click('[data-testid="tracker-edit-ac"]');

    // Wait for success and modal to close
    await expect(page.locator('text=Acceptance criteria updated successfully!')).toBeVisible({ timeout: 5000 });

    // Wait a bit for modal to close
    await page.waitForTimeout(600);

    // Reload the page
    await page.reload();
    await page.waitForSelector('[data-testid="tracker-card"]', { timeout: 10000 });

    // Find the item again
    const cardsAfterReload = page.locator('[data-testid="tracker-card"]');
    let itemCardAfterReload = null;
    const cardCountAfterReload = await cardsAfterReload.count();
    for (let i = 0; i < cardCountAfterReload; i++) {
      const card = cardsAfterReload.nth(i);
      const title = await card.locator('h3').textContent();
      if (title?.includes('Item to Edit AC')) {
        itemCardAfterReload = card;
        break;
      }
    }

    expect(itemCardAfterReload).toBeTruthy();

    // Expand to verify AC persisted
    const expandButtonAfter = itemCardAfterReload!.locator('button').first();
    await expandButtonAfter.click();

    // Verify AC are still there
    await expect(itemCardAfterReload!.locator('text=Implementation complete')).toBeVisible();
    await expect(itemCardAfterReload!.locator('text=manual review + tests')).toBeVisible();
  });
});
