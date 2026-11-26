import { t as uiText } from '@/i18n';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { FileInput, Input, Textarea } from '@/components/ui/form';
import { Notice } from '@/components/ui/layout';
import { Field } from '@/components/ui/form';
import { describeError } from '@/lib/api';
import { clone } from '@/lib/json';
import { useActiveExport, useDraftMutations, useDrafts } from '@/lib/queries';
import type { Draft, Registry } from '@/lib/types';
import { emptyRegistry } from './model';
import { parseRegistryJson } from './importExport';
import { useWorkingDocument } from './WorkingDocument';
import { defaultDraftName } from './draftName';

type Origin = 'empty' | 'active' | 'import' | 'copy';

/**
 * Creating a draft never touches live state. A draft can start empty, from the
 * active registry, from an imported file/text, or as a copy of another draft.
 */
export function CreateDraftDialog({
  open,
  onOpenChange,
  copyOf,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  copyOf?: { config: Registry; name: string };
  onCreated?: (draft: Draft) => void;
}) {
  const { create } = useDraftMutations();
  const active = useActiveExport();
  const drafts = useDrafts('open', open);
  const atLimit = (drafts.data?.length ?? 0) >= 100;
  const doc = useWorkingDocument();
  const [origin, setOrigin] = useState<Origin>(copyOf ? 'copy' : 'empty');
  const [name, setName] = useState('');
  const [text, setText] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setOrigin(copyOf ? 'copy' : active.data ? 'active' : 'empty');
    setName(copyOf ? `${copyOf.name} (Kopie)` : defaultDraftName());
    setText('');
    setError('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = async () => {
    setError('');
    let config: Registry;
    let display = name.trim() || defaultDraftName();
    if (origin === 'empty') config = emptyRegistry();
    else if (origin === 'active') config = clone(active.data ?? emptyRegistry());
    else if (origin === 'copy' && copyOf) config = clone(copyOf.config);
    else {
      const parsed = parseRegistryJson(text);
      if (!parsed.ok) return setError(parsed.error);
      config = parsed.config;
      display ||= parsed.display_name ?? '';
    }
    try {
      const draft = await create.mutateAsync({ config, from_active: origin === 'active', ...(display ? { display_name: display } : {}) });
      // A just-created draft is opened without silently discarding edits of the draft that is open.
      doc.requestOpen(draft);
      onCreated?.(draft);
      onOpenChange(false);
    } catch (e) {
      setError(describeError(e));
    }
  };

  const options: { value: Origin; label: string; hint: string; disabled?: boolean }[] = [
    { value: 'empty', label: uiText("features.authoring.CreateDraftDialog.leere_registry_eb1ce9"), hint: uiText("features.authoring.CreateDraftDialog.von_grund_auf_neu_aufbauen_fingerprint_version_2_56e79f") },
    { value: 'active', label: uiText("features.authoring.CreateDraftDialog.kopie_der_aktiven_registry_728e01"), hint: active.data ? uiText("features.authoring.CreateDraftDialog.bestehende_konfiguration_weiterentwickeln_3e5423") : uiText("features.authoring.CreateDraftDialog.es_ist_keine_registry_aktiv_767a4d"), disabled: !active.data },
    { value: 'import', label: uiText("features.authoring.CreateDraftDialog.import_json_c4b92a"), hint: uiText("features.authoring.CreateDraftDialog.datei_oder_text_wird_wie_jeder_draft_validiert_u_33784f") },
    ...(copyOf ? [{ value: 'copy' as Origin, label: uiText("features.authoring.CreateDraftDialog.kopie_von_v0_8bb123", { v0: (copyOf.name) }), hint: uiText("features.authoring.CreateDraftDialog.duplikat_dieses_drafts_97f0da") }] : []),
  ];

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={uiText("features.authoring.CreateDraftDialog.neuer_draft_610fb2")}
      description={uiText("features.authoring.CreateDraftDialog.ein_draft_ist_niemals_live_erst_die_ausdrucklich_e2171f")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{uiText("features.authoring.CreateDraftDialog.abbrechen_f7ff11")}</Button>
           <Button variant="primary" disabled={atLimit || create.isPending || (origin === 'import' && !text.trim())} disabledReason={atLimit ? uiText('alpha7.import.limit') : create.isPending ? uiText('alpha7.copy.inProgress') : uiText('alpha7.import.empty')} onClick={submit}>
            {uiText("features.authoring.CreateDraftDialog.draft_anlegen_0355d8")}</Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {atLimit && <Notice tone="warn">{uiText("features.authoring.CreateDraftDialog.es_sind_bereits_100_entwurfe_vorhanden_entfernen_f89be6")}</Notice>}
        <Field label={uiText("features.authoring.CreateDraftDialog.name_optional_e09b37")}>{(p) => <Input {...p} value={name} maxLength={256} onChange={(e) => setName(e.target.value)} placeholder={uiText("features.authoring.CreateDraftDialog.z_b_licht_umbau_ed3d21")} />}</Field>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-xs font-medium text-muted">{uiText("features.authoring.CreateDraftDialog.ausgangspunkt_4c2d40")}</legend>
          {options.map((option) => (
            <label key={option.value} className={`flex cursor-pointer items-start gap-2.5 rounded-[var(--radius-md)] border p-3 text-sm ${origin === option.value ? 'border-faint bg-raised' : 'border-line'} ${option.disabled ? 'opacity-50' : ''}`}>
              <input type="radio" name="origin" aria-label={option.label} className="mt-1 accent-[var(--color-fg)]" disabled={option.disabled} checked={origin === option.value} onChange={() => setOrigin(option.value)} />
              <span>
                <span className="font-medium">{option.label}</span>
                <span className="block text-xs text-muted">{option.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {origin === 'import' && (
          <div className="flex flex-col gap-2">
            <Field label={uiText("features.authoring.CreateDraftDialog.registry_datei_json_be6006")}>
              {(p) => (
                <FileInput
                  {...p}
                  accept="application/json,.json"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file) setText(await file.text());
                  }}
                />
              )}
            </Field>
            <Field label={uiText("features.authoring.CreateDraftDialog.oder_json_einfugen_403a9d")}>{(p) => <Textarea {...p} rows={8} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />}</Field>
          </div>
        )}
        {error && <Notice tone="bad" title={uiText("features.authoring.CreateDraftDialog.draft_konnte_nicht_angelegt_werden_ef809e")}>{error}</Notice>}
      </div>
    </Dialog>
  );
}
