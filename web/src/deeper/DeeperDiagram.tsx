/** Three quiet levels: connections, the connection between them, and one beneath that. */
export function DeeperDiagram() {
  return (
    <svg className="deeper-diagram" viewBox="0 0 240 92" aria-hidden="true">
      <path className="deeper-arc" d="M 20 78 Q 50 52 80 78" />
      <path className="deeper-arc" d="M 160 78 Q 190 52 220 78" />
      <path className="deeper-rise" d="M 50 64 Q 80 30 120 36 Q 160 30 190 64" />
      <path className="deeper-rise is-second" d="M 120 36 L 120 14" />
      {[20, 80, 160, 220].map((x) => <circle key={x} className="deeper-field" cx={x} cy="78" r="3.2" />)}
      <circle className="deeper-knot" cx="50" cy="64" r="3.6" />
      <circle className="deeper-knot" cx="190" cy="64" r="3.6" />
      <circle className="deeper-knot is-deeper" cx="120" cy="36" r="4.4" />
      <circle className="deeper-knot is-deepest" cx="120" cy="12" r="5.2" />
    </svg>
  )
}

/** The calm invitation shown beneath a saved connection. */
export function GoDeeper({ onChoose }: { onChoose: () => void }) {
  return (
    <aside className="go-deeper" aria-labelledby="go-deeper-title">
      <DeeperDiagram />
      <div>
        <h3 id="go-deeper-title">Go deeper</h3>
        <p>This connection can become part of a bigger one. Pair it with other saved connections and Polymath looks for the structure they all share, and again from there.</p>
        <button type="button" onClick={onChoose}>Choose connections to combine</button>
      </div>
    </aside>
  )
}
