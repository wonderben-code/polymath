import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ApplicationRouter } from './ApplicationRouter'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ApplicationRouter />
  </StrictMode>,
)
