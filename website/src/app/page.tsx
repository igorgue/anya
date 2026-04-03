import { type Metadata } from 'next'
import Link from 'next/link'
import clsx from 'clsx'
import {
  ArrowRight,
  Bot,
  Boxes,
  Cable,
  CheckCircle2,
  Code2,
  Database,
  Eye,
  GitBranch,
  PanelLeft,
  Play,
  Sparkles,
  Terminal,
  Workflow,
} from 'lucide-react'

import { Button } from '@/components/Button'
import { Card } from '@/components/Card'
import { Container } from '@/components/Container'

function HeroPattern() {
  return (
    <div
      aria-hidden="true"
      className="absolute inset-0 -z-10 overflow-hidden"
    >
      <div className="absolute left-1/2 top-0 h-[34rem] w-[34rem] -translate-x-1/2 rounded-full bg-violet-500/20 blur-3xl dark:bg-violet-500/12" />
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-violet-500/40 to-transparent" />
      <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(113,113,122,0.08)_1px,transparent_1px),linear-gradient(to_bottom,rgba(113,113,122,0.08)_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(circle_at_center,white,transparent_80%)] dark:bg-[linear-gradient(to_right,rgba(255,255,255,0.06)_1px,transparent_1px),linear-gradient(to_bottom,rgba(255,255,255,0.06)_1px,transparent_1px)]" />
    </div>
  )
}

