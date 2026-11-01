/** The home page story under the composer: what Polymath does, what it is for, and why it matters. */
const STEPS = [
  { title: 'Reads across fields', line: 'Real papers from arXiv and OpenAlex, in any discipline.', colour: 'var(--violet)' },
  { title: 'Spots the same idea in different words', line: 'The maths of heat spreading through metal is the same maths that prices stock options.', colour: 'var(--blue)' },
  { title: 'Checks it against the evidence', line: 'Every connection comes with the exact passages behind it, and only if the papers back it up.', colour: 'var(--teal)' },
  { title: 'Builds bigger', line: 'Connections combine into deeper ones, and open problems are tackled step by step.', colour: 'var(--amber)' },
]

export function Story({ onFrontier, onStudio }: { onFrontier: () => void; onStudio?: () => void }) {
  return (
    <section className="story" aria-labelledby="story-title">
      <h2 id="story-title" className="story-heading">How it works</h2>
      <ol className="story-steps">
        {STEPS.map((step) => <li key={step.title}><i style={{ background: step.colour }} aria-hidden="true" /><strong>{step.title}</strong><span>{step.line}</span></li>)}
      </ol>
      <div className="story-columns">
        <div>
          <h2 className="story-heading">What you can use it for</h2>
          <ul className="story-uses">
            <li>Stuck on a problem? Find a field that may already have solved it.</li>
            <li>Find the gaps: what nobody has connected yet.</li>
            <li>Explore any idea across the whole of science.</li>
            <li>Invent something new from ideas that already work in nature and across science. {onStudio && <a href="/studio" onClick={(event) => { event.preventDefault(); onStudio() }}>Try Studio</a>}</li>
            <li>Take on an open problem, step by step. <a href="/frontier" onClick={(event) => { event.preventDefault(); onFrontier() }}>Try Frontier</a></li>
            <li>Write it up and publish it under your own name.</li>
          </ul>
        </div>
        <div>
          <h2 className="story-heading">Why it matters</h2>
          <p className="story-why">Some of the biggest leaps in science came from connecting fields. The mathematics of heat became the pricing of financial options. How ant colonies find food inspired how networks route data. Those leaps used to depend on rare people who happened to know both fields. Polymath makes looking for them something anyone can do, every day.</p>
        </div>
      </div>
    </section>
  )
}
