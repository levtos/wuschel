import { codeLabel } from '@/i18n/codes';
import { formatNumber } from '@/lib/format';
import { t as uiText } from '@/i18n';
import { ChevronRight, Copy, Trash2 } from 'lucide-react';
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { Checkbox, Collapsible } from '@/components/ui/controls';
import { Input, Select } from '@/components/ui/form';
import {
  asArray,
  asObject,
  convertRule,
  newFieldSchema,
  newRule,
  str,
  STATEFUL_OPS,
  type FieldSchema,
  type OperatorDefaults,
  type Rule,
  type RuleContext,
} from '@/features/authoring/model';
import { enumAt, type Capabilities } from '@/lib/capabilities';
import { isObject, join, setAt, toPointer, type Json, type JsonObject } from '@/lib/json';
import { cn } from '@/lib/utils';
import { AddButton, Fld, IconButton, IssueCount, IssuesAt, NumberField, RowActions, SectionTitle, TypedValueEditor } from './common';
import { FieldSchemaEditor } from './FieldSchemaEditor';
import { SchemaForm } from './SchemaForm';
import { defaultLiteralValue } from '@/features/authoring/model';
import { Extras, HANDLED } from './Extras';
import { AnnualCurvePoints } from './AnnualCurvePoints';

/**
 * Recursive editor for the closed, tagged rule union. Operators, formulas,
 * fusion strategies and temporal operations come from the capability metadata;
 * operators without a dedicated editor are rendered by the generic schema form,
 * and unknown ones by raw JSON — never silently dropped.
 */

export interface RuleEnv {
  ctx: RuleContext;
  caps: Capabilities | undefined;
  /** Activated definition versions are semantically immutable. */
  locked: boolean;
}

const EnvContext = createContext<RuleEnv | null>(null);
export const RuleEnvProvider = EnvContext.Provider;
function useEnv(): RuleEnv {
  const env = useContext(EnvContext);
  if (!env) throw new Error(uiText("editors.RuleEditor.ruleenvprovider_fehlt_4c1afc"));
  return env;
}

export function operatorDefaults(caps: Capabilities | undefined): OperatorDefaults {
  return {
    formulas: caps?.formulas.map((f) => ({ name: f.name, version: f.version })) ?? [],
    fusion: caps?.fusion.map((f) => f.strategy) ?? [],
    temporal: caps?.temporal.map((t) => ({ operation: t.operation, arguments: t.arguments })) ?? [],
  };
}

const OP_LABELS: Record<string, string> = {
  annual_curve: uiText('beta3.annualCurve'),
  calendar_active: uiText('beta3.calendarActive'),
  input: uiText("editors.RuleEditor.eingang_d8af5e"),
  known_automatic: uiText('alpha9.context.automatic'),
  ref: uiText('editors.RuleEditor.interne_regel_verweis'),
  literal: uiText("editors.RuleEditor.konstante_2e2736"),
  and: uiText("editors.RuleEditor.und_fd2908"),
  or: uiText("editors.RuleEditor.oder_43e406"),
  not: uiText("editors.RuleEditor.nicht_774d65"),
  compare: uiText("editors.RuleEditor.vergleich_79a63c"),
  managed_catalog_lookup: uiText('alpha10.catalog.lookup'),
  map: uiText("editors.RuleEditor.zuordnung_map_5ecf05"),
  first_match: uiText("editors.RuleEditor.erster_treffer_e8c4e8"),
  bucket: uiText("editors.RuleEditor.hysterese_bucket_688e48"),
  formula: uiText("editors.RuleEditor.formel_244289"),
  fusion: uiText("editors.RuleEditor.fusion_09d751"),
  temporal: uiText("editors.RuleEditor.zeitlich_temporal_542f27"),
  machine: uiText("editors.RuleEditor.zustand_der_state_machine_2b1f38"),
  not_applicable: uiText("editors.RuleEditor.nicht_anwendbar_af8969"),
};
const OP_GROUPS: { label: string; ops: string[] }[] = [
  { label: uiText("editors.RuleEditor.werte_6fac44"), ops: ['input', 'ref', 'literal'] },
  { label: uiText("editors.RuleEditor.logik_eff656"), ops: ['and', 'or', 'not', 'compare', 'map', 'first_match', 'not_applicable'] },
  { label: uiText("editors.RuleEditor.berechnung_fusion_55d3b1"), ops: ['formula', 'fusion', 'bucket'] },
  { label: uiText("editors.RuleEditor.zeit_zustand_6dfc62"), ops: ['temporal', 'machine'] },
];
const FALLBACK_OPS = Object.keys(OP_LABELS);

const COMPARISONS: Record<string, string> = {
  eq: uiText("editors.RuleEditor.gleich_dcabf6"),
  ne: uiText("editors.RuleEditor.ungleich_d4ec9a"),
  lt: uiText("editors.RuleEditor.kleiner_fa34fb"),
  le: uiText("editors.RuleEditor.hochstens_a9e2ae"),
  gt: uiText("editors.RuleEditor.gro_er_ccbed1"),
  ge: uiText("editors.RuleEditor.mindestens_dc2ecc"),
};

