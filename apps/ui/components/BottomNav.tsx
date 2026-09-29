'use client';
import {useRef, type ReactNode} from 'react';
import {mobileNav} from '../lib/nav.mjs';

/** Phone navigation: four sections plus "Ko‘proq" in a thumb-reach bar. Shown only on
 * narrow screens (app/globals.css); desktop keeps the top tabs. "Ko‘proq" opens a modal
 * sheet (a native <dialog>: focus trap, Esc and inert background come for free) with
 * Sozlamalar and the advanced sections. Badges are supplied by the dashboard. */

const ICONS: Record<string, ReactNode> = {
  conversations: <path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>,
  approvals: <><circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/></>,
  orders: <><path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/></>,
  catalog: <path d="M9 3 3 6l2 4 2-1v12h10V9l2 1 2-4-6-3a3 3 0 0 1-6 0z"/>,
  more: <><circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/></>,
};
function Icon({id}: {id: string}) {
  return <svg className="ico" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{ICONS[id] ?? ICONS.more}</svg>;
}

export default function BottomNav({role, tab, onSelect, badge}: {
  role: string; tab: string; onSelect: (id: string) => void; badge: (id: string) => ReactNode;
}) {
  const sheet = useRef<HTMLDialogElement>(null);
  const {bottom, more} = mobileNav(role);
  const moreItems = [...more.main, ...more.advanced];
  const moreActive = moreItems.some(i => i.id === tab);
  const choose = (id: string) => { sheet.current?.close(); onSelect(id); };
  return <>
    <nav className="bottomnav" aria-label="Asosiy bo‘limlar">
      {bottom.map(i => <button key={i.id} type="button" className="bn-item"
        aria-current={tab === i.id ? 'page' : undefined} onClick={() => onSelect(i.id)}>
        <span className="bn-icon"><Icon id={i.id}/>{badge(i.id)}</span>
        <span className="bn-label">{i.label}</span>
      </button>)}
      <button type="button" className="bn-item" aria-haspopup="dialog" aria-current={moreActive ? 'page' : undefined}
        onClick={() => sheet.current?.showModal()}>
        <span className="bn-icon"><Icon id="more"/></span>
        <span className="bn-label">Ko‘proq</span>
      </button>
    </nav>
    <dialog ref={sheet} className="sheet" aria-label="Boshqa bo‘limlar"
      onClick={e => { if (e.target === sheet.current) sheet.current?.close(); }}>
      <div className="sheet-head"><h2>Ko‘proq</h2>
        <button type="button" className="btn small" onClick={() => sheet.current?.close()}>Yopish</button></div>
      {more.main.map(i => <button key={i.id} type="button" className="sheet-item" aria-current={tab === i.id ? 'page' : undefined}
        onClick={() => choose(i.id)}><Icon id={i.id}/>{i.label}</button>)}
      {more.advanced.length > 0 && <>
        <h3>Kengaytirilgan</h3>
        <p className="muted">Texnik bo‘limlar: agentlar, vazifalar, integratsiyalar va jurnallar.</p>
        {more.advanced.map(i => <button key={i.id} type="button" className="sheet-item" aria-current={tab === i.id ? 'page' : undefined}
          onClick={() => choose(i.id)}>{i.label}</button>)}
      </>}
    </dialog>
  </>;
}
