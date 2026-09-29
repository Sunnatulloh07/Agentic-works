'use client';
import {useCallback, useEffect, useId, useRef, useState, type ReactNode} from 'react';
import {formatDate, formatRelative, friendlyError} from '../lib/format.mjs';

/** Shared pieces for every panel: a quiet loader, a confirm dialog and a time label.
 * Styling lives in app/globals.css; nothing here decides permissions. */

export const nowSeconds = () => Date.now() / 1000;

/** Loads on mount and on `refresh()`. Only the first load shows as loading, so a
 * background refresh never blanks a list the operator is reading. */
export function useLoader<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    try { setData(await load()); setError(''); }
    catch (e) { setError(friendlyError(e)); }
    finally { setLoading(false); }
  }, [load]);
  useEffect(() => { void refresh(); }, [refresh]);
  return {data, loading, error, setError, refresh};
}

/** Relative time in lists, the full Tashkent date on hover. */
export function When({at}: {at: number}) {
  return <time className="nowrap" dateTime={Number.isFinite(at) && at > 0 ? new Date(at * 1000).toISOString() : undefined}
    title={formatDate(at)}>{formatRelative(at, nowSeconds())}</time>;
}

/** Loading placeholder that reserves list space, announced once to screen readers. */
export function Skeleton({rows = 3}: {rows?: number}) {
  return <div className="skeleton" role="status" aria-busy="true">
    <span className="sr-only">Yuklanmoqda…</span>
    {Array.from({length: rows}, (_, i) => <span key={i} aria-hidden="true"/>)}
  </div>;
}

export function Alert({text}: {text: string}) {
  return text ? <p role="alert" className="notice error">{text}</p> : null;
}

/** A button that asks before it does something that cannot be taken back. */
export function ConfirmButton({label, title, message, confirmLabel, onConfirm, disabled, tone = 'danger', className}: {
  label: ReactNode; title: string; message: ReactNode; confirmLabel: string;
  onConfirm: () => unknown; disabled?: boolean; tone?: 'danger' | 'primary'; className?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  return <>
    <button type="button" className={className ?? (tone === 'danger' ? 'btn danger-outline' : 'btn')} disabled={disabled}
      onClick={() => dialog.current?.showModal()}>{label}</button>
    <dialog ref={dialog} className="confirm" aria-labelledby={titleId}>
      <h3 id={titleId}>{title}</h3>
      <div className="muted">{message}</div>
      <div className="actions">
        <button type="button" className={tone === 'danger' ? 'btn danger' : 'btn primary'} onClick={() => {
          dialog.current?.close(); void onConfirm();
        }}>{confirmLabel}</button>
        <button type="button" className="btn" autoFocus onClick={() => dialog.current?.close()}>Qaytish</button>
      </div>
    </dialog>
  </>;
}
