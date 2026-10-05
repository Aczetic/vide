import type { Config } from 'tailwindcss'
export default {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Neutral greys, no colour cast. People judge skin tone and grade
        // against this surround, so it must not tint what sits on it.
        ink:    { 900: '#0b0b0d', 800: '#121214', 700: '#17171a', 600: '#1e1e22', 500: '#2a2a30' },
        line:   { DEFAULT: '#2e2e35', soft: '#232329' },
        text:   { hi: '#ededf0', mid: '#a1a1aa', lo: '#6b6b76' },
        accent: { DEFAULT: '#c9a227', soft: '#3a3118' },
      },
      fontFamily: { sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'] },
    },
  },
  plugins: [],
} satisfies Config
