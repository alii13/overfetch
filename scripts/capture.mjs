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
 *   {"blur": true}             visibilitychange(hidden) + blur, like leaving the window
 *   {"focus": true}            the reverse
 *   {"offline": true}          go offline; {"offline": false} reconnects
 *   {"block": "**\/api/v1/x/**", "status": 404}
 *                              answer matching requests with that status from now on
 *   {"label": "home"}          a marker line in the visited file
 *
 * The script waits for network idle plus --settle after every step, then
 * --idle (default 0) at the end for polling to show up. Every URL the page was
 * on is written to OUT.visited.txt, which coverage_map.py reads with --visited.
 */
import { createRequire } from "node:module"
import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const args = process.argv.slice(2)
if (args.includes("--help") || args.length < 2) {
  console.log(readFileSync(new URL(import.meta.url)).toString().split("*/")[0].replace(/^#!.*\n\/\*\*?\s?/, "").replace(/^ \* ?/gm, ""))
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
const visited = [`# ${new Date().toISOString()} ${url}`]
const note = (line) => {
  if (visited[visited.length - 1] !== line) visited.push(line)
}
page.on("framenavigated", (frame) => {
  if (frame === page.mainFrame()) note(frame.url())
})

async function quiet() {
  await page.waitForLoadState("networkidle").catch(() => {})
  await page.waitForTimeout(settle)
  note(page.url()) // client-side routing changes the URL without a navigation event
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
  else if (step.blur || step.focus) {
    const hidden = Boolean(step.blur)
    await page.evaluate((hide) => {
      Object.defineProperty(document, "visibilityState", { value: hide ? "hidden" : "visible", configurable: true })
      document.dispatchEvent(new Event("visibilitychange"))
      window.dispatchEvent(new Event(hide ? "blur" : "focus"))
    }, hidden)
  } else if (typeof step.offline === "boolean") await context.setOffline(step.offline)
  else if (step.block) {
    await context.route(step.block, (route) => route.fulfill({ status: step.status ?? 500, body: "" }))
  } else if (step.label) note(`# label: ${step.label}`)
  await quiet()
}

if (idle > 0) await page.waitForTimeout(idle)
await context.close()
await browser.close()
writeFileSync(`${out}.visited.txt`, visited.join("\n") + "\n")
console.log(out)
