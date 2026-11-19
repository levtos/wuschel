import { t as uiText } from '@/i18n';
import { Link } from 'react-router';
import { Badge } from '@/components/ui/badge';
import { Collapsible } from '@/components/ui/controls';
import { Notice } from '@/components/ui/layout';
import { fieldLabel } from '@/i18n/codes';
import { useActiveRevision } from '@/lib/queries';
import { useOptionalWorkingDocument } from '@/features/authoring/WorkingDocument';
import { asArray, str } from '@/features/authoring/model';
import { isObject, type JsonObject } from '@/lib/json';
import type { Diff } from '@/lib/types';
import { changedObjectCount } from '@/lib/diff';
import { countSentence } from '@/lib/valueText';
import { durationText } from '@/lib/duration';

const GROUP_LABELS: Record<string, string> = {
  freshness_templates: uiText('b4.title'),
  dependencies: uiText("features.registry.DiffView.dependencies_2e41b1"),
  sources: uiText("features.registry.DiffView.sources_caf85b"),
  bindings: uiText("features.registry.DiffView.bindings_7697ab"),
  catalogs: uiText("features.registry.DiffView.kataloge_daf282"),
  contracts: uiText("features.registry.DiffView.contracts_0488ac"),
  definitions: uiText("features.registry.DiffView.definitionen_871a91"),
};

function Chips({ items, tone = 'neutral', link, label = id => id }: { items: string[]; tone?: 'neutral' | 'ok' | 'bad' | 'warn' | 'accent'; link?: boolean; label?: (id: string) => string }) {
  if (items.length === 0) return <span className="text-xs text-faint">–</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {items.map((item) => (link ? <Link key={item} to={`/contracts/view/${encodeURIComponent(item)}`}>{label(item)}</Link> : <Badge key={item} tone={tone}>{label(item)}</Badge>))}
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-1.5 sm:grid-cols-[13rem_1fr]">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="min-w-0 text-sm">{children}</dd>
    </div>
  );
}

/**
 * Static semantic diff and impact analysis as delivered by the server. It never
 * predicts values (`predicts_values: false`); it lists what an activation would
 * change and which contexts might be re-initialized.
 */
