import { Link } from 'react-router';
import { t } from '@/i18n';
import { Panel, PanelHeader } from '@/components/ui/layout';
import { formatTime } from '@/lib/format';
import type { Envelope, FieldValue, Registry } from '@/lib/types';
import { fieldLabel } from '@/i18n/codes';
import { contractFieldValue, groupedInputs, type ContractSource } from './presentation';
import { inputSchema } from './inputPresentation';

export function InputOrigins({ envelope, config, sources, inputs, why, rule, children }: { envelope: Envelope; config: Registry | null; sources: ContractSource[]; inputs: Record<string, FieldValue>; why: string; rule: string; children?: React.ReactNode }) {
  const groups = groupedInputs(sources);
  const entry = (input: ContractSource) => {
    const field = inputs[input.inputName];
    return field ? <><span>{contractFieldValue(undefined, input.inputName, field, inputSchema(input.inputName, envelope, config))}</span><span className="ml-2 text-xs text-muted">{t(field.since_at ? 'b22.fact.since' : 'b22.inputs.stand', { time: formatTime(field.since_at || field.measured_at) })}</span>{(field.quality !== 'healthy' || !['valid', 'held'].includes(field.status)) && <span className="ml-2 text-xs text-warn">{t('b22.inputs.unreliable')}</span>}</> : <span className="text-muted">{t('b22.inputs.noProof')}</span>;
  };
  return <Panel><PanelHeader title={t('b22.inputs.title')} /><div className="space-y-3 p-5">
    {groups.length ? <ul className="space-y-3">{groups.map(group => <li key={`${group.kind}:${group.id}`}><Link className="font-medium hover:text-fg" to={group.href}>{group.name}</Link>{group.members.length === 1 ? <> – {entry(group.members[0]!)}</> : <details><summary>{t('b22.inputs.count', { count: group.members.length })}</summary><ul className="ml-4 space-y-1">{group.members.map(member => <li key={member.ref}>{fieldLabel(member.inputName)}: {entry(member)}</li>)}</ul></details>}{group.role && <p className="text-sm text-muted">{group.role}</p>}</li>)}</ul> : <p>{t('alpha7.contract.noSource')}</p>}
    {rule && <p className="text-sm">{rule}</p>}<p className="text-sm">{why}</p>{children}
  </div></Panel>;
}
