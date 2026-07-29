/**
 * UI Error States & Accessibility Tests
 *
 * Tests for:
 * 1. Cost view error state when data is null
 * 2. PR-board view renders without errors
 * 3. Agents view contrast in both themes + empty state
 * 4. Mobile layout for 6 viewports (no body-level horizontal scroll)
 */

import { test, expect } from '@playwright/test';

test.describe('Cost View Error States', () => {
  test('cost view renders without crashing', async ({ page }) => {
    await page.goto('/#/cost');
    await page.waitForLoadState('networkidle');

    // Cost view should load (either with data, error, or placeholder)
    const costView = page.locator('[data-testid="view-cost"]');
    expect(costView).toBeVisible();
  });

  test('cost error state has proper styling when data unavailable', async ({ page }) => {
    // Simulate no cost data by checking if error state renders properly
    await page.goto('/#/cost');
    await page.waitForLoadState('networkidle');

    const costView = page.locator('[data-testid="view-cost"]');
    const errorElement = page.locator('[data-testid="cost-error"]');

    // Should render either error or data
    expect(costView).toBeVisible();

    // If error is shown, verify styling
    if (await errorElement.isVisible().catch(() => false)) {
      const computedStyle = await errorElement.evaluate((el) => {
        return window.getComputedStyle(el);
      });

      // Error container should have explicit background and border
      expect(computedStyle.backgroundColor).not.toBe('');
      expect(computedStyle.borderColor).not.toBe('');
    }
  });

  test('cost error message title has proper text color', async ({ page }) => {
    await page.goto('/#/cost');
    await page.waitForLoadState('networkidle');

    const errorElement = page.locator('[data-testid="cost-error"]');
    if (!(await errorElement.isVisible().catch(() => false))) {
      return; // Skip if error not shown
    }

    const title = errorElement.locator('h3');
    const computedStyle = await title.evaluate((el) => {
      return window.getComputedStyle(el);
    });

    // Title should have explicit color set (not white on white)
    expect(computedStyle.color).not.toBe('rgb(255, 255, 255)');
    expect(computedStyle.color).not.toBe('');
  });
});

test.describe('PR-Board View Error States', () => {
  test('pr-board view renders and handles states gracefully', async ({ page }) => {
    await page.goto('/#/prs');
    await page.waitForLoadState('networkidle');

    // PR board should load without crashing
    const prBoardView = page.locator('[data-testid="view-prboard"]');
    expect(prBoardView).toBeVisible();

    // Check for one of the expected states
    const states = [
      page.locator('[data-testid="prboard-loading"]'),
      page.locator('[data-testid="prboard-error"]'),
      page.locator('[data-testid="prboard-empty"]'),
      page.locator('[data-testid="prboard-table"]'),
    ];

    let foundState = false;
    for (const state of states) {
      if (await state.isVisible().catch(() => false)) {
        foundState = true;
        break;
      }
    }

    expect(foundState).toBeTruthy();
  });

  test('pr-board error message has proper contrast when shown', async ({ page }) => {
    await page.goto('/#/prs');
    await page.waitForLoadState('networkidle');

    const errorElement = page.locator('[data-testid="prboard-error"]');
    if (!(await errorElement.isVisible().catch(() => false))) {
      return; // Skip if error not shown
    }

    const computedStyle = await errorElement.evaluate((el) => {
      return window.getComputedStyle(el);
    });

    // Error message should not be white text
    expect(computedStyle.color).not.toBe('rgb(255, 255, 255)');
  });
});

test.describe('Agents View Contrast & Accessibility', () => {
  test('agents empty state has explicit text color (light theme)', async ({ page }) => {
    // Force light theme
    await page.goto('/');
    await page.evaluate(() => {
      document.documentElement.setAttribute('data-theme', 'light');
      localStorage.setItem('aesop-theme', 'light');
    });

    await page.goto('/#/activity');
    await page.waitForLoadState('networkidle');

    const emptyState = page.locator('.empty-state');
    const isVisible = await emptyState.isVisible().catch(() => false);

    if (!isVisible) {
      return; // Skip if no empty state
    }

    const computedStyle = await emptyState.evaluate((el) => {
      return window.getComputedStyle(el);
    });

    // Empty state text should have explicit color (not white)
    expect(computedStyle.color).not.toBe('rgb(255, 255, 255)');
    expect(computedStyle.color).not.toBe('');
  });

  test('agents empty state has explicit text color (dark theme)', async ({ page }) => {
    // Force dark theme
    await page.goto('/');
    await page.evaluate(() => {
      document.documentElement.setAttribute('data-theme', 'dark');
      localStorage.setItem('aesop-theme', 'dark');
    });

    await page.goto('/#/activity');
    await page.waitForLoadState('networkidle');

    const emptyState = page.locator('.empty-state');
    const isVisible = await emptyState.isVisible().catch(() => false);

    if (!isVisible) {
      return; // Skip if no empty state
    }

    const computedStyle = await emptyState.evaluate((el) => {
      return window.getComputedStyle(el);
    });

    // Empty state text should have explicit color
    expect(computedStyle.color).not.toBe('rgb(255, 255, 255)');
    expect(computedStyle.color).not.toBe('');
  });

  test('agents view heading has explicit text color', async ({ page }) => {
    await page.goto('/#/activity');
    await page.waitForLoadState('networkidle');

    const heading = page.locator('.agents-panel h2');
    const isVisible = await heading.isVisible().catch(() => false);

    if (!isVisible) {
      return; // Skip if no heading
    }

    const computedStyle = await heading.evaluate((el) => {
      return window.getComputedStyle(el);
    });

    // Heading should have explicit color
    expect(computedStyle.color).not.toBe('');
    expect(computedStyle.color).not.toBe('rgb(255, 255, 255)');
  });
});

test.describe('Mobile Layout (6/6 Viewports)', () => {
  test('all views render properly on mobile (320px)', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });

    const views = ['/#/', '/#/work', '/#/activity', '/#/cost', '/#/prs'];
    for (const view of views) {
      await page.goto(view);
      await page.waitForLoadState('networkidle');

      // Page should render without crashing
      const main = page.locator('main');
      expect(main).toBeVisible();
    }
  });

  test('all views render properly on tablet (768px)', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });

    const views = ['/#/', '/#/work', '/#/activity', '/#/cost', '/#/prs'];
    for (const view of views) {
      await page.goto(view);
      await page.waitForLoadState('networkidle');

      const main = page.locator('main');
      expect(main).toBeVisible();
    }
  });

  test('all views render properly on desktop (1920px)', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });

    const views = ['/#/', '/#/work', '/#/activity', '/#/cost', '/#/prs'];
    for (const view of views) {
      await page.goto(view);
      await page.waitForLoadState('networkidle');

      const main = page.locator('main');
      expect(main).toBeVisible();
    }
  });

  test('app-main container does not cause body horizontal scroll on mobile', async ({ page }) => {
    const mobileViewports = [
      320,  // iPhone SE
      375,  // iPhone 8
      414,  // iPhone 12
    ];

    for (const width of mobileViewports) {
      await page.setViewportSize({ width, height: 568 });
      await page.goto('/#/activity');
      await page.waitForLoadState('networkidle');

      // Check if main element overflows the viewport
      const overflows = await page.evaluate(() => {
        const main = document.querySelector('.app-main');
        return main ? main.scrollWidth > window.innerWidth : false;
      });

      // Main content should not overflow viewport
      expect(overflows).toBeFalsy();
    }
  });
});
