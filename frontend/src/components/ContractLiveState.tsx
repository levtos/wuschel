import { codeLabel, reasonLabel } from '@/i18n/codes';
import { t as uiText } from '@/i18n';
import { Link } from 'react-router';
import { LayoutPanel, PanelLayout } from './PanelLayout';
import { FieldStatusBadge, QualityBadge } from '@/components/status-parts';
import { Badge } from '@/components/ui/badge';
import { KeyValue, Notice, Panel, PanelHeader } from '@/components/ui/layout';
import { formatTime } from '@/lib/format';
import { displayValue } from '@/lib/status';
import type { Envelope } from '@/lib/types';
import { MachineRecovery } from './MachineRecovery';
import type { FieldSchema } from '@/features/authoring/model';

/** Live contract state exactly as the server reports it (status, quality, reasons, evidence). */
export function ContractLiveState({ envelope, stale, fields = {} }: { envelope: Envelope; stale: boolean; fields?: Record<string, FieldSchema> }) {
  return (
    <div className="flex flex-col gap-4">
      {stale && <Notice tone="warn" title={uiText("components.ContractLiveState.stand_moglicherweise_veraltet_42a607")}>{uiText("components.ContractLiveState.die_live_verbindung_ist_unterbrochen_oder_synchr_6baea2")}</Notice>}
      {!envelope.state_machine && Object.values(envelope.fields).some((field) => field.reasons.some((reason) => reason.code.startsWith('restore_context_'))) && <MachineRecovery contractId={envelope.contract_id} />}
      <PanelLayout view="contract.state"><LayoutPanel id="contract" title={uiText("components.ContractLiveState.contract_a6f5b0")}><Panel>
        <PanelHeader title={uiText("components.ContractLiveState.contract_a6f5b0")} aside={<><QualityBadge quality={envelope.contract_health} /><Badge tone={envelope.available ? 'ok' : 'warn'}>{envelope.available ? uiText("components.ContractLiveState.verfugbar_e5a644") : uiText("components.ContractLiveState.nicht_verfugbar_9a98b3")}</Badge></>} />
        <div className="grid gap-x-10 p-4 sm:grid-cols-2">
          <KeyValue items={[{ label: uiText("components.ContractLiveState.berechnet_65097b"), value: formatTime(envelope.computed_at) }, { label: uiText("components.ContractLiveState.veroffentlicht_16f085"), value: formatTime(envelope.published_at) }]} />
          <KeyValue items={[{ label: uiText("components.ContractLiveState.publication_15fc65"), value: envelope.publication_seq }, { label: uiText("components.ContractLiveState.revision_2e516d"), value: envelope.registry_revision }]} />
        </div>
      </Panel>
      </LayoutPanel><LayoutPanel id="fields" title={uiText("components.ContractLiveState.felder_b26f0b")}><Panel>
        <PanelHeader title={uiText("components.ContractLiveState.felder_b26f0b")} description={uiText("components.ContractLiveState.status_qualitat_und_grunde_stammen_unverandert_v_dd46c4")} />
        <div className="divide-y divide-line">
          {Object.entries(envelope.fields).map(([name, field]) => (
            <div key={name} className="grid gap-x-6 gap-y-1 px-4 py-3 text-sm md:grid-cols-[10rem_1fr]">
              <code className="font-medium">{name}</code>
              <div className="flex min-w-0 flex-col gap-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <FieldStatusBadge field={field} />
                  <QualityBadge quality={field.quality} />
                  <span className={field.status === 'valid' || field.status === 'held' ? 'font-mono' : 'text-muted'} data-testid={`value-${name}`}>{displayValue(field, typeof fields[name]?.unit === 'string' ? fields[name]?.unit as string : undefined, fields[name])}</span>
                  {fields[name]?.type === 'enum' && <span className="text-xs text-muted">{uiText('b3.rawValue')}: <code>{String(field.value ?? '—')}</code></span>}
                  {field.freshness && <Badge>{uiText("components.ContractLiveState.frische_b42690")}{codeLabel(field.freshness)}</Badge>}
                </div>
                <dl className="grid grid-cols-[max-content_1fr] gap-x-3 text-xs text-muted">
                  {field.measured_at && (<><dt>{uiText("components.ContractLiveState.gemessen_a9d62c")}</dt><dd>{formatTime(field.measured_at)}</dd></>)}
                  {field.since_at && (<><dt>{uiText("components.ContractLiveState.seit_b00312")}</dt><dd>{formatTime(field.since_at)}</dd></>)}
                  {field.held_until && (<><dt>{uiText("components.ContractLiveState.gehalten_bis_c18ae5")}</dt><dd>{formatTime(field.held_until)}</dd></>)}
                </dl>
                {field.attribution && <details><summary className="cursor-pointer text-xs text-muted">{uiText('alpha7.contract.advanced')}</summary><pre className="max-w-full overflow-auto text-xs">{JSON.stringify(field.attribution, null, 2)}</pre></details>}
                {field.reasons.length > 0 && (
                  <ul className="flex flex-col gap-0.5 text-xs">
                    {field.reasons.map((reason, i) => (
                      <li key={i} className="flex flex-wrap items-center gap-2"><Badge tone="warn">{reasonLabel(reason.code)}</Badge><span className="text-muted">{uiText("components.ContractLiveState.eingang_ea63c1")}{reason.input}{reason.source_id ? uiText("components.ContractLiveState.v0_8b43bb", { v0: (reason.source_id) }) : ''}{reason.detail ? uiText("components.ContractLiveState.v0_8b43bb", { v0: (reason.code === 'calendar_coverage_missing' ? reason.detail : codeLabel(reason.detail)) }) : ''} {uiText("components.ContractLiveState.seit_905c7f")}{formatTime(reason.since)}</span></li>
                    ))}
                  </ul>
                )}
                {field.evidence.length > 0 && <p className="text-xs text-muted">{uiText(field.evidence.length === 1 ? 'alpha9.evidence.one' : 'alpha9.evidence.many', { count: field.evidence.length })} · <Link className="text-fg hover:underline" to={`/settings/diagnostics?contract=${encodeURIComponent(envelope.contract_id)}`}>{uiText('alpha9.evidence.open')}</Link></p>}
              </div>
            </div>
          ))}
        </div>
      </Panel>
      </LayoutPanel>{envelope.state_machine && (
        <LayoutPanel id="machine" title={uiText("components.ContractLiveState.state_machine_801376")}>
        <Panel>
          <PanelHeader title={uiText("components.ContractLiveState.state_machine_801376")} />
          <details className="p-4"><summary className="cursor-pointer text-sm text-muted">{uiText('alpha7.contract.advanced')}</summary><pre className="max-h-72 overflow-auto py-3 text-xs">{JSON.stringify(envelope.state_machine, null, 2)}</pre></details>
        </Panel>
        </LayoutPanel>
      )}
      </PanelLayout>
    </div>
  );
}