const TEMPORAL_HINTS: Record<string, string> = {
  stable_for: uiText("editors.RuleEditor.wahr_erst_wenn_der_wahrheitswert_uber_die_dauer__cdd9b0"),
  dwell: uiText("editors.RuleEditor.verweildauer_des_wahrheitswerts_wahrheitswert_ei_37f3bf"),
  grace: uiText("editors.RuleEditor.halt_den_letzten_gultigen_wert_bis_zum_ursprungl_05d8ca"),
  edge: uiText("editors.RuleEditor.flankenerkennung_eines_wahrheitswerts_0325ea"),
  delta: uiText("editors.RuleEditor.differenz_zweier_zahlen_5cc0dd"),
  age: uiText("editors.RuleEditor.alter_der_messung_benotigt_messzeitpunkt_evidenz_ce2c48"),
};

function summary(rule: Rule): string {
  switch (rule.op) {
    case 'input':
    case 'ref':
      return str(rule.name) || '—';
    case 'literal':
      return typeof rule.value === 'boolean' ? codeLabel(String(rule.value)) : typeof rule.value === 'number' ? formatNumber(rule.value) : isObject(rule.value) || Array.isArray(rule.value) ? uiText('alpha7.copy.structuredValue') : String(rule.value ?? uiText('alpha7.copy.noValue'));
    case 'compare':
      return COMPARISONS[str(rule.comparison)] ?? '';
    case 'bucket':
    case 'temporal':
      return `${str(rule.id)}${rule.operation ? ` · ${codeLabel(rule.operation)}` : ''}`;
    case 'formula':
      return `${str(rule.name)}@${String(rule.version ?? '')}`;
    case 'fusion':
      return codeLabel(rule.strategy);
    default:
      return Array.isArray(rule.args) ? uiText(rule.args.length === 1 ? 'alpha7.copy.oneArgument' : 'alpha7.copy.arguments', { n: rule.args.length }) : '';
  }
}

interface NodeProps {
  value: Rule;
  onChange: (next: Rule) => void;
  pointer: string;
  label?: string;
  onRemove?: () => void;
  /** Allowed to wrap/remove-as-list-item; roots are replaced, not removed. */
  depth?: number;
}

/** One expression node with its operator-specific body and recursive children. */
export function RuleEditor({ value, onChange, pointer, label, onRemove, depth = 0 }: NodeProps) {
  const env = useEnv();
  const defaults = useMemo(() => operatorDefaults(env.caps), [env.caps]);
  const [open, setOpen] = useState(depth < 6);
  const knownOps = env.caps?.operators.map((o) => o.op) ?? FALLBACK_OPS;
  const available = knownOps.filter((op) => !(env.ctx.stateless && STATEFUL_OPS.has(op)));
  const op = value.op;
  const known = knownOps.includes(op);
  const ruleDef = env.caps?.operators.find((o) => o.op === op)?.schema_ref.split('/').pop();

  const switchOp = (next: string) => onChange(convertRule(value, next, env.ctx, defaults));
  const wrap = (wrapper: 'not' | 'and' | 'or') => onChange({ op: wrapper, args: wrapper === 'not' ? [value] : [value, newRule('input', env.ctx, defaults)] });

  return (
    <div className="rounded-[var(--radius-md)] border border-line border-l-[3px] border-l-faint bg-sunken/60" data-op={op} data-pointer={pointer}>
      <div className="flex flex-wrap items-center gap-2 px-2.5 py-2">
        <span role="button" tabIndex={0} aria-label={open ? uiText("editors.RuleEditor.zuklappen_41b22f") : uiText("editors.RuleEditor.aufklappen_0f644f")} aria-expanded={open} className="cursor-pointer text-muted hover:text-fg" onClick={() => setOpen((v) => !v)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setOpen((v) => !v); } }}>
          <ChevronRight size={14} className={cn('transition-transform', open && 'rotate-90')} />
        </span>
        {label && <span className="text-xs font-medium text-muted">{label}</span>}
        <Select
          aria-label={uiText("editors.RuleEditor.operator_v0_e1f310", { v0: (label ? uiText("editors.RuleEditor.v0_37b56e", { v0: (label) }) : '') })}
          className="h-8 w-auto min-w-44 text-xs"
          value={op}
          disabled={env.locked}
          onChange={(e) => switchOp(e.target.value)}
        >
          {!known && <option value={op}>{uiText('alpha7.copy.unknownCalculation')}</option>}
          {OP_GROUPS.map((group) => {
            const ops = group.ops.filter((o) => available.includes(o));
            return ops.length === 0 ? null : (
              <optgroup key={group.label} label={group.label}>
                {ops.map((o) => (
                  <option key={o} value={o}>{OP_LABELS[o] ?? codeLabel(o)}</option>
                ))}
              </optgroup>
            );
          })}
          {available.filter((o) => !OP_GROUPS.some((g) => g.ops.includes(o))).map((o) => (
            <option key={o} value={o}>{OP_LABELS[o] ?? codeLabel(o)}</option>
          ))}
        </Select>
        {!open && <span className="mono min-w-0 truncate text-xs text-muted">{summary(value)}</span>}
        <span className="ml-auto flex items-center gap-1">
          <IssueCount pointer={pointer} />
          {!env.locked && (
            <>
              <IconButton label={uiText("editors.RuleEditor.in_nicht_einpacken_f78273")} onClick={() => wrap('not')}>
                <span className="text-[10px] font-bold">¬</span>
              </IconButton>
              <IconButton label={uiText("editors.RuleEditor.in_und_einpacken_ad37d5")} onClick={() => wrap('and')}>
                <span className="text-[10px] font-bold">∧</span>
              </IconButton>
              <IconButton label={uiText("editors.RuleEditor.in_oder_einpacken_6e76ab")} onClick={() => wrap('or')}>
                <span className="text-[10px] font-bold">∨</span>
              </IconButton>
              <IconButton label={uiText("editors.RuleEditor.knoten_duplizieren_json_in_zwischenablage_060036")} onClick={() => void navigator.clipboard?.writeText(JSON.stringify(value))}>
                <Copy size={13} />
              </IconButton>
              {onRemove && (
                <IconButton label={uiText("editors.RuleEditor.entfernen_382837")} tone="danger" onClick={onRemove}>
                  <Trash2 size={14} />
                </IconButton>
              )}
            </>
          )}
        </span>
      </div>
      {open && (
        <div className="flex flex-col gap-3 border-t border-line/70 px-3 pb-3 pt-3">
          <IssuesAt pointer={pointer} />
          <fieldset disabled={env.locked} className="contents">
            <Body value={value} onChange={onChange} pointer={pointer} depth={depth} />
            {ruleDef && ruleDef in HANDLED && !(value.op === 'bucket' && Array.isArray(value.classes)) && (
              <Extras
                def={ruleDef}
                value={value}
                pointer={pointer}
                onChange={(next) => onChange(next as Rule)}
                renderRule={({ value: v, onChange: c, pointer: p, label: l }) => <RuleEditor value={v as Rule} onChange={(n) => c(n)} pointer={p} label={l} depth={depth + 1} />}
              />
            )}
          </fieldset>
        </div>
      )}
    </div>
  );
}

