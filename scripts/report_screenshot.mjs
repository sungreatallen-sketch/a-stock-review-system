// 报告 HTML → 手机宽度整页长图（供飞书发送；切片在 Python 侧用 Pillow 完成）
// 用法: node report_screenshot.mjs <html路径> <输出png>
import { createRequire } from 'module';
import { existsSync } from 'fs';
const require = createRequire('/Users/yage/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/');
const { chromium } = require('playwright');
const [htmlPath, outPng] = process.argv.slice(2);
const W = 390;
const exe = '/Users/yage/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
if (!existsSync(htmlPath)) { console.error('NO_HTML'); process.exit(1); }
const browser = await chromium.launch({ executablePath: exe });
const page = await browser.newPage({ viewport: { width: W, height: 844 }, deviceScaleFactor: 2 });
await page.goto('file://' + htmlPath, { waitUntil: 'networkidle' });
await page.waitForTimeout(1500);
await page.screenshot({ path: outPng, fullPage: true });
const H = await page.evaluate(() => document.documentElement.scrollHeight);
console.log('OK ' + H);
await browser.close();
