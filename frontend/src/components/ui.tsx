import { AlertTriangle, CheckCircle2, LoaderCircle, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ApiError } from "../api/client";

export function Button({
  children,
  variant = "primary",
  loading = false,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; loading?: boolean }) {
  return (
    <button {...props} className={`button button--${variant} ${className}`.trim()} disabled={loading || props.disabled}>
      {loading ? <LoaderCircle aria-hidden="true" className="spin" size={16} /> : null}
      {children}
    </button>
  );
}

export function Panel({ children, className = "", title, action }: { children: ReactNode; className?: string; title?: string; action?: ReactNode }) {
  return (
    <section className={`panel ${className}`}>
      {title ? <header className="panel__header"><h2>{title}</h2>{action}</header> : null}
      {children}
    </section>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "success" | "warning" | "danger" | "info" | "neutral" }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return <div className="skeleton" aria-label="Loading">{Array.from({ length: lines }, (_, index) => <span key={index} />)}</div>;
}

export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return <div className="empty-state"><CheckCircle2 aria-hidden="true" /><h3>{title}</h3><p>{children}</p></div>;
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  const status = error instanceof ApiError && error.status ? `HTTP ${error.status}` : null;
  return (
    <div className="error-state" role="alert">
      <AlertTriangle aria-hidden="true" />
      <div><strong>{status ? `${status}: ` : ""}{message}</strong><p>No sensitive request data was logged.</p></div>
      {retry ? <Button variant="secondary" onClick={retry}><RefreshCw size={15} />Retry</Button> : null}
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  danger = false,
  pending = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  pending?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal?.();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  if (!open) return null;
  return (
    <dialog ref={ref} className="dialog" aria-labelledby="confirm-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <div className="dialog__head"><h2 id="confirm-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close confirmation"><X /></button></div>
      <div className="dialog__body">{children}</div>
      <div className="dialog__actions"><Button variant="ghost" onClick={onClose} disabled={pending}>Cancel</Button><Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={pending}>{confirmLabel}</Button></div>
    </dialog>
  );
}
