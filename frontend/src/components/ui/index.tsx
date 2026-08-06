import clsx from 'clsx'
import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { useEffect } from 'react'

/* Hand-written shadcn-shaped primitives. They read the same tokens the CLI
 * components would, so swapping to the real thing later is a file move. */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'destructive'
  size?: 'sm' | 'md'
}

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  ...props
}: ButtonProps) {
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
        'disabled:pointer-events-none disabled:opacity-50',
        size === 'sm' ? 'h-8 px-3 text-sm' : 'h-9 px-4 text-sm',
        variant === 'primary' && 'bg-primary text-primary-foreground hover:opacity-90',
        variant === 'secondary' &&
          'border border-border bg-card text-foreground hover:bg-muted',
        variant === 'ghost' && 'text-muted-foreground hover:bg-muted hover:text-foreground',
        variant === 'destructive' &&
          'bg-destructive text-destructive-foreground hover:opacity-90',
        className,
      )}
      {...props}
    />
  )
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx('rounded-card border border-border bg-card', className)}
      {...props}
    />
  )
}

export function Badge({
  tone = 'neutral',
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: 'neutral' | 'primary' | 'warning' | 'destructive' | 'success'
}) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        tone === 'neutral' && 'bg-muted text-muted-foreground',
        tone === 'primary' && 'bg-accent text-accent-foreground',
        tone === 'warning' && 'bg-warning-muted text-warning-foreground',
        tone === 'destructive' && 'bg-destructive-muted text-destructive',
        tone === 'success' && 'bg-success-muted text-success',
        className,
      )}
      {...props}
    />
  )
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={clsx(
        'h-9 w-full rounded-lg border border-input bg-background px-3 text-sm',
        'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
      {...props}
    />
  )
}

export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  label?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={clsx(
        'relative h-6 w-11 shrink-0 rounded-full transition-colors duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        checked ? 'bg-primary' : 'bg-border',
      )}
    >
      <span
        className={clsx(
          'absolute top-0.5 h-5 w-5 rounded-full bg-background transition-transform duration-150',
          checked ? 'translate-x-5.5' : 'translate-x-0.5',
        )}
      />
    </button>
  )
}

/** Drawer + scrim. Replaces the mockups' hand-rolled transform pattern. */
export function Sheet({
  open,
  onClose,
  title,
  children,
  width = 'w-[32rem]',
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  width?: string
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-foreground/25"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        role="dialog"
        aria-modal="true"
        className={clsx(
          'relative flex h-full flex-col border-l border-border bg-card shadow-xl animate-fade-up',
          width,
          'max-w-full',
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0">{title}</div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            Close
          </Button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </aside>
    </div>
  )
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: string; label: string; count?: number }[]
  active: string
  onChange: (id: string) => void
}) {
  return (
    <div className="flex gap-1 border-b border-border" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={active === tab.id}
          onClick={() => onChange(tab.id)}
          className={clsx(
            '-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150',
            active === tab.id
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground',
          )}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="ml-1.5 text-xs text-muted-foreground">{tab.count}</span>
          )}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="px-4 py-10 text-center">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-muted-foreground">{label}...</div>
  )
}
