import { ContainerInner, ContainerOuter } from '@/components/Container'

export function Footer() {
  return (
    <footer className="mt-32 flex-none">
      <ContainerOuter>
        <div className="border-t border-zinc-100 pt-10 pb-16 dark:border-zinc-700/40">
          <ContainerInner>
            <div className="flex flex-col items-center justify-between gap-6 sm:flex-row">
              <div className="flex flex-col items-center gap-1 sm:items-start">
                <p className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">
                  Anya
                </p>
                <p className="text-xs text-zinc-400 dark:text-zinc-500">
                  The AI coding agent for Neovim
                </p>
              </div>
              <div className="flex items-center gap-6 text-sm text-zinc-400 dark:text-zinc-500">
                <a
                  href="https://github.com/igor47/anya"
                  className="transition hover:text-violet-500 dark:hover:text-violet-400"
                >
                  GitHub
                </a>
                <a
                  href="https://github.com/igor47/anya#installation"
                  className="transition hover:text-violet-500 dark:hover:text-violet-400"
                >
                  Install
                </a>
                <a
                  href="#features"
                  className="transition hover:text-violet-500 dark:hover:text-violet-400"
                >
                  Features
                </a>
              </div>
            </div>
            <div className="mt-8 flex justify-center">
              <p className="text-xs text-zinc-400 dark:text-zinc-500">
                &copy; {new Date().getFullYear()} Anya. Open source under MIT
                License.
              </p>
            </div>
          </ContainerInner>
        </div>
      </ContainerOuter>
    </footer>
  )
}