function TerminalWindow() {
  const lines = [
    '$ nvim',
    ':Anya',
    'Ask Anya to inspect a codebase, refactor files, or run tools — all in Python.',
    'Anya writes Python code and streams the results instantly inside Neovim.',
    'Every action is Python — file reads, shell commands, web fetches, all executed as code.',
  ]

  return (
    <div className="relative mx-auto w-full max-w-5xl overflow-hidden rounded-3xl border border-zinc-200/80 bg-white/90 shadow-2xl shadow-violet-500/10 ring-1 ring-zinc-900/5 backdrop-blur dark:border-white/10 dark:bg-zinc-900/90 dark:ring-white/10">
      <div className="flex items-center gap-2 border-b border-zinc-200/80 px-4 py-3 dark:border-white/10">
        <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
        <div className="ml-4 rounded-full bg-zinc-100 px-3 py-1 text-xs text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
          anya.nvim
        </div>
      </div>
      <div className="grid gap-0 lg:grid-cols-[1.15fr_0.85fr]">
        <div className="border-b border-zinc-200/80 p-5 font-mono text-[13px] leading-6 text-zinc-700 dark:border-white/10 dark:text-zinc-300 lg:border-r lg:border-b-0">
          {lines.map((line, index) => (
            <div key={line} className={clsx(index > 1 && 'text-zinc-500 dark:text-zinc-400')}>
              {line}
            </div>
          ))}
          <div className="mt-4 rounded-2xl border border-violet-500/20 bg-violet-500/5 p-4 text-zinc-700 dark:text-zinc-300">
            <div className="text-violet-600 dark:text-violet-400">Anya</div>
            <div className="mt-2">
              Reading project structure, writing Python to trace handlers, and preparing a safe edit.
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {['fs.read_file()', 'shell.run()', 'mcp.call()', 'buffer.modify_file()'].map((label) => (
                <span
                  key={label}
                  className="rounded-full border border-violet-500/20 bg-white px-2.5 py-1 text-xs text-violet-700 dark:bg-zinc-900 dark:text-violet-300"
                >
                  {label}
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="bg-zinc-50/80 p-5 dark:bg-zinc-950/80">
          <div className="rounded-2xl border border-zinc-200/80 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-zinc-900">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-800 dark:text-zinc-100">
              <Workflow className="h-4 w-4 text-violet-500" />
              Live task list
            </div>
            <ul className="mt-4 space-y-3 text-sm text-zinc-600 dark:text-zinc-400">
              {[
                'Inspect current code and open buffers',
                'Run tools and gather project context',
                'Apply edits with confirmation-aware flows',
                'Verify changes and keep the conversation history',
              ].map((item, index) => (
                <li key={item} className="flex items-start gap-3">
                  <CheckCircle2
                    className={clsx(
                      'mt-0.5 h-4 w-4 flex-none',
                      index < 2 ? 'text-emerald-500' : 'text-violet-500',
                    )}
                  />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-4">
            {[
              ['Python only', 'Every action is written and run as Python code'],
              ['Streaming', 'Incremental output over ZeroMQ'],
              ['Extensible', 'Rich Python libs + MCP servers'],
              ['Editor-native', 'Buffers, folds, and confirmations'],
            ].map(([title, body]) => (
              <div
                key={title}
                className="rounded-2xl border border-zinc-200/80 bg-white p-4 dark:border-white/10 dark:bg-zinc-900"
              >
                <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">{title}</div>
                <div className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{body}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function SectionHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return (
    <div className="mx-auto max-w-3xl text-center">
      <p className="text-sm font-semibold tracking-[0.2em] text-violet-500 uppercase">{eyebrow}</p>
      <h2 className="mt-4 text-3xl font-semibold tracking-tight text-zinc-900 sm:text-4xl dark:text-white">{title}</h2>
      <p className="mt-4 text-base text-zinc-600 dark:text-zinc-400">{description}</p>
    </div>
  )
}

const features = [
  {
    title: 'Pure Python execution',
    description: 'Every action — reading files, running commands, editing code — is written and executed as Python. No shell hacks, no mixed languages.',
    icon: Database,
  },
  {
    title: 'Streaming inside Neovim',
    description: 'LLM output streams directly into buffers with folds, markers, and live UI updates.',
    icon: Eye,
  },
  {
    title: 'One tool, many libs',
    description: 'A single execute call runs Python with built-in libs for filesystem, shell, GitHub, web, MCP, and buffer operations.',
    icon: Code2,
  },
  {
    title: 'Safe edit flows',
    description: 'Tool edits can be reviewed, confirmed, rejected, and replayed with structured markers.',
    icon: GitBranch,
  },
  {
    title: 'Background daemon architecture',
    description: 'ZeroMQ + CBOR2 keep the UI responsive while the daemon handles agent execution and persistence.',
    icon: Cable,
  },
  {
    title: 'Built for real codebases',
    description: 'Inspect files, run commands, search code, consult MCP servers, and verify results from one session.',
    icon: Boxes,
  },
]

const pillars = [
  {
    title: 'Editor-native experience',
    body: 'Anya feels like part of Neovim: floating windows, prompt buffers, folds, extmarks, and multi-buffer conversations.',
    icon: PanelLeft,
  },
  {
    title: 'Composable tooling',
    body: 'Every action routes through a single Python execute call with high-level libs like fs, shell, web, search, mcp, and background.',
    icon: Terminal,
  },
  {
    title: 'Agent extensibility',
    body: 'Add new libs, skills, and MCP backends without exploding the tool surface or sacrificing reliability.',
    icon: Bot,
  },
]

const steps = [
  {
    title: 'Open Anya',
    body: 'Use :Anya to open a persistent chat UI backed by the daemon.',
  },
  {
    title: 'Ask for real work',
    body: 'Refactors, code reviews, debugging, docs lookup — Anya generates Python to do the work.',
  },
  {
    title: 'Watch it stream',
    body: 'Anya writes Python that calls its built-in libs, updates task lists, and edits files with confirmation-aware flows.',
  },
  {
    title: 'Resume anytime',
    body: 'State lives in SQLite and markers, so restarting Neovim does not reset your progress.',
  },
]

const technologies = [
  'Python 3.12+',
  'ZeroMQ + CBOR2',
  'SQLite persistence',
  'Neovim Lua bridge',
  'MCP protocol',
  'Built-in libs: fs, shell, web, search',
]

export const metadata: Metadata = {
  title: 'Anya',
  description: 'The AI coding agent that writes and executes only Python, inside Neovim.',
}

export default function Home() {
  return (
    <>
      <Container className="pt-16 sm:pt-24">
        <div className="relative overflow-hidden rounded-[2rem] border border-zinc-200/80 bg-linear-to-b from-white to-zinc-50 px-6 py-16 shadow-xl shadow-violet-500/5 sm:px-10 sm:py-20 dark:border-white/10 dark:from-zinc-900 dark:to-black">
          <HeroPattern />
          <div className="mx-auto max-w-4xl text-center">
            <div className="inline-flex items-center gap-2 rounded-full border border-violet-500/20 bg-violet-500/10 px-4 py-1.5 text-sm font-medium text-violet-700 dark:text-violet-300">
              <Sparkles className="h-4 w-4" />
              The Python-native AI agent for Neovim
            </div>
            <h1 className="mt-8 text-5xl font-semibold tracking-tight text-zinc-900 sm:text-7xl dark:text-white">
              One language. One tool. Anya writes and executes <span className="text-violet-500">Python</span> — nothing else.
            </h1>
            <p className="mx-auto mt-6 max-w-3xl text-lg text-zinc-600 sm:text-xl dark:text-zinc-400">
              Anya doesn't guess at shell commands or mix languages. It generates and runs pure Python — backed
              by a resilient daemon, persistent conversations, and powerful built-in libraries — streamed live into Neovim.
            </p>
            <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
              <Button href="https://github.com/igor47/anya" className="px-5 py-3 text-sm">
                Get started
                <ArrowRight className="h-4 w-4" />
              </Button>
              <Button
                href="https://github.com/igor47/anya#installation"
                variant="secondary"
                className="px-5 py-3 text-sm"
              >
                <Play className="h-4 w-4" />
                Installation
              </Button>
            </div>
            <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-zinc-500 dark:text-zinc-400">
              <span>Pure Python execution</span>
              <span>Persistent history</span>
              <span>Safe file edits</span>
              <span>MCP tools</span>
            </div>
          </div>
        </div>
      </Container>

      <Container className="mt-16 sm:mt-24">
        <TerminalWindow />
      </Container>

      <Container id="features" className="mt-24 sm:mt-32">
        <SectionHeading
          eyebrow="Why Anya"
          title="Everything you need for serious AI-assisted coding"
          description="Inspired by modern AI product landing pages, but grounded in Anya’s actual architecture: persistent state, daemon-backed execution, streaming UI, and composable tools."
        />
        <div className="mt-16 grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          {features.map((feature) => (
            <Card
              key={feature.title}
              className="rounded-3xl border border-zinc-200/80 bg-white p-8 shadow-sm shadow-zinc-900/5 dark:border-white/10 dark:bg-zinc-900"
            >
              <feature.icon className="h-8 w-8 text-violet-500" />
              <div className="mt-6">
                <Card.Title as="h3">{feature.title}</Card.Title>
              </div>
              <Card.Description>{feature.description}</Card.Description>
            </Card>
          ))}
        </div>
      </Container>

      <Container className="mt-24 sm:mt-32">
        <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-3xl border border-zinc-200/80 bg-zinc-50 p-8 dark:border-white/10 dark:bg-zinc-900/60">
            <SectionHeading
              eyebrow="Architecture"
              title="Python-first, daemon-backed"
              description="Anya separates editor UI from agent execution. The daemon runs pure Python — no shell injection, no language mixing — so work streams, persists, and recovers cleanly."
            />
            <div className="mt-10 space-y-6">
              {pillars.map((pillar) => (
                <div key={pillar.title} className="rounded-2xl border border-zinc-200/80 bg-white p-6 dark:border-white/10 dark:bg-black/20">
                  <div className="flex items-center gap-3 text-zinc-900 dark:text-white">
                    <pillar.icon className="h-5 w-5 text-violet-500" />
                    <h3 className="text-base font-semibold">{pillar.title}</h3>
                  </div>
                  <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">{pillar.body}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-3xl border border-violet-500/20 bg-violet-500/[0.07] p-8 dark:bg-violet-500/[0.08]">
            <p className="text-sm font-semibold tracking-[0.2em] text-violet-600 uppercase dark:text-violet-400">
              Core stack
            </p>
            <h3 className="mt-4 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-white">
              Every tool call is just Python.
            </h3>
            <p className="mt-4 text-base text-zinc-600 dark:text-zinc-400">
              Instead of juggling shell commands, curl invocations, or ad-hoc scripts, Anya generates Python code
              and executes it in a sandboxed environment. Read files with fs, run commands with shell, fetch the web — all Python.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              {technologies.map((tech) => (
                <span
                  key={tech}
                  className="rounded-full border border-violet-500/20 bg-white px-4 py-2 text-sm text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
                >
                  {tech}
                </span>
              ))}
            </div>
            <div className="mt-10 rounded-2xl border border-zinc-200/80 bg-white p-6 dark:border-white/10 dark:bg-zinc-900">
              <div className="text-sm font-semibold text-zinc-900 dark:text-white">Included in the platform</div>
              <ul className="mt-4 space-y-3 text-sm text-zinc-600 dark:text-zinc-400">
                {[
                  'Pure Python execution engine',
                  'SQLite-backed message persistence',
                  'Streaming chat and tool markers',
                  'MCP, shell, web, and filesystem Python libs',
                  'Skills and background job support',
                ].map((item) => (
                  <li key={item} className="flex items-start gap-3">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none text-violet-500" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </Container>

      <Container className="mt-24 sm:mt-32">
        <SectionHeading
          eyebrow="Workflow"
          title="From prompt to Python to verified changes"
          description="Anya turns your request into Python code, executes it, and streams the results back — no shell one-liners, no language roulette."
        />
        <div className="mt-16 grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          {steps.map((step, index) => (
            <div
              key={step.title}
              className="rounded-3xl border border-zinc-200/80 bg-white p-8 dark:border-white/10 dark:bg-zinc-900"
            >
              <div className="text-sm font-semibold text-violet-500">0{index + 1}</div>
              <h3 className="mt-4 text-lg font-semibold text-zinc-900 dark:text-white">{step.title}</h3>
              <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">{step.body}</p>
            </div>
          ))}
        </div>
      </Container>

      <Container className="mt-24 mb-24 sm:mt-32 sm:mb-32">
        <div className="rounded-[2rem] border border-zinc-200/80 bg-zinc-900 px-6 py-14 text-center sm:px-10 dark:border-white/10">
          <p className="text-sm font-semibold tracking-[0.2em] text-violet-400 uppercase">Open source</p>
          <h2 className="mt-4 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
            Ship code faster — with Python, inside Neovim.
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-base text-zinc-300">
            Explore the source, install Anya, and build your own workflow — every action is just Python.
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Button href="https://github.com/igor47/anya" className="bg-violet-500 px-5 py-3 hover:bg-violet-400 active:bg-violet-500">
              View on GitHub
            </Button>
            <Link
              href="https://github.com/igor47/anya#installation"
              className="text-sm font-medium text-zinc-300 transition hover:text-white"
            >
              Read installation guide <span aria-hidden="true">→</span>
            </Link>
          </div>
        </div>
      </Container>
    </>
  )
}
