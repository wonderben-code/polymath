import { useId } from 'react'
import { RIGOURS, setRigour, useRigour } from './rigour'

/** How rigorous the literature must be for the next investigation. */
export function RigourBar() {
  const uid = useId()
  const rigour = useRigour()
  const chosen = RIGOURS.find((item) => item.id === rigour) ?? RIGOURS[1]
  return (
    <div className="rigour-bar" role="group" aria-labelledby={`${uid}-label`}>
      <span id={`${uid}-label`} className="rigour-label">Evidence bar</span>
      <div className="rigour-options">
        {RIGOURS.map((item) => <button key={item.id} type="button" aria-pressed={rigour === item.id} onClick={() => setRigour(item.id)}>{item.label}</button>)}
      </div>
      <p className="rigour-line" aria-live="polite">{chosen.line}</p>
    </div>
  )
}
