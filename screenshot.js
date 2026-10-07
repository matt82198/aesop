const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:4321', { waitUntil: 'networkidle' });
  
  // Scroll to writing section
  const writingSection = await page.$('#writing');
  if (writingSection) {
    await writingSection.scrollIntoViewIfNeeded();
  }
  
  // Take screenshot
  await page.screenshot({ path: '/c/Users/matt8/AppData/Local/Temp/claude/C--Users-matt8-aesop/62fcf286-5da1-4c81-bce3-ca0e6f258e35/scratchpad/codal/portfolio-writing.png', fullPage: false });
  
  await browser.close();
  process.exit(0);
})();
