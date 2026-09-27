// Renders docs/images/readme-title-analystos.png from title-card.html.
//
//   make setup-e2e                       # once: installs Playwright and its Chromium
//   node docs/src/render-title-card.mjs  # from the repository root
//
// The card is 1200x440 rendered at 2x (a 2400x880 PNG), following the title-card
// convention used by the other repos. Playwright is resolved from tests/e2e, whose
// lockfile pins it, so this script adds no dependency of its own. Set CHROMIUM_PATH
// to override the browser binary (some Playwright builds hang on screenshot under
// WSL2). If Inter or JetBrains Mono fail to load, nothing is written: a card in a
// fallback font must never replace the committed one.

import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(new URL("../../tests/e2e/package.json", import.meta.url));
const { chromium } = require("@playwright/test");

const here = path.dirname(fileURLToPath(import.meta.url));
const source = path.join(here, "title-card.html");
const out = path.resolve(here, "..", "images", "readme-title-analystos.png");

// Same rendering flags as tests/e2e/scripts/screenshots.mjs.
const browser = await chromium.launch({
  args: ["--disable-gpu", "--disable-gpu-compositing", "--disable-software-rasterizer"],
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
});
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 440 }, deviceScaleFactor: 2 });
  await page.goto(pathToFileURL(source).href, { waitUntil: "networkidle" });
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  const fonts = await page.evaluate(() => ({
    inter: document.fonts.check('700 40px "Inter"'),
    mono: document.fonts.check('500 12px "JetBrains Mono"'),
  }));
  if (!fonts.inter || !fonts.mono) {
    console.error(
      `error: web fonts did not load (Inter: ${fonts.inter}, JetBrains Mono: ${fonts.mono}); ` +
        "the card needs network access to Google Fonts. Nothing was written.",
    );
    process.exitCode = 1;
  } else {
    await page.screenshot({ path: out, clip: { x: 0, y: 0, width: 1200, height: 440 }, animations: "disabled" });
    console.log(out);
  }
} finally {
  await browser.close();
}
