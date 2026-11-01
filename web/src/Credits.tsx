/** Who Polymath is built with, where its knowledge comes from, and where it publishes. */
interface Credit { name: string; note: string; logo?: string; mark?: boolean; height?: number }

// Official logo files, served from /logos. A mark is shown beside the name; a full
// logo replaces it. OpenAlex is named in text until its logo file is added.
const GROUPS: { label: string; names: Credit[] }[] = [
  { label: 'Built with', names: [
    { name: 'Meta Muse models', note: 'frontier intelligence, Muse Spark today', logo: '/logos/meta.svg', height: 18 },
    { name: 'Meta Global AI Developer Hackathon', note: 'created for' },
    { name: 'Cerebral Valley', note: 'hackathon host', logo: '/logos/cerebral-valley.svg', mark: true },
  ] },
  { label: 'Reads from', names: [
    { name: 'arXiv', note: 'open preprints and full text', logo: '/logos/arxiv.svg', height: 30 },
    { name: 'OpenAlex', note: 'the open catalogue of world research' },
  ] },
  { label: 'Publishes to', names: [
    { name: 'Zenodo', note: 'open repository operated by CERN', logo: '/logos/zenodo.svg', height: 22 },
  ] },
]

function Name({ item }: { item: Credit }) {
  if (item.logo && !item.mark) return <img className="credits-logo" src={item.logo} alt={item.name} height={item.height ?? 22} />
  return <strong>{item.logo && <img className="credits-mark" src={item.logo} alt="" height={18} width={18} />}{item.name}</strong>
}

export function Credits() {
  return (
    <section className="credits" aria-label="Built with, reads from and publishes to">
      {GROUPS.map((group) => <div key={group.label} className="credits-group">
        <h2>{group.label}</h2>
        <ul>{group.names.map((item) => <li key={item.name}><Name item={item} /><span>{item.logo && !item.mark ? `${item.name}: ${item.note}` : item.note}</span></li>)}</ul>
      </div>)}
    </section>
  )
}
