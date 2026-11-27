import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field, Input, Select } from '@/components/ui/form';
import { Notice } from '@/components/ui/layout';
import { useOptionalWorkingDocument } from '@/features/authoring/WorkingDocument';
import { IssueList } from '@/features/registry/IssueList';
import { api, describeError } from '@/lib/api';
import type { Draft, Registry, ValidationResult } from '@/lib/types';
import { uuid } from '@/lib/uuid';
import { t } from '@/i18n';
import { catalogLabel } from './catalogLabels';
import { classLabel, ClassField, type ClassDefinition, type ClassValue } from './ClassFields';
import type { Catalog } from './CatalogPage';

type Assignment = { class_key: string; default?: ClassValue; title_catalog_mapping?: Record<string, ClassValue> };
type Change = {
  catalog_id: string; entries: number; before: Record<string, number>; variants: number; tombstones: number;
  sightings: number; class_values: Record<string, Record<string, ClassValue>>; removed_values?: Record<string, number>;
};
type Projection = { contract_id: string; entity_id: string; unique_id: string; old_fields: string[]; fields: string[]; primary_field: string };
type MovePlan = {
  package: Record<string, unknown>; resolutions: Record<string, string>; projections?: Projection[];
  blockers?: { catalog_id: string; values: Record<string, number> }[];
  preview: {
    plan_digest: string; can_stage?: boolean; unresolved: string[]; catalog_migrations?: Change[];
    configuration_issues?: ValidationResult['issues'];
  };
};
type UndoPlan = {
  expected_active_revision: number; migration_revision: number; target_revision: number; plan_digest: string;
  catalogs: { catalog_id: string; care_revisions: number; changed_sightings: number }[];
};

const seed: ClassDefinition = { class_key: '', display_name: '', kind: 'boolean', default: false };
function ClassEditor({ row, existing, onChange }: { row: ClassDefinition; existing: boolean; onChange: (row: ClassDefinition) => void }) {
  return <fieldset className="space-y-2 rounded-lg border border-line p-3">
    <Field label={t('b31.sensor.key')}>{props => <Input {...props} readOnly={existing} value={row.class_key} onChange={event => onChange({ ...row, class_key: event.target.value })} />}</Field>
    <Field label={t('b31.sensor.name')}>{props => <Input {...props} value={row.display_name} onChange={event => onChange({ ...row, display_name: event.target.value })} />}</Field>
    <Field label={t('b31.classes.kind')}>{props => <Select {...props} disabled={existing} value={row.kind} onChange={event => {
      const kind = event.target.value as ClassDefinition['kind'];
      const base = { class_key: row.class_key, display_name: row.display_name, kind };
      onChange(kind === 'integer' ? { ...base, unit: '', minimum: 0, maximum: 100, default: 0 } :
        kind === 'enum' ? { ...base, default: '', values: {} } : { ...base, default: false });
    }}>{(['enum', 'integer', 'boolean'] as const).map(kind => <option key={kind} value={kind}>{t(`b31.classes.${kind}`)}</option>)}</Select>}</Field>
    {row.kind === 'integer' && <div className="grid gap-2 sm:grid-cols-3">
      <Field label={t('b31.sensor.unit')}>{props => <Input {...props} value={row.unit ?? ''} onChange={event => onChange({ ...row, unit: event.target.value })} />}</Field>
      {(['minimum', 'maximum'] as const).map(key => <Field key={key} label={t(`b31.classes.${key}`)}>{props =>
        <Input {...props} type="number" step={1} value={row[key] ?? ''} onChange={event => onChange({ ...row, [key]: event.target.value === '' ? null : event.target.valueAsNumber })} />
      }</Field>)}
    </div>}
    {row.kind === 'enum' && <EnumValues value={row.values ?? {}} onChange={values => onChange({ ...row, values })} />}
    <ClassField definition={row} label={t('b31.classes.default')} value={row.default} onChange={value => {
      if (value !== undefined) onChange({ ...row, default: value });
    }} />
  </fieldset>;
}

