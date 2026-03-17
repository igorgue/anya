import { type Metadata } from 'next'

import { Providers } from '@/app/providers'
import { Layout } from '@/components/Layout'

import '@/styles/tailwind.css'

export const metadata: Metadata = {
  title: {
    template: '%s - Anya',
    default: 'Anya - The AI Coding Agent for Neovim',
  },
  description:
    'Anya is a modern, persistent Neovim AI coding agent. Streaming LLM output, multi-buffer UI, intelligent code-aware tools, and full conversation persistence across restarts.',
  icons: {
    icon: '/favicon.ico',
  },
  openGraph: {
    title: 'Anya - The AI Coding Agent for Neovim',
    description:
      'A modern, persistent Neovim AI coding agent with streaming output, intelligent tools, and conversation persistence.',
    url: 'https://igor-elysia.github.io/anya',
    siteName: 'Anya',
    locale: 'en_US',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Anya - The AI Coding Agent for Neovim',
    description:
      'A modern, persistent Neovim AI coding agent with streaming output, intelligent tools, and conversation persistence.',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <body className="flex h-full bg-zinc-50 dark:bg-black">
        <Providers>
          <div className="flex w-full">
            <Layout>{children}</Layout>
          </div>
        </Providers>
      </body>
    </html>
  )
}
