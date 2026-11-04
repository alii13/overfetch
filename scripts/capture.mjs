#!/usr/bin/env node
/**
 * Record a HAR of a browser journey with Playwright.
 *
 *   node capture.mjs URL OUT.har [--settle MS] [--idle MS] [--journey FILE.json]
 *                                [--headed] [--viewport WxH] [--storage FILE]
 *
 * Playwright is resolved from the current project (or a global install); it is
 * not bundled with the skill. Install it in the app under audit with
 * `npm i -D playwright && npx playwright install chromium` if it is missing.
 *
 * journey.json is a list of steps, run in order after the first navigation:
 *   {"goto": "/path"}          navigate within the same origin
 *   {"click": "text=Agents"}   Playwright selector
 *   {"hover": "[data-row]"}    hover the first match; add "all": true to hover each match
 *   {"type": ["input[name=q]", "lineage"]}
 *   {"wait": 3000}             pause in ms
 *   {"blur": true}             dispatch visibilitychange(hidden) + blur, like leaving the window
 *   {"focus": true}            the reverse
 *   {"label": "home"}          start a new HAR page so the analyser can split counts
 *
 * The script waits for network idle plus --settle after every step, then
 * --idle (default 0) at the end for polling to show up.
 */
import { createRequire } from "node:module"
import { readFileSync } from "node:fs"
import path from "node:path"

const args = process.argv.slice(2)
if (args.includes("--help") || args.length < 2) {
  console.log(readFileSync(new URL(import.meta.url)).toString().split("*/")[0].replace(/^\/\*\*?\s?/, "").replace(/^ \* ?/gm, ""))
  process.exit(args.includes("--help") ? 0 : 2)
}

const [url, out] = args
const opt = (name, fallback) => {
  const i = args.indexOf(name)
  return i >= 0 ? args[i + 1] : fallback
}
const settle = Number(opt("--settle", 2000))
const idle = Number(opt("--idle", 0))
const journeyFile = opt("--journey", null)
const headed = args.includes("--headed")
const [vw, vh] = String(opt("--viewport", "1440x900")).split("x").map(Number)
const storage = opt("--storage", null)

let playwright
try {
  const require = createRequire(path.join(process.cwd(), "package.json"))
  playwright = require("playwright")
} catch {
  try {
    playwright = await import("playwright")
  } catch {
    console.error("playwright is not installed here. Run: npm i -D playwright && npx playwright install chromium")
    process.exit(2)
  }
}

const browser = await playwright.chromium.launch({ headless: !headed })
const context = await browser.newContext({
  viewport: { width: vw, height: vh },
  recordHar: { path: out, content: "omit" },
  storageState: storage ?? undefined,
})
const page = await context.newPage()

async function quiet() {
  await page.waitForLoadState("networkidle").catch(() => {})
  await page.waitForTimeout(settle)
}

await page.goto(url)
await quiet()

const steps = journeyFile ? JSON.parse(readFileSync(journeyFile, "utf8")) : []
for (const step of steps) {
  if (step.goto) await page.goto(new URL(step.goto, url).toString())
  else if (step.click) await page.click(step.click)
  else if (step.hover) {
    if (step.all) {
      for (const el of await page.locator(step.hover).all()) await el.hover()
    } else await page.hover(step.hover)
  } else if (step.type) await page.fill(step.type[0], step.type[1])
  else if (step.wait) await page.waitForTimeout(step.wait)
  else if (step.blur) {
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true })
      document.dispatchEvent(new Event("visibilitychange"))
      window.dispatchEvent(new Event("blur"))
    })
  } else if (step.focus) {
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true })
      document.dispatchEvent(new Event("visibilitychange"))
      window.dispatchEvent(new Event("focus"))
    })
  } else if (step.label) {
    // Playwright has no public "new HAR page" API; the label is recorded as a
    // marker request the analyser can see in the timeline.
    await page.evaluate((label) => fetch(`/__request-audit__/${encodeURIComponent(label)}`).catch(() => {}), step.label)
  }
  await quiet()
}

if (idle > 0) await page.waitForTimeout(idle)
await context.close()
await browser.close()
console.log(out)
