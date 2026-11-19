import { t } from '@/i18n';
import { ObjectReadLink } from '@/features/authoring/ObjectReadLinks';
import { Panel, PanelHeader } from '@/components/ui/layout';
import { formatTime } from '@/lib/format';
import type { Envelope, FieldValue, Registry } from '@/lib/types';
import { fieldLabel } from '@/i18n/codes';
import { contractFieldValue, groupedInputs, type ContractSource } from './presentation';
import { inputSchema } from './inputPresentation';
import { InputReadLinks } from './ContractReadLinks';

function minuteText(value: FieldValue, input: ContractSource) {
  const minutes = value.value;
  if (value.status !== 'valid' && value.status !== 'held') return undefined;
  if (!/(^|\.)minute_of_day$/.test(input.ref) && input.inputName !== 'minute_of_day') return undefined;
  if (typeof minutes !== 'number' || !Number.isFinite(minutes) || minutes < 0 || minutes >= 1440) return undefined;
  const hours = String(Math.floor(minutes / 60)).padStart(2, '0');
  const remainder = String(Math.floor(minutes % 60)).padStart(2, '0');
  return t('a2.read.clockTime', { time: `${hours}:${remainder}` });
}

export function InputOrigins({ envelope, config, sources, inputs, rule, children }: { envelope: Envelope; config: Registry | null; sources: ContractSource[]; inputs: Record<string, FieldValue>; rule: string; children?: React.ReactNode }) {
  const groups = groupedInputs(sources);
  const entry = (input: ContractSource) => {
    const field = inputs[input.inputName];
    return field ? <><span>{minuteText(field, input) ?? contractFieldValue(undefined, input.inputName, field, inputSchema(input.inputName, envelope, config))}</span><span className="ml-2 text-xs text-muted">{t(field.since_at ? 'b22.fact.since' : 'b22.inputs.stand', { time: formatTime(field.since_at || field.measured_at) })}</span>{(field.quality !== 'healthy' || !['valid', 'held'].includes(field.status)) && <span className="ml-2 text-xs text-warn">{t('b22.inputs.unreliable')}</span>}</> : <span className="text-muted">{t('b22.inputs.noProof')}</span>;
  };
  return <Panel><PanelHeader title={t('b22.inputs.title')} /><div className="space-y-3 p-5">
    {groups.length ? <ul className="space-y-3">{groups.map(group => <li key={`${group.kind}:${group.id}`}>
      <div className="flex flex-wrap items-baseline gap-x-2"><ObjectReadLink className="font-medium underline underline-offset-4" to={group.href}>{group.name}</ObjectReadLink>{group.members.length === 1 && entry(group.members[0]!)}</div>
      {group.members.length > 1 && <details><summary>{t('b22.inputs.count', { count: group.members.length })}</summary><ul className="ml-4 space-y-1">{group.members.map(member => <li key={member.ref}>{fieldLabel(member.inputName)} {entry(member)}</li>)}</ul></details>}
      {group.role && <p className="text-sm text-muted">{group.role}</p>}
      <InputReadLinks input={group} config={config} includeTarget={false} />
    </li>)}</ul> : <p>{t('alpha7.contract.noSource')}</p>}
    {rule && <p className="text-sm">{rule}</p>}{children}
  </div></Panel>;
}