export function DiffView({ diff, against, config }: { diff: Diff; against?: string; config: JsonObject }) {
  const doc = useOptionalWorkingDocument();
  const active = useActiveRevision();
  const label = (group: string, id: string) => {
    const key = group === 'contracts' ? 'contract_id' : group === 'sources' ? 'source_id' : group === 'bindings' ? 'binding_id' : group === 'catalogs' ? 'catalog_id' : group === 'definitions' ? 'type_id' : 'dependency_id';
    const item = [config, doc?.config, active.data?.config].flatMap(candidate => asArray(candidate?.[group])).filter(isObject).find(row => row[key] === id);
    const name = str(item?.display_name);
    return name && name !== id ? `${name} (${id})` : id;
  };
  const contractName = (id: string) => label('contracts', id);
  const sourceName = (id: string) => label('sources', id);
  const groups = Object.entries(diff.objects).filter(([, g]) => g.added.length + g.removed.length + g.changed.length > 0);
  const nothing = groups.length === 0 && diff.affected_contracts.length === 0 && !diff.fingerprint_version.context_migration;
  const added = new Set([...diff.added, ...Object.values(diff.objects).flatMap((group) => group.added)]);
  const restarted = diff.possibly_invalidated_contexts.filter((id) => !added.has(id));
  const migrating = diff.fingerprint_version.context_migration && diff.fingerprint_version.before !== null;
  const targetContracts = asArray(config.contracts).length;
  const affectedContracts = diff.snapshot.affected_contracts.length;
  let snapshotText = uiText('features.registry.DiffView.unverandert_85e10f');
  if (diff.snapshot.revision_changes) {
    if (targetContracts === 0) {
      snapshotText = uiText('alpha9.configuration.noContracts');
    } else if (affectedContracts === 0) {
      snapshotText = uiText(targetContracts === 1 ? 'alpha9.configuration.contractRetained' : 'alpha9.configuration.contractsRetained', { n: targetContracts });
    } else {
      snapshotText = uiText(affectedContracts === 1 ? 'alpha7.versions.affectedOne' : 'alpha7.versions.affected', { n: affectedContracts });
    }
  }
  return (
    <div className="flex flex-col gap-4">
      <p aria-label={uiText('alpha7.draft.changeCount')} className="text-sm font-medium">{countSentence(changedObjectCount(diff), 'changes')}</p>
      <p className="text-xs text-muted">
        {uiText('alpha7.diff.explanation')} {against && uiText('alpha7.diff.comparison', { against })}</p>
      {nothing && <Notice tone="info" title={uiText("features.registry.DiffView.keine_anderung_gegenuber_der_aktiven_revision_b4a476")} />}
      {diff.fingerprint_version.before === null && !nothing && <p className="text-sm text-muted">{uiText('alpha7.versions.firstStart')}</p>}
      {migrating && restarted.length > 0 && (
        <Notice tone="warn" title={uiText('alpha7.diff.reinitialize')}>{uiText('alpha7.diff.reinitializeHelp')}</Notice>
      )}
      <dl className="divide-y divide-line rounded-[var(--radius-md)] border border-line px-3">
        {diff.freshness_effects?.map(effect => <Row key={effect.source_id} label={sourceName(effect.source_id)}>{uiText('b4.beforeAfter', { before: effect.before?.mode === 'event_stateful' ? uiText('b4.noInterval') : durationText(Number(effect.before?.interval_s)), after: effect.after?.mode === 'event_stateful' ? uiText('b4.noInterval') : durationText(Number(effect.after?.interval_s)) })}</Row>)}
        <Row label={uiText("features.registry.DiffView.contracts_hinzugefugt_aa1b10")}><Chips items={diff.added} link label={contractName} /></Row>
        <Row label={uiText("features.registry.DiffView.contracts_entfernt_5c5965")}><Chips items={diff.removed} label={contractName} /></Row>
        <Row label={uiText("features.registry.DiffView.contracts_geandert_59d44d")}><Chips items={diff.changed} link label={contractName} /></Row>
        <Row label={uiText("features.registry.DiffView.direkt_betroffen_832e51")}><Chips items={diff.directly_affected_contracts} link label={contractName} /></Row>
        <Row label={uiText("features.registry.DiffView.transitiv_betroffen_ef01a8")}><Chips items={diff.transitively_affected_contracts} link label={contractName} /></Row>
        {restarted.length > 0 && <Row label={uiText("features.registry.DiffView.mogliche_neuinitialisierung_2361a0")}><Chips items={restarted} tone="warn" link label={contractName} /></Row>}
        <Row label={uiText('features.registry.DiffView.snapshot_6ad27b')}>{snapshotText}</Row>
      </dl>
      {groups.map(([name, group]) => (
        <Collapsible key={name} title={uiText("features.registry.DiffView.v0_v1_neu_v2_geandert_v3_entfernt_80a78f", { v0: (GROUP_LABELS[name] ?? name), v1: (group.added.length), v2: (group.changed.length), v3: (group.removed.length) })} defaultOpen={groups.length <= 2}>
          <dl>
            <Row label={uiText("features.registry.DiffView.hinzugefugt_1625f5")}><Chips items={group.added} label={id => label(name, id)} /></Row>
            <Row label={uiText("features.registry.DiffView.entfernt_d0124f")}><Chips items={group.removed} label={id => label(name, id)} /></Row>
            <Row label={uiText("features.registry.DiffView.geandert_d63c93")}><Chips items={group.changed} label={id => label(name, id)} /></Row>
          </dl>
          {group.changed.map((key) => (
            <div key={key} className="mt-2 rounded-[var(--radius-sm)] border border-line p-2">
              <p className="font-mono text-xs font-medium">{label(name, key)}</p>
              <p className="mt-1 text-xs text-muted">{uiText("features.registry.DiffView.felder_ad2dff")}{(group.fields[key] ?? []).map(fieldLabel).join(', ') || '–'}</p>
              <ul className="mt-1 max-h-40 overflow-auto font-mono text-[11px] text-muted">
                {(group.field_paths[key] ?? []).map((path) => <li key={path}>{path.split('/').map(fieldLabel).join(' / ')}</li>)}
              </ul>
              {group.field_paths_truncated.includes(key) && <p className="mt-1 text-xs text-warn">{uiText("features.registry.DiffView.pfadliste_gekurzt_server_limit_1_024_344b24")}</p>}
            </div>
          ))}
        </Collapsible>
      ))}
      <Collapsible title={uiText('alpha7.versions.technicalDiff')}>
        <dl>
          <Row label={uiText('features.registry.DraftPage.fingerprint_version_e46d72')}>{diff.fingerprint_version.before ?? '–'} → {diff.fingerprint_version.after}</Row>
          <Row label={uiText('features.registry.DiffView.fristen_bleiben_erhalten_bcbfe1')}>{diff.restore.retains_absolute_deadlines_if_compatible ? uiText('features.registry.DiffView.ja_bei_kompatiblem_fingerprint_219c13') : uiText('features.registry.DiffView.nein_4d03db')}</Row>
          <Row label={uiText("features.registry.DiffView.geanderte_fingerprints_eb5466")}><Chips items={diff.fingerprint_changes} /></Row>
          <Row label={uiText("features.registry.DiffView.identitatswechsel_quellen_9211e3")}><Chips items={diff.source_identity_changes} /></Row>
          <Row label={uiText("features.registry.DiffView.identitatswechsel_bindings_3f8a12")}><Chips items={diff.binding_identity_changes} /></Row>
        </dl>
      </Collapsible>
      {Object.keys(diff.affected_fields).length > 0 && (
        <Collapsible title={uiText("features.registry.DiffView.betroffene_ausgabefelder_konservativ_1da6f9")}>
          <ul className="space-y-1 text-sm">
            {Object.entries(diff.affected_fields).map(([id, fields]) => (
              <li key={id} className="flex flex-wrap items-center gap-2"><Link to={`/contracts/view/${encodeURIComponent(id)}`} className="font-mono text-xs text-fg hover:underline">{contractName(id)}</Link>{fields.map((f) => <Badge key={f}>{fieldLabel(f)}</Badge>)}</li>
            ))}
          </ul>
        </Collapsible>
      )}
    </div>
  );
}
