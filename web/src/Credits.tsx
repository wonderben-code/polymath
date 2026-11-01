/** Who Polymath is built with, where its knowledge comes from, and where it publishes. */
const GROUPS = [
  { label: 'Built with', names: [{ name: 'Meta Muse Spark', note: 'the research intelligence' }, { name: 'Meta Global AI Developer Hackathon', note: 'created for' }, { name: 'Cerebral Valley', note: 'hackathon host' }] },
  { label: 'Reads from', names: [{ name: 'arXiv', note: 'open preprints and full text' }, { name: 'OpenAlex', note: 'the open catalogue of world research' }] },
  { label: 'Publishes to', names: [{ name: 'Zenodo', note: 'open repository operated by CERN' }] },
]

export function Credits() {
  return (
    <section className="credits" aria-label="Built with, reads from and publishes to">
      {GROUPS.map((group) => <div key={group.label} className="credits-group">
        <h2>{group.label}</h2>
        <ul>{group.names.map((item) => <li key={item.name}><strong>{item.name}</strong><span>{item.note}</span></li>)}</ul>
      </div>)}
    </section>
  )
}
