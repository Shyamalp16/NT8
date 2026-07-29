import { chromium } from "file:///C:/Users/patel/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";
import { pathToFileURL } from "node:url";

const reportPath = process.argv[2];
if (!reportPath) throw new Error("report path required");

const browser = await chromium.launch({
  executablePath: "C:/Users/patel/AppData/Local/ms-playwright/chromium-1187/chrome-win/chrome.exe",
  headless: true
});
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(pathToFileURL(reportPath).href);
  await page.waitForTimeout(500);
  const result = await page.evaluate(() => {
    const root = document.documentElement;
    const viewportWidth = root.clientWidth;
    const overflowing = [...document.querySelectorAll("*")]
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName,
          className: typeof element.className === "string" ? element.className : "",
          left: Math.round(rect.left * 10) / 10,
          right: Math.round(rect.right * 10) / 10,
          width: Math.round(rect.width * 10) / 10,
          scrollWidth: element.scrollWidth,
          clientWidth: element.clientWidth
        };
      })
      .filter((item) => item.right > viewportWidth + 1 || item.left < -1 || item.scrollWidth > item.clientWidth + 1)
      .sort((a, b) => Math.max(b.right - viewportWidth, b.scrollWidth - b.clientWidth) - Math.max(a.right - viewportWidth, a.scrollWidth - a.clientWidth))
      .slice(0, 30);
    return {
      viewportWidth,
      rootScrollWidth: root.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      overflowing
    };
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} finally {
  await browser.close();
}
