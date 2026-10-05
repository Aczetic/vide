import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'vide — pre-production',
  description: 'Stage 1 pre-production for AI drama and film',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
