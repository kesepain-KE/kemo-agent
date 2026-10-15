import { useState } from 'react'
import { getLogoUrl } from '../api/client'

export function KemoLogo() {
  const [fallback, setFallback] = useState(false)
  return <img
    className="kemo-logo"
    src={fallback ? `${import.meta.env.BASE_URL}kemo-agent.jpg` : getLogoUrl()}
    alt="kemo-agent Logo"
    onError={() => setFallback(true)}
  />
}