function EnumValues({ value, onChange }: {
  value: NonNullable<ClassDefinition['values']>;
  onChange: (value: NonNullable<ClassDefinition['values']>) => void;
}) {
  const [key, setKey] = useState('');
  return <>
    {Object.entries(value).map(([code, item]) => <div className="grid gap-2 sm:grid-cols-3" key={code}>
      <p>{code}</p>
      <Field label={t('b31.sensor.name')}>{props => <Input {...props} value={item.display_name} onChange={event => onChange({ ...value, [code]: { ...item, display_name: event.target.value } })} />}</Field>
      <Field label={t('b31.classes.meaning')}>{props => <Input {...props} value={item.description} onChange={event => onChange({ ...value, [code]: { ...item, description: event.target.value } })} />}</Field>
    </div>)}
    <div className="flex items-end gap-2"><Field label={t('b31.classes.newValueKey')}>{props => <Input {...props} value={key} onChange={event => setKey(event.target.value)} />}</Field>
      <Button disabled={!/^[a-z][a-z0-9_]{0,63}$/.test(key) || !!value[key]} onClick={() => {
        onChange({ ...value, [key]: { display_name: '', description: '' } }); setKey('');
      }}>{t('b31.classes.addValue')}</Button></div>
  </>;
}

function CatalogAssignments({ catalog, classes, assignments, onChange }: {
  catalog: Catalog; classes: ClassDefinition[]; assignments: Assignment[]; onChange: (value: Assignment[]) => void;
}) {
  const [adding, setAdding] = useState('');
  const available = classes.filter(row => row.class_key && !assignments.some(item => item.class_key === row.class_key));
  const edit = (key: string, patch: Partial<Assignment>) => onChange(assignments.map(row => row.class_key === key ? { ...row, ...patch } : row));
  return <details className="space-y-2 rounded-lg border border-line p-3"><summary>{catalogLabel(catalog)}</summary>
    {assignments.map(assignment => {
      const definition = classes.find(row => row.class_key === assignment.class_key);
      if (!definition) return null;
      return <section className="space-y-2" key={assignment.class_key}>
        <h4>{definition.display_name}</h4>
        <ClassField definition={definition} label={t('b31.classes.catalogDefault')} value={assignment.default} onChange={value => edit(assignment.class_key, { default: value })} />
        {assignment.class_key === 'light_look' && ['media.ps5', 'media.pc'].includes(catalog.catalog_id) && <div className="grid gap-2 sm:grid-cols-2">
          {[0, 1, 2, 3].map(number => <ClassField key={number} definition={definition} label={t('b31.classes.tcNumber', { number })} value={assignment.title_catalog_mapping?.[String(number)]}
            onChange={value => {
              const mapping = { ...assignment.title_catalog_mapping };
              if (value === undefined) delete mapping[String(number)];
              else mapping[String(number)] = value;
              edit(assignment.class_key, { title_catalog_mapping: mapping });
            }} />)}
        </div>}
        <Button disabled={assignments.length <= 1} onClick={() => onChange(assignments.filter(row => row !== assignment))}>{t('b31.classes.removeAssignment')}</Button>
      </section>;
    })}
    <div className="flex items-end gap-2"><Field label={t('b31.classes.assign')} >{props => <Select {...props} value={adding} onChange={event => setAdding(event.target.value)}>
      <option value="">{t('b4.choose')}</option>{available.map(row => <option key={row.class_key} value={row.class_key}>{row.display_name}</option>)}
    </Select>}</Field><Button disabled={!available.some(row => row.class_key === adding)} onClick={() => {
      onChange([...assignments, { class_key: adding }]); setAdding('');
    }}>{t('b31.classes.addAssignment')}</Button></div>
  </details>;
}

