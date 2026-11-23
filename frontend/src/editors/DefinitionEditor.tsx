import { OutputOriginsEditor } from './ValueOriginEditor';
import { t as uiText } from '@/i18n';
import { codeLabel } from '@/i18n/codes';
import { Lock, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Checkbox, Tabs, Tip } from '@/components/ui/controls';
import { Collapsible } from '@/components/ui/controls';
import { Input, Select, Textarea } from '@/components/ui/form';
import { Notice } from '@/components/ui/layout';
import {
  asArray,
  asObject,
  newFieldSchema,
  newRule,
  referencedNames,
  renameKey,
  renameReferences,
  ruleContext,
  str,
  uniqueName,
  withMinimumLanguage,
  type FieldSchema,
  type Rule,
} from '@/features/authoring/model';
import type { Capabilities } from '@/lib/capabilities';
import { join, type Json, type JsonObject } from '@/lib/json';
import { AddButton, Fld, IconButton, IssueCount, IssuesAt, JsonValueEditor, RowActions, SectionTitle } from './common';
import { FieldSchemaEditor } from './FieldSchemaEditor';
import { MachineEditor } from './MachineEditor';
import { MachineReading } from './MachineReading';
import { RuleReading } from './RuleReading';
import { operatorDefaults, RuleEditor, RuleEnvProvider } from './RuleEditor';
import { SchemaEditor } from './SchemaEditor';
import { Extras } from './Extras';

const TYPE_ID = /^[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)+$/;

/**
 * Complete graphical editor of one declarative definition. Everything the
 * server accepts in `definitions[]` is reachable here; the "JSON" tab is an
 * optional expert view of the very same data, never a prerequisite.
 */