function Child({ parent, pointer, depth, ...props }: { parent: Rule; onChange: (next: Rule) => void; pointer: string; depth: number; keyPath: (string | number)[]; label?: string; onRemove?: () => void }) {
  const { keyPath, onChange } = props;
  const child = keyPath.reduce<Json | undefined>((node, key) => (Array.isArray(node) ? node[Number(key)] : isObject(node) ? node[String(key)] : undefined), parent as Json);
  if (!isObject(child) || typeof child.op !== 'string') {
    return <p className="text-xs text-bad">{uiText("editors.RuleEditor.ausdruck_fehlt_oder_ist_ungultig_427904")}</p>;
  }
  return (
    <RuleEditor
      value={child as Rule}
      pointer={join(pointer, ...keyPath)}
      depth={depth + 1}
      label={props.label}
      onRemove={props.onRemove}
      onChange={(next) => onChange(setAt(parent as Json, toPointer(keyPath), next) as Rule)}
    />
  );
}

function ArgList({ value, onChange, pointer, depth, min = 1, max, labels, addLabel = uiText('alpha7.copy.argument') }: { value: Rule; onChange: (next: Rule) => void; pointer: string; depth: number; min?: number; max?: number; labels?: string[]; addLabel?: string }) {
  const env = useEnv();
  const defaults = useMemo(() => operatorDefaults(env.caps), [env.caps]);
  const args = asArray(value.args);
  const set = (next: Json[]) => onChange({ ...value, args: next });
  return (
    <div className="flex flex-col gap-2">
      {args.map((_, i) => (
        <div key={i} className="flex items-start gap-1">
          <div className="min-w-0 flex-1">
            <Child parent={value} pointer={pointer} depth={depth} keyPath={['args', i]} label={labels?.[i] ?? uiText("editors.RuleEditor.v0_383901", { v0: (i + 1) })} onChange={onChange} />
          </div>
          {max !== min && (
            <RowActions
              index={i}
              count={args.length}
              removeDisabled={args.length <= min}
              onMove={(from, to) => {
                const copy = args.slice();
                const [moved] = copy.splice(from, 1);
                copy.splice(to, 0, moved as Json);
                set(copy);
              }}
              onRemove={() => set(args.filter((_, j) => j !== i))}
            />
          )}
        </div>
      ))}
      {(max === undefined || args.length < max) && (
        <div>
          <AddButton onClick={() => set([...args, newRule('input', env.ctx, defaults)])}>{addLabel}</AddButton>
        </div>
      )}
    </div>
  );
}

