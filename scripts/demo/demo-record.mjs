// Demo footage: run one feature as a fresh visitor with the browser's video recorder on.
//   node demo-record.mjs <converge|solve|frontier|studio|ask> [minutes]
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
const { chromium } = createRequire(import.meta.url)('playwright')
const [feature, minutesArg = '30', custom = ''] = process.argv.slice(2)
const minutes = Number(minutesArg)
const slug = custom ? '-' + custom.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) : ''
const origin = 'https://askpolymath.com'
const out = path.join(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'demo', feature + slug)
fs.rmSync(out, { recursive: true, force: true }); fs.mkdirSync(out, { recursive: true })
const log = (m) => { const line = `${new Date().toISOString().slice(11, 19)} ${m}`; console.log(line); fs.appendFileSync(path.join(out, 'journey.log'), line + '\n') }
const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, acceptDownloads: true, recordVideo: { dir: out, size: { width: 1280, height: 800 } } })
const page = await context.newPage()
page.on('response', (r) => { if (r.status() >= 400 && r.url().includes('/api/')) log(`HTTP ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`) })
const text = async () => (await page.locator('main').innerText().catch(() => '')).replace(/\s+/g, ' ')
const waitEnabled = async (locator, seconds = 90) => { await locator.waitFor({ state: 'visible' }); for (let i = 0; i < seconds && await locator.isDisabled(); i++) await page.waitForTimeout(1000); return !(await locator.isDisabled()) }
// A slow, human-paced scroll so the recording shows the whole result.
const tour = async () => { const height = await page.evaluate(() => document.documentElement.scrollHeight); for (let y = 0; y < height; y += 180) { await page.evaluate((v) => window.scrollTo({ top: v, behavior: 'smooth' }), y); await page.waitForTimeout(450) } await page.waitForTimeout(1500); await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await page.waitForTimeout(1500) }
const watch = async (terminal, every = 10_000) => {
  const started = Date.now(); let last = ''
  while (Date.now() - started < minutes * 60_000) {
    const t = await text(); const tail = t.slice(-500)
    if (tail !== last) { log(`page: ${tail.slice(-220)}`); last = tail }
    if (terminal(t)) { log('terminal state detected'); break }
    await page.waitForTimeout(every)
  }
}
const started = Date.now()
if (feature === 'converge') {
  await page.goto(origin, { waitUntil: 'load' }); await page.waitForTimeout(2500)
  await page.getByRole('group', { name: 'Research mode' }).getByRole('button', { name: 'Converge', exact: true }).click(); await page.waitForTimeout(1500)
  const form = page.getByRole('form', { name: 'Start a converge circle' })
  await form.locator('textarea').first().pressSequentially(custom || 'how systems remember', { delay: 60 })
  const ceiling = form.getByLabel('Spending ceiling'); if (await ceiling.count()) await ceiling.fill('1.50').catch(() => {})
  const submit = form.getByRole('button', { name: /Begin converging/ }); await waitEnabled(submit); await page.waitForTimeout(800); await submit.click(); log('submitted')
  // Keep the circle (the Ensō and its narration) in view while it works, then show the finished state.
  await page.waitForTimeout(3000)
  const circle = page.locator('.converge-programme').first()
  const frame = async () => { await circle.scrollIntoViewIfNeeded().catch(() => {}); await page.evaluate(() => window.scrollBy({ top: -24, behavior: 'smooth' })) }
  await frame()
  const terminal = (t) => /(Paused at your spending ceiling|Finished: )/.test(t)
  const startedWatch = Date.now(); let lastSeen = ''
  while (Date.now() - startedWatch < minutes * 60_000) {
    const t = await text(); const narration = (await page.locator('.converge-narration').innerText().catch(() => '')).replace(/\s+/g, ' ')
    const tally = (await page.locator('.converge-tally').innerText().catch(() => '')).replace(/\s+/g, ' ')
    const line = `${narration} | ${tally}`; if (line !== lastSeen) { log(`circle: ${line.slice(0, 220)}`); lastSeen = line }
    if (terminal(t)) { log('terminal state detected'); break }
    await frame(); await page.waitForTimeout(8000)
  }
  await page.waitForTimeout(4000); await tour()
} else if (feature === 'solve') {
  await page.goto(origin, { waitUntil: 'load' }); await page.waitForTimeout(2500)
  await page.getByRole('group', { name: 'Research mode' }).getByRole('button', { name: 'Solve', exact: true }).click(); await page.waitForTimeout(1500)
  await page.locator('#solve-problem').pressSequentially(custom || 'How could batteries be recycled without loss of capacity?', { delay: 45 })
  const submit = page.getByRole('button', { name: /Find or forge a key/ }); await waitEnabled(submit); await page.waitForTimeout(800); await submit.click(); log('submitted')
  await watch((t) => /(key found|key forged|close fits?|no key yet|An answer exists|A key already exists|A new key was forged)/i.test(t) && !/running|searching|solving…|in progress|Stage \d of|Finding|Forging/i.test(t))
  await tour()
} else if (feature === 'frontier') {
  await page.goto(`${origin}/frontier`, { waitUntil: 'load' }); await page.waitForTimeout(2500)
  await page.locator('#frontier-prompt').pressSequentially(custom || 'How could a city cut its summer heat without more air conditioning?', { delay: 45 })
  const submit = page.locator('form:has(#frontier-prompt) button[type="submit"]'); await waitEnabled(submit); await page.waitForTimeout(800); await submit.click(); log('submitted')
  const run = page.getByRole('button', { name: /Run programme/ }); if (await run.count() && await waitEnabled(run, 30)) { await run.click(); log('running') }
  // Follow the programme and its map while it works (the page header stays still otherwise).
  await page.waitForTimeout(3000)
  const programme = page.locator('.frontier-programme').first(), map = page.locator('.frontier-map').first()
  const frame = async () => { const target = (await map.count()) ? map : programme; await target.evaluate((el) => el.scrollIntoView({ block: 'start', behavior: 'smooth' })).catch(() => {}); await page.waitForTimeout(600); await page.evaluate(() => window.scrollBy({ top: -112, behavior: 'smooth' })) }
  await frame()
  const startedWatch = Date.now(); let lastSeen = '', used = 0
  while (Date.now() - startedWatch < minutes * 60_000) {
    const t = await text(); const m = t.match(/(\d+) of \d+ investigations used/); used = m ? Number(m[1]) : 0
    const heading = (await programme.locator('.frontier-programme-heading').innerText().catch(() => '')).replace(/\s+/g, ' ')
    const line = `${m ? m[0] : 'no tally'} | ${heading.slice(0, 160)}`; if (line !== lastSeen) { log(`programme: ${line}`); lastSeen = line }
    if (used >= 5 && !/in progress/.test(t)) { log('terminal state detected'); break }
    await frame(); await page.waitForTimeout(8000)
  }
  const pause = page.getByRole('button', { name: /^Pause$/ }); if (await pause.count()) { await pause.click(); log('paused'); await page.waitForTimeout(2000) }
  await programme.scrollIntoViewIfNeeded().catch(() => {}); await page.waitForTimeout(2500)
  if (await map.count()) { await map.scrollIntoViewIfNeeded(); await page.waitForTimeout(4000) }
  const list = page.locator('summary', { hasText: 'Read the map as a list' }).first(); if (await list.count()) { await list.scrollIntoViewIfNeeded().catch(() => {}); await list.click().catch(() => {}); await page.waitForTimeout(1500) }
  await tour()
} else if (feature === 'studio') {
  await page.goto(`${origin}/studio`, { waitUntil: 'load' }); await page.waitForTimeout(2500)
  await page.getByRole('button', { name: custom || 'A submarine inspired by whales' }).click(); await page.waitForTimeout(2000)
  const invent = page.getByRole('button', { name: 'Invent it' }); await waitEnabled(invent); await page.waitForTimeout(800); await invent.click(); log('started')
  const progress = async () => (await page.getByLabel('Progress').innerText().catch(() => '')).replace(/\s+/g, ' ').trim()
  // Follow the design sheet: keep the progress rail in view, and move to each part of the sheet as it appears.
  const sheet = page.locator('.studio-sheet').first()
  const show = async (locator) => { if (await locator.count()) { await locator.first().scrollIntoViewIfNeeded().catch(() => {}); await page.evaluate(() => window.scrollBy({ top: -64, behavior: 'smooth' })) } }
  await page.waitForTimeout(3000); await show(sheet)
  let last = '', shown = ''
  while (Date.now() - started < minutes * 60_000) {
    const p = await progress(); if (p && p !== last) { log(`progress: ${p}`); last = p }
    const gallery = page.getByLabel('Gallery'); const line = (await gallery.innerText().catch(() => '')).replace(/\s+/g, ' ')
    const stage = /Stress test · (done|skipped|failed)/.test(p) ? 'gaps' : /Engineering · (done|skipped|failed)/.test(p) ? 'engineering' : /Invention · done/.test(p) ? 'art' : /Inspiration · (done|skipped|failed)/.test(p) ? 'ideas' : 'sheet'
    if (stage !== shown) { shown = stage; await show(stage === 'sheet' ? sheet : stage === 'art' ? page.locator('.studio-art') : page.locator(`#studio-${stage}`)) }
    else if (stage === 'art' || stage === 'gaps') await show(page.locator('.studio-art'))
    const count = line.match(/Gallery: (\d+) of (\d+)/); const painting = /Painting the gallery/.test(line) || (!!count && count[1] !== count[2])
    if (/Stress test · (done|skipped|failed)/.test(p) && !painting) { log('all stages and gallery done'); break }
    await page.waitForTimeout(10_000)
  }
  for (const target of [sheet, page.locator('.studio-art'), page.locator('#studio-ideas'), page.locator('#studio-engineering'), page.locator('#studio-gaps'), page.locator('#studio-refs')]) { await show(target); await page.waitForTimeout(3500) }
  await tour()
} else if (feature === 'surprise') {
  await page.goto(origin, { waitUntil: 'load' }); await page.waitForTimeout(2500)
  await page.getByRole('group', { name: 'Research mode' }).getByRole('button', { name: 'Surprise me', exact: true }).click(); await page.waitForTimeout(1500)
  const submit = page.locator('form button.primary-button[type="submit"]').first(); await waitEnabled(submit); await page.waitForTimeout(800); await submit.click(); log('submitted')
  await watch((t) => /· (Succeeded|Failed|Stopped)/.test(t) && !/· (Queued|In progress|Running)/.test(t), 8000)
  await page.waitForTimeout(4000); await tour()
} else if (feature === 'ask') {
  await page.goto(origin, { waitUntil: 'load' }); await page.waitForTimeout(2500)
  await page.getByRole('group', { name: 'Research mode' }).getByRole('button', { name: 'Ask', exact: true }).click(); await page.waitForTimeout(1200)
  const box = page.locator('textarea').first(); await box.pressSequentially(custom || 'Why do some materials become superconducting only under pressure?', { delay: 45 })
  const submit = page.locator('form button.primary-button[type="submit"]').first(); await waitEnabled(submit); await page.waitForTimeout(800); await submit.click(); log('submitted')
  await watch((t) => /(Conclusion|Recorded an answer|Investigation completed|succeeded)/i.test(t) && !/in progress|Searching|Reading/i.test(t.slice(-400)), 8000)
  await page.waitForTimeout(4000); await tour()
}
log(`done after ${Math.round((Date.now() - started) / 1000)}s`)
const video = page.video(); await context.close(); const file = await video.path(); const final = path.join(out, `${feature}${slug}.webm`); fs.renameSync(file, final); log(`video ${final} ${fs.statSync(final).size} bytes`)
await browser.close()