export function DefinitionEditor({
  value,
  onChange,
  pointer,
  caps,
  locked: semanticLocked,
  readOnly = false,
  lockReason,
  initialTab,
}: {
  value: JsonObject;
  onChange: (next: JsonObject) => void;
  pointer: string;
  caps: Capabilities | undefined;
  locked: boolean;
  readOnly?: boolean;
  lockReason?: string;
  initialTab?: string;
}) {
  const locked = semanticLocked || readOnly;
  const defaults = operatorDefaults(caps);
  const ctx = ruleContext(value, false);
  const inputs = asArray(value.inputs).map(asObject);
  const rules = asObject(value.rules);
  const outputs = asObject(value.outputs);
  const inputUse = referencedNames(value, 'input');
  const ruleUse = referencedNames(value, 'ref');
  const set = (patch: Record<string, Json | undefined>) => {
    const next: JsonObject = { ...value };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete next[k];
      else next[k] = v;
    }
    onChange(withMinimumLanguage(next));
  };

  const outputsTab = (
    <SchemaEditor
      value={asObject(value.schema)}
      pointer={join(pointer, 'schema')}
      onChange={(schema) => set({ schema })}
      onAddField={(name) => {
        const fields = asObject(asObject(value.schema).fields);
        onChange({
          ...value,
          schema: { ...asObject(value.schema), fields: { ...fields, [name]: newFieldSchema('bool') }, available_projection: asArray(asObject(value.schema).available_projection) },
          outputs: { ...outputs, [name]: newRule('input', ctx, defaults) },
        });
      }}
      onRemoveField={(name) => {
        const schema = asObject(value.schema);
        onChange({
          ...value,
          schema: { ...schema, fields: Object.fromEntries(Object.entries(asObject(schema.fields)).filter(([k]) => k !== name)), available_projection: asArray(schema.available_projection).filter((p) => p !== name) },
          outputs: Object.fromEntries(Object.entries(outputs).filter(([k]) => k !== name)),
        });
      }}
      onRenameField={(from, to) => {
        const schema = asObject(value.schema);
        onChange({
          ...value,
          schema: { ...schema, fields: renameKey(asObject(schema.fields), from, to), available_projection: asArray(schema.available_projection).map((p) => (p === from ? to : p)) },
          outputs: renameKey(outputs, from, to),
        });
      }}
      extra={(name) => (
        <div className="flex flex-col gap-2 border-t border-line pt-3">
          <SectionTitle>{uiText("editors.DefinitionEditor.regel_fur_69dffb")}{name}“</SectionTitle>
          <RuleReading value={outputs[name]} definition={value} output={name} />
          <RuleEditor
            value={(outputs[name] ?? newRule('input', ctx, defaults)) as Rule}
            pointer={join(pointer, 'outputs', name)}
            onChange={(rule) => set({ outputs: { ...outputs, [name]: rule } })}
          />
        </div>
      )}
    />
  );

  const items = [
    {
      value: 'general',
      label: uiText("editors.DefinitionEditor.allgemein_24eddd"),
      content: (
        <div className="flex max-w-3xl flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Fld label={uiText("editors.DefinitionEditor.typ_id_b7db41")} pointer={join(pointer, 'type_id')} hint={TYPE_ID.test(str(value.type_id)) ? uiText("editors.DefinitionEditor.synthetischer_test_namensraum_8ac977") : uiText("editors.DefinitionEditor.muss_mit_test_beginnen_01622a")}>
              {(p) => <Input {...p} disabled={locked} value={str(value.type_id)} onChange={(e) => set({ type_id: e.target.value })} />}
            </Fld>
            <Fld label={uiText("editors.DefinitionEditor.version_dd1679")} pointer={join(pointer, 'type_version')} hint={uiText("editors.DefinitionEditor.logikanderungen_neue_version_28d5cf")}>
              {(p) => <Input {...p} disabled={locked} type="number" min={1} step={1} value={Number(value.type_version ?? 1)} onChange={(e) => set({ type_version: Math.max(1, Math.trunc(Number(e.target.value) || 1)) })} />}
            </Fld>
            <Fld label={uiText("editors.DefinitionEditor.sprachversion_4adccf")} pointer={join(pointer, 'language_version')}>
              {(p) => <Select {...p} disabled={locked} value={String(value.language_version ?? 1)} onChange={event => set({ language_version: Number(event.target.value) })}><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option></Select>}
            </Fld>
          </div>
          <Fld label={uiText("editors.DefinitionEditor.anzeigename_redaktionell_44433b")} pointer={join(pointer, 'display_name')} hint={uiText("editors.DefinitionEditor.redaktionelle_felder_andern_die_semantik_nicht_u_20b198")}>
            {(p) => <Input {...p} disabled={readOnly} value={str(value.display_name)} onChange={(e) => set({ display_name: e.target.value || undefined })} />}
          </Fld>
          <Fld label={uiText("editors.DefinitionEditor.beschreibung_redaktionell_d5700f")} pointer={join(pointer, 'description')}>
            {(p) => <Textarea {...p} disabled={readOnly} rows={3} className="font-sans text-sm" value={str(value.description)} onChange={(e) => set({ description: e.target.value || undefined })} />}
          </Fld>
          <Fld label={uiText('alpha8.lifecycle.title')} pointer={join(pointer, 'lifecycle')}>
            {props => <Select {...props} disabled={readOnly} value={str(value.lifecycle) || 'active'} onChange={event => set({ lifecycle: event.target.value })}>{['active', 'deprecated', 'retired'].map(state => <option key={state} value={state}>{codeLabel(state)}</option>)}</Select>}
          </Fld>
          <Fld label={uiText('alpha8.lifecycle.note')} pointer={join(pointer, 'reactivation_note')} hint={uiText('alpha8.lifecycle.noteHint')}>
            {props => <Input {...props} disabled={readOnly} value={str(value.reactivation_note)} onChange={event => set({ reactivation_note: event.target.value || undefined })} />}
          </Fld>
          <fieldset disabled={locked} className="flex flex-col gap-2">
            <SectionTitle>{uiText("editors.DefinitionEditor.refresh_eingange_f326cb")}</SectionTitle>
            <p className="text-xs text-muted">{uiText("editors.DefinitionEditor.deklarierte_ha_eingange_die_nach_einem_commit_ne_ab47d2")}</p>
            {inputs.length === 0 && <p className="text-xs text-faint">{uiText("editors.DefinitionEditor.noch_keine_eingange_63d0b8")}</p>}
            {inputs.map((input) => {
              const name = str(input.name);
              const selected = asArray(value.refresh_inputs).map(String);
              return (
                <Checkbox key={name} label={<code>{name}</code>} checked={selected.includes(name)} onCheckedChange={(on) => { const next = on ? [...selected, name] : selected.filter((n) => n !== name); set({ refresh_inputs: next.length ? next : undefined }); }} />
              );
            })}
            <IssuesAt pointer={join(pointer, 'refresh_inputs')} />
          </fieldset>
          <fieldset disabled={locked}>
            <OutputOriginsEditor value={value} onChange={next => onChange(withMinimumLanguage(next))} />
        <Extras def="DeclarativeDefinition" value={value} pointer={pointer} onChange={onChange} />
          </fieldset>
        </div>
      ),
    },
    {
      value: 'inputs',
      label: (
        <span className="flex items-center gap-2">
          {uiText("editors.DefinitionEditor.eingange_aaae23")}<Badge>{inputs.length}</Badge>
          <IssueCount pointer={join(pointer, 'inputs')} />
        </span>
      ),
      content: (
        <fieldset disabled={locked} className="flex max-w-3xl flex-col gap-3">
          <SectionTitle aside={<AddButton onClick={() => set({ inputs: [...inputs, { name: uniqueName('eingang', inputs.map((i) => str(i.name))), schema: newFieldSchema('bool') }] })}>{uiText("editors.DefinitionEditor.eingang_d8af5e")}</AddButton>}>{uiText("editors.DefinitionEditor.typisierte_eingange_1ca67c")}</SectionTitle>
          <IssuesAt pointer={join(pointer, 'inputs')} />
          {inputs.length === 0 && <Notice tone="info">{uiText("editors.DefinitionEditor.ohne_eingange_liefert_die_definition_nur_konstan_25c31a")}</Notice>}
          {inputs.map((input, i) => {
            const name = str(input.name);
            const ip = join(pointer, 'inputs', i);
            const used = inputUse.get(name) ?? 0;
            return (
              <Collapsible
                key={i}
                defaultOpen
                title={<span className="flex items-center gap-2"><code>{name}</code><Badge>{codeLabel(str(asObject(input.schema).type))}</Badge>{used > 0 && <span className="text-xs text-faint">{used}{uiText("editors.DefinitionEditor.verwendet_cf0908")}</span>}</span>}
                aside={
                  <>
                    <IssueCount pointer={ip} />
                    <RowActions
                      index={i}
                      count={inputs.length}
                      removeDisabled={used > 0}
                      onMove={(from, to) => {
                        const copy = inputs.slice();
                        const [moved] = copy.splice(from, 1);
                        copy.splice(to, 0, moved as JsonObject);
                        set({ inputs: copy });
                      }}
                      onRemove={() => set({ inputs: inputs.filter((_, j) => j !== i), ...(value.refresh_inputs !== undefined ? { refresh_inputs: asArray(value.refresh_inputs).filter((n) => n !== name) } : {}) })}
                    />
                  </>
                }
              >
                <div className="flex flex-col gap-3">
                  <Fld label={uiText("editors.DefinitionEditor.name_dcd1d5")} pointer={join(ip, 'name')} hint={used > 0 ? uiText("editors.DefinitionEditor.umbenennen_aktualisiert_alle_verweise_in_dieser__746da1") : undefined}>
                    {(p) => (
                      <Input
                        {...p}
                        key={name}
                        defaultValue={name}
                        onBlur={(e) => {
                          const to = e.target.value.trim();
                          if (!to || to === name || inputs.some((x) => str(x.name) === to)) return void (e.target.value = name);
                          const renamed = renameReferences(value, 'input', name, to);
                          onChange({ ...renamed, inputs: asArray(renamed.inputs).map((x, j) => (j === i ? { ...asObject(x), name: to } : x)), ...(renamed.refresh_inputs !== undefined ? { refresh_inputs: asArray(renamed.refresh_inputs).map((n) => (n === name ? to : n)) } : {}) });
                        }}
                      />
                    )}
                  </Fld>
                  <FieldSchemaEditor pointer={join(ip, 'schema')} value={asObject(input.schema) as FieldSchema} onChange={(schema) => set({ inputs: inputs.map((x, j) => (j === i ? { ...x, schema } : x)) })} />
                  <Extras def="TypedInput" value={input} pointer={ip} onChange={(next) => set({ inputs: inputs.map((x, j) => (j === i ? next : x)) })} />
                </div>
              </Collapsible>
            );
          })}
        </fieldset>
      ),
    },
    {
      value: 'outputs',
      label: (
        <span className="flex items-center gap-2">
          {uiText("editors.DefinitionEditor.ausgaben_c20af5")}<Badge>{Object.keys(asObject(asObject(value.schema).fields)).length}</Badge>
          <IssueCount pointer={join(pointer, 'outputs')} />
          <IssueCount pointer={join(pointer, 'schema')} />
        </span>
      ),
      content: <fieldset disabled={locked} className="min-w-0 max-w-4xl">{outputsTab}</fieldset>,
    },
    {
      value: 'rules',
      label: (
        <span className="flex items-center gap-2">
          {uiText("editors.DefinitionEditor.benannte_regeln_2d1254")}<Badge>{Object.keys(rules).length}</Badge>
          <IssueCount pointer={join(pointer, 'rules')} />
        </span>
      ),
      content: (
        <fieldset disabled={locked} className="flex max-w-4xl flex-col gap-3">
          <SectionTitle aside={<AddButton onClick={() => set({ rules: { ...rules, [uniqueName('regel', Object.keys(rules))]: newRule('input', ctx, defaults) } })}>{uiText("editors.DefinitionEditor.regel_4b09cd")}</AddButton>}>{uiText("editors.DefinitionEditor.interne_zwischenausdrucke_55311a")}</SectionTitle>
          <p className="text-xs text-muted">{uiText("editors.DefinitionEditor.benannte_regeln_werden_pro_auswertung_einmal_ber_2306fc")}</p>
          <IssuesAt pointer={join(pointer, 'rules')} />
          {Object.keys(rules).length === 0 && <Notice tone="info">{uiText("editors.DefinitionEditor.noch_keine_benannten_regeln_161f59")}</Notice>}
          {Object.entries(rules).map(([name, rule]) => {
            const used = ruleUse.get(name) ?? 0;
            return (
              <Collapsible
                key={name}
                defaultOpen
                title={<span className="flex items-center gap-2"><code>{name}</code>{used > 0 && <span className="text-xs text-faint">{used}{uiText("editors.DefinitionEditor.verwendet_cf0908")}</span>}</span>}
                aside={
                  <>
                    <IssueCount pointer={join(pointer, 'rules', name)} />
                    <IconButton label={used > 0 ? uiText("editors.DefinitionEditor.v0_wird_verwendet_e5d823", { v0: (name) }) : uiText("editors.DefinitionEditor.regel_v0_entfernen_df07a9", { v0: (name) })} tone="danger" disabled={used > 0} onClick={() => set({ rules: Object.fromEntries(Object.entries(rules).filter(([k]) => k !== name)) })}>
                      <Trash2 size={14} />
                    </IconButton>
                  </>
                }
              >
                <div className="flex flex-col gap-3">
                  <Fld label={uiText("editors.DefinitionEditor.name_dcd1d5")} pointer={join(pointer, 'rules', name)}>
                    {(p) => (
                      <Input
                        {...p}
                        key={name}
                        defaultValue={name}
                        onBlur={(e) => {
                          const to = e.target.value.trim();
                          if (!to || to === name || to in rules) return void (e.target.value = name);
                          const renamed = renameReferences(value, 'ref', name, to);
                          onChange({ ...renamed, rules: renameKey(asObject(renamed.rules), name, to) });
                        }}
                      />
                    )}
                  </Fld>
                  <RuleReading value={rule} definition={value} />
                  <RuleEditor value={rule as Rule} pointer={join(pointer, 'rules', name)} onChange={(next) => set({ rules: { ...rules, [name]: next } })} />
                </div>
              </Collapsible>
            );
          })}
        </fieldset>
      ),
    },
    {
      value: 'machine',
      label: (
        <span className="flex items-center gap-2">
          {uiText("editors.DefinitionEditor.state_machine_670ced")}{value.machine ? <Badge tone="accent">{uiText("editors.DefinitionEditor.aktiv_3139fc")}</Badge> : <Badge>{uiText("editors.DefinitionEditor.keine_db028e")}</Badge>}
          <IssueCount pointer={join(pointer, 'machine')} />
        </span>
      ),
      content: value.machine ? (
        <div className="flex max-w-4xl flex-col gap-4">
          {value.type_id === 'sleep.flow' && <MachineReading definition={value} />}
          <MachineEditor value={asObject(value.machine)} pointer={join(pointer, 'machine')} definition={value} caps={caps} locked={locked} onChange={(machine) => set({ machine })} />
          {!locked && (
            <div>
              <button type="button" className="cursor-pointer text-xs text-bad hover:underline" onClick={() => set({ machine: undefined })}>
                {uiText("editors.DefinitionEditor.state_machine_aus_dieser_definition_entfernen_46ef4d")}</button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex max-w-xl flex-col items-start gap-3">
          <p className="text-sm text-muted">{uiText("editors.DefinitionEditor.diese_definition_hat_keine_state_machine_eine_ma_eb27c0")}</p>
          <AddButton
            disabled={locked}
            onClick={() =>
              set({
                machine: {
                  id: 'machine',
                  states: ['idle', 'active'],
                  initial: 'idle',
                  sessions: false,
                  commands: {},
                  transitions: [],
                  reinitialize_when: { op: 'literal', schema: newFieldSchema('bool'), value: false },
                },
              })
            }
          >
            {uiText("editors.DefinitionEditor.state_machine_hinzufugen_c9125d")}</AddButton>
        </div>
      ),
    },
    {
      value: 'json',
      label: uiText("editors.DefinitionEditor.json_experte_99d21a"),
      content: (
        <div className="max-w-4xl">
          <p className="mb-2 text-xs text-muted">{uiText("editors.DefinitionEditor.optionale_expertenansicht_derselben_daten_ungult_e2b8d0")}</p>
          <fieldset disabled={locked}>
            <JsonValueEditor label={uiText("editors.DefinitionEditor.definition_9813fa")} pointer={pointer} rows={24} value={value} onChange={(next) => next && typeof next === 'object' && !Array.isArray(next) && onChange(next)} />
          </fieldset>
        </div>
      ),
    },
  ];

  const [tab, setTab] = useState(initialTab ?? 'general');
  useEffect(() => {
    if (initialTab) setTab(initialTab);
  }, [initialTab]);
  return (
    <RuleEnvProvider value={{ ctx, caps, locked }}>
      <div className="flex flex-col gap-4">
        {semanticLocked && (
          <Notice tone="warn" title={<span className="flex items-center gap-2"><Lock size={14} /> {uiText("editors.DefinitionEditor.aktivierte_version_semantisch_unveranderlich_8bfc17")}</span>}>
            {lockReason ?? uiText("editors.DefinitionEditor.diese_version_war_bereits_aktiv_faa77f")} {uiText("editors.DefinitionEditor.redaktionelle_felder_name_beschreibung_bleiben_a_a99fa7")}</Notice>
        )}
        <IssuesAt pointer={pointer} />
        <Tabs items={items} value={tab} onValueChange={setTab} />
      </div>
    </RuleEnvProvider>
  );
}

export function LockBadge() {
  return (
    <Tip label={uiText("editors.DefinitionEditor.diese_version_war_bereits_aktiv_und_ist_semantis_b868d2")}>
      <span tabIndex={0}>
        <Badge tone="info"><Lock size={11} /> {uiText("editors.DefinitionEditor.gesperrt_4a76d6")}</Badge>
      </span>
    </Tip>
  );
}