function Body({ value, onChange, pointer, depth }: { value: Rule; onChange: (next: Rule) => void; pointer: string; depth: number }) {
  const env = useEnv();
  const defaults = useMemo(() => operatorDefaults(env.caps), [env.caps]);
  const { ctx, caps } = env;
  const set = (patch: Record<string, Json | undefined>) => {
    const next: JsonObject = { ...value };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete next[k];
      else next[k] = v;
    }
    onChange(next as Rule);
  };

  switch (value.op) {
    case 'annual_curve': return <>
      <p className="text-sm text-muted">{uiText('beta3.languageHelp')}</p>
      <Child parent={value} pointer={pointer} depth={depth} keyPath={['at']} label={uiText('beta3.date')} onChange={onChange} />
      <AnnualCurvePoints value={value} onChange={onChange} pointer={pointer} />
    </>;
    case 'calendar_active': return <>
      <p className="text-sm text-muted">{uiText('beta3.languageHelp')}</p>
      <Child parent={value} pointer={pointer} depth={depth} keyPath={['arg']} label={uiText('beta3.calendarList')} onChange={onChange} />
      <Child parent={value} pointer={pointer} depth={depth} keyPath={['pattern']} label={uiText('beta3.pattern')} onChange={onChange} />
      <p className="text-sm text-muted">{uiText('beta3.patternHelp')}</p>
      <Checkbox checked={value.all_day_only === true} onCheckedChange={checked => set({ all_day_only: checked })} label={uiText('beta3.allDayOnly')} />
    </>;
    case 'managed_catalog_lookup': return <>
      <Fld label={uiText('alpha10.catalog.catalog')} pointer={join(pointer, 'catalog_id')}>{p => <Input {...p} value={str(value.catalog_id)} onChange={e => set({ catalog_id: e.target.value })} />}</Fld>
      <NumberField label={uiText('editors.RegistryObjects.version_dd1679')} pointer={join(pointer, 'definition_version')} value={Number(value.definition_version ?? 1)} min={1} step={1} onChange={n => set({ definition_version: Math.max(1, Math.trunc(n)) })} />
      <p className="text-sm text-muted">{uiText('alpha10.catalog.languageHelp')}</p>
      {['scope', 'media_type', 'signal_type', 'key'].map(name => <Child key={name} parent={value} pointer={pointer} depth={depth} keyPath={[name]} label={uiText(`alpha10.catalog.field.${name}`)} onChange={onChange} />)}
      <FieldSchemaEditor pointer={join(pointer, 'schema')} value={asObject(value.schema) as FieldSchema} onChange={next => set({ schema: next })} />
    </>;
    case 'input': {
      const names = ctx.inputs.map((i) => i.name);
      const current = str(value.name);
      const declared = ctx.inputs.find((i) => i.name === current);
      return (
        <Fld label={uiText("editors.RuleEditor.eingang_d8af5e")} pointer={join(pointer, 'name')} hint={declared ? uiText("editors.RuleEditor.typ_v0_v1_e5ef47", { v0: codeLabel(declared.schema.type), v1: (declared.schema.unit ? uiText("editors.RuleEditor.v0_8b43bb", { v0: (String(declared.schema.unit)) }) : '') }) : uiText("editors.RuleEditor.dieser_name_ist_in_der_definition_nicht_als_eing_bd4b8c")}>
          {(p) => (
            <Select {...p} value={current} onChange={(e) => set({ name: e.target.value })}>
              {!names.includes(current) && <option value={current}>{current || uiText("editors.RuleEditor.wahlen_602246")}</option>}
              {names.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </Select>
          )}
        </Fld>
      );
    }
    case 'ref': {
      const current = str(value.name);
      return (
        <Fld label={uiText("editors.RuleEditor.interne_regel_744cdc")} pointer={join(pointer, 'name')} hint={uiText("editors.RuleEditor.benannte_regeln_werden_pro_auswertung_nur_einmal_5ec5f3")}>
          {(p) => (
            <Select {...p} value={current} onChange={(e) => set({ name: e.target.value })}>
              {!ctx.ruleNames.includes(current) && <option value={current}>{current || uiText("editors.RuleEditor.wahlen_602246")}</option>}
              {ctx.ruleNames.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </Select>
          )}
        </Fld>
      );
    }
    case 'literal': {
      const schema = asObject(value.schema) as FieldSchema;
      return (
        <div className="flex flex-col gap-3">
          <TypedValueEditor schema={schema} value={(value.value ?? null) as Json} pointer={join(pointer, 'value')} onChange={(v) => set({ value: v })} />
          <Collapsible title={uiText('alpha7.template.constantAdvanced')}><FieldSchemaEditor
            compact
            pointer={join(pointer, 'schema')}
            value={schema}
            onChange={(next) => set({ schema: next, ...(next.type !== schema.type ? { value: defaultLiteralValue(next) } : {}) })}
          /></Collapsible>
        </div>
      );
    }
    case 'and':
    case 'or':
      return <ArgList value={value} onChange={onChange} pointer={pointer} depth={depth} addLabel={uiText("editors.RuleEditor.operand_c81de8")} />;
    case 'not':
      return <ArgList value={value} onChange={onChange} pointer={pointer} depth={depth} min={1} max={1} labels={[uiText('editors.RuleEditor.operand_c81de8')]} />;
    case 'compare':
      return (
        <div className="flex flex-col gap-3">
          <Fld label={uiText("editors.RuleEditor.vergleich_79a63c")} pointer={join(pointer, 'comparison')} className="max-w-56">
            {(p) => (
              <Select {...p} value={str(value.comparison)} onChange={(e) => set({ comparison: e.target.value })}>
                {(enumAt(caps?.schemas.definition, 'CompareRule', 'comparison', Object.keys(COMPARISONS))).map((k) => (
                  <option key={k} value={k}>{COMPARISONS[k] ?? codeLabel(k)}</option>
                ))}
              </Select>
            )}
          </Fld>
          <Child parent={value} pointer={pointer} depth={depth} keyPath={['left']} label={uiText("editors.RuleEditor.links_9024c1")} onChange={onChange} />
          <Child parent={value} pointer={pointer} depth={depth} keyPath={['right']} label={uiText("editors.RuleEditor.rechts_00e9e5")} onChange={onChange} />
        </div>
      );
    case 'not_applicable':
      return <Child parent={value} pointer={pointer} depth={depth} keyPath={['arg']} label={uiText("editors.RuleEditor.bedingung_trifft_nicht_zu_wenn_wahr_cb4a34")} onChange={onChange} />;
    case 'first_match': {
      const cases = asArray(value.cases);
      const set2 = (next: Json[]) => set({ cases: next });
      return (
        <div className="flex flex-col gap-3">
          <p className="text-xs text-muted">{uiText("editors.RuleEditor.die_erste_zeile_deren_wenn_wahr_ist_bestimmt_das_300b11")}</p>
          {cases.map((_, i) => (
            <div key={i} className="flex items-start gap-1 rounded-[var(--radius-md)] border border-line p-2">
              <div className="flex min-w-0 flex-1 flex-col gap-2">
                <Child parent={value} pointer={pointer} depth={depth} keyPath={['cases', i, 'when']} label={uiText("editors.RuleEditor.wenn_312fc0")} onChange={onChange} />
                <Child parent={value} pointer={pointer} depth={depth} keyPath={['cases', i, 'then']} label={uiText("editors.RuleEditor.dann_612577")} onChange={onChange} />
              </div>
              <RowActions
                index={i}
                count={cases.length}
                removeDisabled={cases.length <= 1}
                onMove={(from, to) => {
                  const copy = cases.slice();
                  const [moved] = copy.splice(from, 1);
                  copy.splice(to, 0, moved as Json);
                  set2(copy);
                }}
                onRemove={() => set2(cases.filter((_, j) => j !== i))}
              />
            </div>
          ))}
          <div>
            <AddButton onClick={() => set2([...cases, { when: newRule('input', ctx, defaults), then: newRule('literal', ctx, defaults) }])}>{uiText("editors.RuleEditor.fall_e85a64")}</AddButton>
          </div>
        </div>
      );
    }
    case 'map':
      return <MapBody value={value} onChange={onChange} pointer={pointer} depth={depth} />;
    case 'bucket':
      if (Array.isArray(value.classes)) return <GenericBody value={value} onChange={onChange} pointer={pointer} depth={depth} />;
      return (
        <div className="flex flex-col gap-3">
          <AddButton onClick={() => onChange({ op: 'bucket', id: value.id ?? 'bucket', arg: value.arg ?? newRule('input', ctx, defaults), classes: ['niedrig', 'hoch'], schema: { ...newFieldSchema('enum'), value_catalog: ['niedrig', 'hoch'] }, boundaries: [{ at: { op: 'literal', schema: newFieldSchema('number'), value: 1 } }], initial: { unknown: true } })}>{uiText('alpha8.bucket.multiclass')}</AddButton>
          <p className="text-xs text-muted">{uiText("editors.RuleEditor.hysterese_schaltet_erst_uber_oben_ein_und_erst_u_629b5d")}</p>
          <div className="grid gap-3 sm:grid-cols-4">
            <Fld label={uiText("editors.RuleEditor.id_eindeutig_df6df9")} pointer={join(pointer, 'id')}>
              {(p) => <Input {...p} value={str(value.id)} onChange={(e) => set({ id: e.target.value })} />}
            </Fld>
            <NumberField label={uiText("editors.RuleEditor.unten_39c51d")} pointer={join(pointer, 'lower')} value={Number(value.lower ?? 0)} onChange={(n) => set({ lower: n })} />
            <NumberField label={uiText("editors.RuleEditor.oben_0d7d94")} pointer={join(pointer, 'upper')} value={Number(value.upper ?? 0)} onChange={(n) => set({ upper: n })} />
            <Fld label={uiText("editors.RuleEditor.initial_1f3974")} pointer={join(pointer, 'initial')}>
              {(p) => (
                <Select {...p} value={String(value.initial === true)} onChange={(e) => set({ initial: e.target.value === 'true' })}>
                  <option value="false">{uiText("editors.RuleEditor.aus_2337c3")}</option>
                  <option value="true">{uiText("editors.RuleEditor.an_ea325d")}</option>
                </Select>
              )}
            </Fld>
          </div>
          <Child parent={value} pointer={pointer} depth={depth} keyPath={['arg']} label={uiText("editors.RuleEditor.zahl_654eb4")} onChange={onChange} />
        </div>
      );
    case 'formula': {
      const formulas = (caps?.formulas ?? []).filter(formula => formula.lifecycle !== 'retired' || formula.name === value.name);
      const names = [...new Set(formulas.map((f) => f.name))];
      const versions = formulas.filter((f) => f.name === value.name).map((f) => f.version);
      const selected = formulas.find((f) => f.name === value.name && f.version === value.version);
      return (
        <div className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Fld label={uiText("editors.RuleEditor.formel_244289")} pointer={join(pointer, 'name')} hint={selected ? uiText("editors.RuleEditor.eingange_v0_v1_v2_cd9f97", { v0: codeLabel(selected.input_schema.type), v1: (selected.input_schema.unit ? uiText("editors.RuleEditor.v0_37b56e", { v0: (String(selected.input_schema.unit)) }) : ''), v2: codeLabel(selected.output_schema.type) }) : undefined}>
              {(p) => (
                <Select
                  {...p}
                  value={str(value.name)}
                  onChange={(e) => {
                    const first = formulas.find((f) => f.name === e.target.value);
                    set({ name: e.target.value, version: first?.version ?? 1, args: first?.inputs?.length ? Object.fromEntries(first.inputs.filter(input => input.required).map(input => [input.name, newRule('input', ctx, defaults)])) : asArray(value.args) });
                  }}
                >
                  {!names.includes(str(value.name)) && <option value={str(value.name)}>{str(value.name) || '—'} {uiText("editors.RuleEditor.unbekannt_319b5c")}</option>}
                  {names.map((n) => (
                    <option key={n} value={n}>{codeLabel(n)}</option>
                  ))}
                </Select>
              )}
            </Fld>
            <Fld label={uiText("editors.RuleEditor.version_dd1679")} pointer={join(pointer, 'version')}>
              {(p) => (
                <Select {...p} value={String(value.version ?? '')} onChange={(e) => set({ version: Number(e.target.value) })}>
                  {!versions.includes(Number(value.version)) && <option value={String(value.version ?? '')}>{String(value.version ?? '—')}</option>}
                  {versions.map((v) => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </Select>
              )}
            </Fld>
          </div>
          {selected?.inputs?.length ? selected.inputs.map(input => <div key={input.name}>{isObject(asObject(value.args)[input.name]) ? <><Child parent={value} pointer={pointer} depth={depth} keyPath={['args', input.name]} label={uiText('alpha8.formula.inputLabel', { name: input.name, unit: input.schema.unit ? uiText('alpha8.formula.unit', { unit: String(input.schema.unit) }) : '' })} onChange={onChange} />{!input.required && <button type="button" onClick={() => set({ args: Object.fromEntries(Object.entries(asObject(value.args)).filter(([name]) => name !== input.name)) })}>{uiText('alpha8.formula.removeInput', { name: input.name })}</button>}</> : <AddButton onClick={() => set({ args: { ...asObject(value.args), [input.name]: newRule('input', ctx, defaults) } })}>{uiText('alpha8.formula.addInput', { name: input.name })}</AddButton>}</div>) : <ArgList value={value} onChange={onChange} pointer={pointer} depth={depth} />}
        </div>
      );
    }
    case 'fusion': {
      const strategies = caps?.fusion ?? [];
      const current = strategies.find((s) => s.strategy === value.strategy);
      return (
        <div className="flex flex-col gap-3">
          <Fld
            label={uiText("editors.RuleEditor.strategie_bfecce")}
            pointer={join(pointer, 'strategy')}
            hint={[current?.priority === 'argument_order' ? uiText("editors.RuleEditor.die_reihenfolge_der_argumente_ist_die_prioritat_09f0f4") : '', current?.conflict_detection ? uiText("editors.RuleEditor.erkennt_konflikte_zwischen_gleich_gewichteten_ka_e83977") : ''].filter(Boolean).join(' ') || undefined}
            className="max-w-sm"
          >
            {(p) => (
              <Select {...p} value={str(value.strategy)} onChange={(e) => set({ strategy: e.target.value })}>
                {!current && <option value={str(value.strategy)}>{str(value.strategy) || '—'} {uiText("editors.RuleEditor.unbekannt_319b5c")}</option>}
                {strategies.map((s) => (
                  <option key={s.strategy} value={s.strategy}>{codeLabel(s.strategy)}</option>
                ))}
              </Select>
            )}
          </Fld>
          <ArgList value={value} onChange={onChange} pointer={pointer} depth={depth} addLabel={uiText("editors.RuleEditor.kandidat_3c49ac")} />
        </div>
      );
    }
    case 'temporal': {
      const operations = caps?.temporal ?? [];
      const operation = str(value.operation);
      const meta = operations.find((o) => o.operation === operation);
      const arity = meta?.arguments ?? (operation === 'delta' ? 2 : 1);
      return (
        <div className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-4">
            <Fld label={uiText("editors.RuleEditor.id_eindeutig_df6df9")} pointer={join(pointer, 'id')}>
              {(p) => <Input {...p} value={str(value.id)} onChange={(e) => set({ id: e.target.value })} />}
            </Fld>
            <Fld label={uiText("editors.RuleEditor.operation_0f044f")} pointer={join(pointer, 'operation')} className="sm:col-span-1">
              {(p) => (
                <Select
                  {...p}
                  value={operation}
                  onChange={(e) => {
                    const nextArity = operations.find((o) => o.operation === e.target.value)?.arguments ?? 1;
                    const args = asArray(value.args).slice(0, nextArity);
                    while (args.length < nextArity) args.push(newRule('input', ctx, defaults));
                    set({ operation: e.target.value, args });
                  }}
                >
                  {!meta && <option value={operation}>{uiText('alpha7.copy.unknownCalculation')}</option>}
                  {operations.map((o) => (
                    <option key={o.operation} value={o.operation}>{codeLabel(o.operation)}</option>
                  ))}
                </Select>
              )}
            </Fld>
            {isObject(value.duration_s) ? <Child parent={value} pointer={pointer} depth={depth} keyPath={['duration_s']} label={uiText('editors.RuleEditor.dauer_s_e59ef1')} onChange={onChange} /> : <NumberField label={uiText("editors.RuleEditor.dauer_s_e59ef1")} pointer={join(pointer, 'duration_s')} value={Number(value.duration_s ?? 1)} min={0} onChange={(n) => set({ duration_s: n })} hint={uiText("editors.RuleEditor.gro_er_0_2ae790")} />}
            <div className="flex items-end pb-1.5">
              <Checkbox checked={value.accepts_held === true} onCheckedChange={(on) => set({ accepts_held: on || undefined })} label={uiText("editors.RuleEditor.gehaltene_werte_akzeptieren_034eb1")} />
            </div>
          </div>
          {TEMPORAL_HINTS[operation] && <p className="text-xs text-muted">{TEMPORAL_HINTS[operation]}</p>}
          <ArgList value={value} onChange={onChange} pointer={pointer} depth={depth} min={arity} max={arity} labels={arity === 2 ? [uiText('alpha7.copy.current'), uiText('alpha7.copy.comparison')] : [uiText('editors.RuleEditor.eingang_d8af5e')]} />
        </div>
      );
    }
    case 'machine':
      return (
        <p className="text-xs text-muted">
          {uiText("editors.RuleEditor.liefert_den_aktuellen_zustand_der_state_machine__723ee5")}{!ctx.hasMachine && <span className="ml-1 text-bad">{uiText("editors.RuleEditor.die_definition_hat_noch_keine_state_machine_im_r_062ddc")}</span>}
        </p>
      );
    default:
      return <GenericBody value={value} onChange={onChange} pointer={pointer} depth={depth} />;
  }
}

/** Operators not known to this UI: use the server's schema if listed, raw JSON otherwise. */
function GenericBody({ value, onChange, pointer, depth }: { value: Rule; onChange: (next: Rule) => void; pointer: string; depth: number }) {
  const { caps } = useEnv();
  const operator = caps?.operators.find((o) => o.op === value.op);
  const definitionSchema = caps?.schemas.definition;
  if (!operator || !definitionSchema) {
    return <SchemaForm root={{}} node={undefined} value={value} onChange={(next) => isObject(next) && onChange(next as Rule)} pointer={pointer} label={uiText("editors.RuleEditor.unbekannter_operator_json_a20a96")} />;
  }
  return (
    <SchemaForm
      root={definitionSchema}
      node={{ $ref: operator.schema_ref }}
      value={value}
      pointer={pointer}
      onChange={(next) => isObject(next) && onChange(next as Rule)}
      renderRule={({ value: v, onChange: c, pointer: p, label }) => (
        <RuleEditor value={v as Rule} onChange={(n) => c(n)} pointer={p} label={label} depth={depth + 1} />
      )}
    />
  );
}

/** Canonical map keys are JSON scalars encoded as object keys (docs/alpha4/workbench-api.md). */
export function decodeKey(key: string): { kind: 'bool' | 'number' | 'string' | 'raw'; text: string } {
  try {
    const parsed = JSON.parse(key) as unknown;
    if (typeof parsed === 'boolean') return { kind: 'bool', text: String(parsed) };
    if (typeof parsed === 'number') return { kind: 'number', text: key };
    if (typeof parsed === 'string') return { kind: 'string', text: parsed };
  } catch {
    /* fall through */
  }
  return { kind: 'raw', text: key };
}

function MapBody({ value, onChange, pointer, depth }: { value: Rule; onChange: (next: Rule) => void; pointer: string; depth: number }) {
  const { ctx } = useEnv();
  const schema = asObject(value.schema) as FieldSchema;
  const entries = Object.entries(asObject(value.entries));
  const arg = asObject(value.arg);
  const argSchema = arg.op === 'input' ? ctx.inputs.find((i) => i.name === arg.name)?.schema : undefined;
  const mode: 'bool' | 'number' | 'enum' | 'string' | 'raw' = !argSchema ? 'raw' : argSchema.type === 'bool' ? 'bool' : argSchema.type === 'number' ? 'number' : argSchema.type === 'enum' ? 'enum' : argSchema.type === 'object' ? 'raw' : 'string';
  const catalog = Array.isArray(argSchema?.value_catalog) ? (argSchema!.value_catalog as string[]) : [];

  const setEntries = (next: [string, Json][]) => onChange({ ...value, entries: Object.fromEntries(next) });
  const keysUsed = new Set(entries.map(([k]) => k));
  const freshKey = (): string => {
    if (mode === 'bool') return ['true', 'false'].find((k) => !keysUsed.has(k)) ?? 'true';
    if (mode === 'enum') return JSON.stringify(catalog.find((c) => !keysUsed.has(JSON.stringify(c))) ?? 'neu');
    if (mode === 'number') {
      let n = 0;
      while (keysUsed.has(String(n))) n += 1;
      return String(n);
    }
    let n = 1;
    while (keysUsed.has(JSON.stringify(`wert${n}`))) n += 1;
    return JSON.stringify(`wert${n}`);
  };

  return (
    <div className="flex flex-col gap-3">
      <Child parent={value} pointer={pointer} depth={depth} keyPath={['arg']} label={uiText("editors.RuleEditor.eingang_der_zuordnung_b6a678")} onChange={onChange} />
      <div>
        <SectionTitle>{uiText("editors.RuleEditor.ergebnistyp_a0907a")}</SectionTitle>
        <FieldSchemaEditor
          compact
          pointer={join(pointer, 'schema')}
          value={schema}
          onChange={(next) => {
            const typeChanged = next.type !== schema.type;
            onChange({ ...value, schema: next, ...(typeChanged ? { entries: Object.fromEntries(entries.map(([k]) => [k, defaultLiteralValue(next)])) } : {}) });
          }}
        />
      </div>
      <div className="flex flex-col gap-2">
        <SectionTitle aside={<AddButton onClick={() => setEntries([...entries, [freshKey(), defaultLiteralValue(schema)]])}>{uiText("editors.RuleEditor.eintrag_99b925")}</AddButton>}>{uiText("editors.RuleEditor.zuordnungen_6382c7")}</SectionTitle>
        {mode === 'raw' && <p className="text-xs text-muted">{uiText("editors.RuleEditor.schlussel_sind_kanonische_json_skalare_2c69e1")}<code>{uiText("editors.RuleEditor.true_b5bea4")}</code>, <code>42</code> {uiText("editors.RuleEditor.oder_7e3b22")}<code>{uiText("editors.RuleEditor.quot_text_quot_dc8483")}</code> {uiText("editors.RuleEditor.in_anfuhrungszeichen_ab3cf1")}</p>}
        {entries.map(([key, entryValue], i) => {
          const decoded = decodeKey(key);
          const rename = (nextKey: string) => {
            if (nextKey !== key && keysUsed.has(nextKey)) return;
            setEntries(entries.map(([k, v], j) => (j === i ? [nextKey, v] : [k, v])));
          };
          return (
            <div key={i} className="grid items-start gap-2 rounded-[var(--radius-md)] border border-line p-2 sm:grid-cols-[minmax(10rem,14rem)_1fr_auto]">
              <Fld label={uiText("editors.RuleEditor.wenn_eingang_53a1cc")} pointer={join(pointer, 'entries', key)}>
                {(p) =>
                  mode === 'bool' ? (
                    <Select {...p} value={decoded.text} onChange={(e) => rename(e.target.value)}>
                      <option value="true">{uiText("editors.RuleEditor.wahr_167f97")}</option>
                      <option value="false">{uiText("editors.RuleEditor.falsch_b3c3e0")}</option>
                    </Select>
                  ) : mode === 'enum' ? (
                    <Select {...p} value={key} onChange={(e) => rename(e.target.value)}>
                      {!catalog.some((c) => JSON.stringify(c) === key) && <option value={key}>{key}</option>}
                      {catalog.map((c) => (
                        <option key={c} value={JSON.stringify(c)}>{c}</option>
                      ))}
                    </Select>
                  ) : mode === 'string' ? (
                    <Input {...p} value={decoded.kind === 'string' ? decoded.text : key} onChange={(e) => rename(JSON.stringify(e.target.value))} />
                  ) : (
                    <Input {...p} className="font-mono text-xs" value={key} onChange={(e) => rename(e.target.value)} />
                  )
                }
              </Fld>
              <TypedValueEditor
                label={uiText("editors.RuleEditor.dann_06cb6c")}
                schema={schema}
                value={entryValue}
                pointer={join(pointer, 'entries', key)}
                onChange={(next) => setEntries(entries.map(([k, v], j) => (j === i ? [k, next] : [k, v])))}
              />
              <div className="pt-5">
                <IconButton label={uiText("editors.RuleEditor.eintrag_entfernen_706a15")} tone="danger" disabled={entries.length <= 1} onClick={() => setEntries(entries.filter((_, j) => j !== i))}>
                  <Trash2 size={14} />
                </IconButton>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Convenience wrapper wiring the environment around the root node of an expression. */
export function RuleRoot({ env, value, onChange, pointer, label, locked }: { env: Omit<RuleEnv, 'locked'>; value: Rule; onChange: (next: Rule) => void; pointer: string; label?: string; locked: boolean }): ReactNode {
  return (
    <RuleEnvProvider value={{ ...env, locked }}>
      <RuleEditor value={value} onChange={onChange} pointer={pointer} label={label} />
    </RuleEnvProvider>
  );
}

export { newFieldSchema };