export function CatalogClasses({ config, revision, catalogs }: { config?: Registry; revision: number; catalogs: Catalog[] }) {
  const queryClient = useQueryClient();
  const working = useOptionalWorkingDocument();
  const [open, setOpen] = useState(false);
  const [classes, setClasses] = useState<ClassDefinition[]>([]);
  const [assignments, setAssignments] = useState<Record<string, Assignment[]>>({});
  const [dirty, setDirty] = useState(false);
  const [plan, setPlan] = useState<MovePlan | null>(null);
  const [undo, setUndo] = useState<UndoPlan | null>(null);
  const [receipt, setReceipt] = useState('');
  const [requestId, setRequestId] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [baseRevision, setBaseRevision] = useState(0);
  const version5 = config?.config_schema_version === 5;
  const legacy = catalogs.some(row => ['media.homepods', 'media.ps5', 'media.pc', 'media.appletv', 'media.appletv_titles', 'media.stash', 'media.tv_device', 'media.tv_activity'].includes(row.catalog_id) && !row.class_definitions?.length);
  const typed = catalogs.filter(row => row.class_definitions?.length);
  const reverse = useQuery({
    queryKey: ['catalog-classes-rollback', revision], enabled: open && !!typed.length,
    queryFn: () => api<UndoPlan | null>('catalogs/classes/rollback'), retry: false,
  });
  const saved = (config?.catalog_classes ?? []) as unknown as ClassDefinition[];
  const start = () => {
    setClasses(saved); setPlan(null); setUndo(null); setReceipt(''); setError(''); setDirty(false);
    setBaseRevision(revision);
    setAssignments(Object.fromEntries((Array.isArray(config?.catalogs) ? config.catalogs : [])
      .flatMap(value => value && typeof value === 'object' && 'class_assignments' in value &&
        typeof value.catalog_id === 'string' ? [[value.catalog_id, value.class_assignments as unknown as Assignment[]]] : [])));
    setOpen(true);
  };
  const current = baseRevision === revision;
  const execute = async (action: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await action(); }
    catch (failure) { setError(describeError(failure)); }
    finally { setBusy(false); }
  };
  const refresh = () => {
    setDirty(true); setPlan(null); setUndo(null); setError('');
  };
  const check = (move: boolean) => execute(async () => {
    const response = await api<MovePlan>('catalogs/classes/migration', {
      method: 'POST', body: { expected_active_revision: revision, ...(move ? {} : { catalog_classes: classes, assignments }) },
    });
    setPlan(response); setUndo(null); setRequestId(uuid());
  });
  const stage = () => execute(async () => {
    if (!plan || !current) return;
    const draft = await api<Draft>('portable/stage', {
      method: 'POST', interactive: true, body: {
        package: plan.package, resolutions: plan.resolutions,
        plan_digest: plan.preview.plan_digest, request_id: requestId,
      },
    });
    await queryClient.invalidateQueries({ queryKey: ['registry', 'drafts'] });
    working?.requestOpen(draft);
    setReceipt(draft.draft_id); setPlan(null); setDirty(false);
  });
  const rollback = () => execute(async () => {
    if (!undo || !current) return;
    await api('registry/rollback', { method: 'POST', interactive: true, body: {
      expected_active_revision: undo.expected_active_revision, revision: undo.target_revision,
      catalog_migration_revision: undo.migration_revision, plan_digest: undo.plan_digest,
      request_id: requestId,
    } });
    setOpen(false);
    await queryClient.invalidateQueries();
  });
  return <>
    <Button disabled={!revision} onClick={start}>{t('b31.classes.manage')}</Button>
    <Dialog open={open} size="lg" title={t('b31.classes.manage')} onOpenChange={shown => !shown && !busy && setOpen(false)}
      footer={<>
        <Button disabled={busy} onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
        {undo ? <Button variant="primary" disabled={busy || !current} onClick={() => void rollback()}>{t('b31.classes.confirmUndo')}</Button> :
          <Button variant="primary" disabled={busy || !plan || plan.preview.can_stage === false || !!plan.preview.unresolved.length || !current} onClick={() => void stage()}>{t('b31.classes.stage')}</Button>}
      </>}>
      <p>{t('b31.classes.description')}</p>
      {!current && <Notice tone="warn">{t('a2.care.selectionChanged')} <Button onClick={start}>{t('a2.care.reload')}</Button></Notice>}
      {error && <Notice tone="bad">{error}</Notice>}
      {receipt && <Notice tone="ok"><Link to={`/changes/drafts/${receipt}`}>{t('b31.classes.openDraft')}</Link></Notice>}
      {legacy && <Button disabled={busy || !current} onClick={() => void check(true)}>{t('b31.classes.previewMove')}</Button>}
      {version5 && <>
        <div className="space-y-2">
          {classes.map((row, index) => <ClassEditor key={index} row={row} existing={index < saved.length} onChange={value => {
            setClasses(classes.map((old, i) => index === i ? value : old)); refresh();
          }} />)}
        </div>
        <Button disabled={busy} onClick={() => { setClasses([...classes, seed]); refresh(); }}>{t('b31.classes.addClass')}</Button>
        {typed.map(catalog => <CatalogAssignments key={catalog.catalog_id} catalog={catalog} classes={classes}
          assignments={assignments[catalog.catalog_id] ?? []} onChange={next => {
            setAssignments({ ...assignments, [catalog.catalog_id]: next }); refresh();
          }} />)}
        <Button disabled={busy || !current || !dirty} onClick={() => void check(false)}>{t('alpha10.catalog.preview')}</Button>
      </>}
      {reverse.data && !undo && <Button disabled={busy || !current} onClick={() => {
        setUndo(reverse.data); setPlan(null); setRequestId(uuid());
      }}>{t('b31.classes.undo')}</Button>}
      {plan?.preview.configuration_issues && <IssueList issues={plan.preview.configuration_issues} config={config ?? null} />}
      {!!plan?.blockers?.length && <Notice tone="bad">
        <p>{t('codes.catalog_migration_unmapped')}</p>
        <ul>{plan.blockers.flatMap(row => Object.entries(row.values).map(([value, count]) =>
          <li key={`${row.catalog_id}:${value}`}>{row.catalog_id} · {value} · {count}</li>
        ))}</ul>
      </Notice>}
      {plan?.preview.can_stage !== false && plan && <>
        <p>{t('b31.classes.moveExplained')}</p>
        <ul>{plan.preview.catalog_migrations?.map(row => <li key={row.catalog_id}>
          <h4>{catalogs.find(catalog => catalog.catalog_id === row.catalog_id) ? catalogLabel(catalogs.find(catalog => catalog.catalog_id === row.catalog_id)!) : row.catalog_id}</h4>
          <p>{t('b31.classes.moveCount', { entries: row.entries, variants: row.variants, sightings: row.sightings, tombstones: row.tombstones })}</p>
          <ul>{Object.entries(row.before).map(([value, count]) => <li key={value}>
            {value === 'unassigned' ? t('a2.care.noStoredClass') : value} · {count}
            {row.class_values[value] && ` → ${Object.entries(row.class_values[value]).map(([key, replacement]) => {
              const item = classes.find(definition => definition.class_key === key);
              return item ? `${item.display_name} ${classLabel(item, replacement)}` : `${key} ${String(replacement)}`;
            }).join(' · ')}`}
          </li>)}</ul>
          {Object.entries(row.removed_values ?? {}).map(([name, count]) => <p key={name}>{t('b31.classes.removed', { name, count })}</p>)}
        </li>)}</ul>
        {plan.projections?.length ? <details><summary>{t('b31.classes.projections')}</summary><ul>{plan.projections.map(row => <li key={row.contract_id}>
          <div>{row.entity_id} · {row.unique_id}</div><div>{row.primary_field} · {row.old_fields.join(', ')} → {row.fields.join(', ')}</div>
        </li>)}</ul></details> : null}
      </>}
      {undo && <Notice tone="warn"><p>{t('b31.classes.undoInfo', { revision: undo.target_revision })}</p><ul>
        {undo.catalogs.map(row => <li key={row.catalog_id}>{t('b31.classes.undoImpact', { catalog: row.catalog_id, revisions: row.care_revisions, sightings: row.changed_sightings })}</li>)}
      </ul></Notice>}
    </Dialog>
  </>;
}
