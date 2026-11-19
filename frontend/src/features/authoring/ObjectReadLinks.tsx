import { ArrowLeft } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { t } from '@/i18n';

export const sourceHref = (id: string) => `/sources/devices?source_id=${encodeURIComponent(id)}`;
export const bindingHref = (id: string) => `/sources/bindings?tab=bindings&binding_id=${encodeURIComponent(id)}`;
export const contractHref = (id: string) => `/contracts/view/${encodeURIComponent(id)}`;
export const definitionHref = (id: string, version: number) => `/templates/details/${encodeURIComponent(id)}/${version}`;

export function ObjectReadLink({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return <Link to={to} state={{ objectRead: true }} className={className ?? 'text-sm underline underline-offset-4'}>{children}</Link>;
}

export function ObjectReadBack({ fallback }: { fallback?: { to: string; label: string } }) {
  const { state } = useLocation();
  const navigate = useNavigate();
  const className = 'mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg';
  if (state?.objectRead === true) {
    return <button type="button" className={className} onClick={() => navigate(-1)}><ArrowLeft size={13} />{t('a2.read.back')}</button>;
  }
  return fallback ? <Link className={className} to={fallback.to}><ArrowLeft size={13} />{fallback.label}</Link> : null;
}
