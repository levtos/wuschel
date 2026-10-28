import { codeLabel, fieldLabel } from '@/i18n/codes';
import { t as uiText } from '@/i18n';
import { Trash2 } from 'lucide-react';
import { Checkbox } from '@/components/ui/controls';
import { Input, Select } from '@/components/ui/form';
import { deref, type JsonSchema } from '@/lib/capabilities';
import { deepEqual, isObject, join, type Json, type JsonObject } from '@/lib/json';
import { AddButton, Fld, IconButton, NumberField, SectionTitle } from './common';
import { ValueEditor } from './ValueEditor';

/**
 * Generic form for any JSON Schema fragment delivered by the server (type
 * parameter schemas, properties no specialised editor knows). It interprets the
 * schema; it does not validate — the server stays authoritative. It handles
 * $ref, nullable and general unions (variant picker), enums/consts, scalars,
 * arrays, closed objects, open objects (typed key/value rows) and recursive
 * structures. What it cannot interpret falls back to the typed value editor —
 * graphical as well — so no value is ever dropped and JSON is never required.
 */

export interface SchemaFormProps {
  root: JsonSchema;
  node: Json | undefined;
  value: Json | undefined;
  onChange: (next: Json | undefined) => void;
  pointer: string;
  label?: string;
  required?: boolean;
  /** Renders a discriminated `op` union (the recursive rule editor). */
  renderRule?: (props: { value: JsonObject; onChange: (next: JsonObject) => void; pointer: string; label?: string }) => React.ReactNode;
  depth?: number;
}

const MAX_DEPTH = 24;

/** Merge `allOf` members that are plain object fragments into one schema node. */
export function merged(root: JsonSchema, node: JsonObject, seen = new Set<JsonObject>(), depth = 0): JsonObject {
  if (!Array.isArray(node.allOf)) return node;
  if (seen.has(node) || depth > MAX_DEPTH) return {};
  seen.add(node);
  let result: JsonObject = { ...node };
  delete result.allOf;
  for (const part of node.allOf) {
    const resolved = deref(root, part as Json);
    if (!resolved || 'if' in resolved || 'then' in resolved) continue; // conditionals are validation hints
    const inner = merged(root, resolved, new Set(seen), depth + 1);
    const properties: JsonObject = { ...(isObject(inner.properties) ? inner.properties : {}) };
    for (const [key, value] of Object.entries(isObject(result.properties) ? result.properties : {})) {
      properties[key] = key in properties ? { allOf: [properties[key]!, value] } : value;
    }
    result = {
      ...inner,
      ...result,
      properties,
      required: [...new Set([...(Array.isArray(inner.required) ? inner.required : []), ...(Array.isArray(result.required) ? result.required : [])])],
    };
    if (typeof inner.minimum === 'number') result.minimum = Math.max(inner.minimum, typeof result.minimum === 'number' ? result.minimum : inner.minimum);
    if (typeof inner.maximum === 'number') result.maximum = Math.min(inner.maximum, typeof result.maximum === 'number' ? result.maximum : inner.maximum);
    if (Array.isArray(inner.enum) && Array.isArray(node.enum)) result.enum = inner.enum.filter((v) => (node.enum as Json[]).some((x) => deepEqual(v, x)));
  }
  return result;
}

function typesOf(node: JsonObject): string[] {
  return Array.isArray(node.type) ? node.type.map(String) : typeof node.type === 'string' ? [node.type] : [];
}

function variantsOf(node: JsonObject): Json[] | null {
  const list = Array.isArray(node.anyOf) ? node.anyOf : Array.isArray(node.oneOf) ? node.oneOf : null;
  return list as Json[] | null;
}

const isNullVariant = (v: Json | undefined): boolean => isObject(v) && (v.type === 'null' || (Array.isArray(v.type) && v.type.length === 1 && v.type[0] === 'null') || ('const' in v && v.const === null));

export function defaultFor(root: JsonSchema, rawNode: Json | undefined, depth = 0): Json {
  const raw = deref(root, rawNode) ?? {};
  const node = depth > MAX_DEPTH ? raw : merged(root, raw);
  if ('default' in node) return node.default as Json;
  if ('const' in node) return node.const as Json;
  if (Array.isArray(node.enum) && node.enum.length) return node.enum[0] as Json;
  const variants = variantsOf(node);
  if (variants?.length) {
    const first = variants.find((v) => !isNullVariant(v)) ?? variants[0];
    return depth > MAX_DEPTH ? null : defaultFor(root, first, depth + 1);
  }
  const types = typesOf(node).filter((t) => t !== 'null');
  switch (types[0]) {
    case 'string':
      return '';
    case 'integer':
    case 'number':
      return typeof node.minimum === 'number' ? node.minimum : 0;
    case 'boolean':
      return false;
    case 'array':
      return [];
    case 'object':
      return objectDefault(root, node, depth);
    default:
      return isObject(node.properties) ? objectDefault(root, node, depth) : null;
  }
}

/** A new object starts with the properties that must exist (required or pinned by `const`). */
function objectDefault(root: JsonSchema, node: JsonObject, depth: number): JsonObject {
  const properties = isObject(node.properties) ? node.properties : {};
  const required = new Set(Array.isArray(node.required) ? (node.required as string[]) : []);
  const out: JsonObject = {};
  for (const [key, spec] of Object.entries(properties)) {
    const resolved = deref(root, spec as Json);
    if (required.has(key) || (resolved && 'const' in resolved)) out[key] = depth > MAX_DEPTH ? null : defaultFor(root, spec as Json, depth + 1);
  }
  return out;
}

/** Does `value` fit the (shallow) shape of this variant? Used to pick the active union branch. */
export function variantMatches(root: JsonSchema, variant: Json, value: Json | undefined, depth = 0): boolean {
  const raw = deref(root, variant);
  if (!raw || depth > MAX_DEPTH) return false;
  const node = merged(root, raw);
  if (isNullVariant(node)) return value === null || value === undefined;
  if ('const' in node) return JSON.stringify(node.const) === JSON.stringify(value);
  if (Array.isArray(node.enum)) return node.enum.some((e) => JSON.stringify(e) === JSON.stringify(value));
  const nested = variantsOf(node);
  if (nested && depth < MAX_DEPTH) return nested.some((v) => variantMatches(root, v, value, depth + 1));
  const types = typesOf(merged(root, node));
  const kind = value === null || value === undefined ? 'null' : Array.isArray(value) ? 'array' : typeof value === 'object' ? 'object' : typeof value === 'number' ? (Number.isInteger(value) ? 'integer' : 'number') : typeof value;
  if (types.length === 0) return isObject(node.properties) ? isObject(value) && discriminatorFits(node, value) : false;
  const fitsType = types.some((t) => t === kind || (t === 'number' && kind === 'integer'));
  return fitsType && (kind !== 'object' || !isObject(value) || discriminatorFits(node, value));
}

function discriminatorFits(node: JsonObject, value: JsonObject): boolean {
  const properties = isObject(node.properties) ? node.properties : {};
  if (Array.isArray(node.required) && node.required.some((key) => typeof key === 'string' && !(key in value))) return false;
  for (const [key, spec] of Object.entries(properties)) {
    if (isObject(spec) && 'const' in spec && key in value && value[key] !== spec.const) return false;
  }
  return true;
}

function variantLabel(root: JsonSchema, variant: Json, index: number): string {
  const node = deref(root, variant) ?? {};
  if (typeof node.title === 'string') return fieldLabel(node.title);
  if ('const' in node) return `= ${JSON.stringify(node.const)}`;
  if (isNullVariant(node)) return uiText("editors.SchemaForm.leer_null_ce1303");
  const types = typesOf(node);
  if (types.length) return types.map(codeLabel).join(' / ');
  if (isObject(variant) && typeof variant.$ref === 'string') return variant.$ref.split('/').pop() ?? uiText("editors.SchemaForm.variante_v0_78b949", { v0: (index + 1) });
  return uiText("editors.SchemaForm.variante_v0_78b949", { v0: (index + 1) });
}

export function SchemaForm(props: SchemaFormProps) {
  const { root, value, onChange, pointer, label, required, renderRule } = props;
  const depth = props.depth ?? 0;
  const raw = deref(root, props.node);
  const title = pointer.includes('/wake_settings/') && pointer.endsWith('/id') ? uiText('b17.wakeId') : label ?? (isObject(raw) && typeof raw.title === 'string' ? fieldLabel(raw.title) : fieldLabel(pointer.split('/').pop() ?? ''));
  const caption = required ? `${title} *` : title;
  if (!raw || depth > MAX_DEPTH) {
    return <ValueEditor label={caption || uiText("editors.SchemaForm.wert_7e347d")} pointer={pointer} value={value ?? null} onChange={onChange} />;
  }
  const node = merged(root, raw);
  const rec = (extra: Partial<SchemaFormProps>) => <SchemaForm {...props} depth={depth + 1} {...extra} />;

  // JSON Schema type arrays are unions too, including an explicit null branch.
  if (Array.isArray(node.type) && node.type.length > 1) {
    const union = { ...node };
    delete union.type;
    return rec({ node: { ...union, anyOf: node.type.map((type) => ({ ...union, type })) } });
  }

  if (isObject(node.discriminator) && node.discriminator.propertyName === 'op' && renderRule && isObject(value)) {
    return <>{renderRule({ value, onChange, pointer, label: caption })}</>;
  }

  // Unions (anyOf/oneOf). A single non-null variant is just an optional value.
  const variants = variantsOf(node);
  if (variants && variants.length > 0) {
    const nonNull = variants.filter((v) => !isNullVariant(v));
    const nullable = nonNull.length < variants.length;
    const single = nonNull.length === 1 && nonNull[0] !== undefined ? nonNull[0] : undefined;
    const singleNode = single !== undefined ? deref(root, single) : undefined;
    if (single !== undefined && singleNode && !('enum' in singleNode) && !('const' in singleNode) && ['string', 'number', 'integer'].includes(typesOf(singleNode)[0] ?? '')) {
      const numeric = typesOf(singleNode)[0] !== 'string';
      return (
        <div className="flex flex-col gap-2"><Fld label={caption} pointer={pointer}>
          {(p) => (
            <Input
              {...p}
              type={numeric ? 'number' : 'text'}
              step={numeric ? 'any' : undefined}
              value={value === null || value === undefined ? '' : String(value)}
              onChange={(e) => { if (!numeric || e.target.value !== '') onChange(numeric ? Number(e.target.value) : e.target.value); }}
            />
          )}
        </Fld>{nullable && <AddButton onClick={() => onChange(null)}>{uiText("editors.SchemaForm.auf_null_setzen_cc2a5b")}</AddButton>}</div>
      );
    }
    if (single !== undefined && nullable && (value === null || value === undefined)) {
      return (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">{caption}</span>
          <div><AddButton onClick={() => onChange(defaultFor(root, single, depth + 1))}>{uiText("editors.SchemaForm.wert_festlegen_55b559")}</AddButton></div>
        </div>
      );
    }
    if (single !== undefined) return <div className="flex flex-col gap-2">{rec({ node: single, label: title })}{nullable && <AddButton onClick={() => onChange(null)}>{uiText("editors.SchemaForm.auf_null_setzen_cc2a5b")}</AddButton>}</div>;
    // General union: pick the branch, then edit it with its own schema.
    const activeIndex = Math.max(0, variants.findIndex((v) => variantMatches(root, v, value)));
    const active = variants[activeIndex]!;
    return (
      <div className="flex flex-col gap-2">
        <Fld label={uiText("editors.SchemaForm.v0_variante_d57948", { v0: (caption) })} pointer={pointer}>
          {(p) => (
            <Select {...p} value={String(activeIndex)} onChange={(e) => onChange(defaultFor(root, variants[Number(e.target.value)], depth + 1))}>
              {variants.map((v, i) => <option key={i} value={i}>{variantLabel(root, v, i)}</option>)}
            </Select>
          )}
        </Fld>
        {isNullVariant(active) ? null : rec({ node: active, label: caption, required: false })}
      </div>
    );
  }

  if ('const' in node) {
    return (
      <Fld label={caption} pointer={pointer}>
        {(p) => <Input {...p} readOnly value={pointer.includes('/wake_settings/') && typeof node.const === 'string' ? codeLabel(node.const) : JSON.stringify(node.const)} />}
      </Fld>
    );
  }
  if (Array.isArray(node.enum)) {
    const options = node.enum;
    const selected = options.findIndex((v) => deepEqual(v, value));
    const platformField = pointer.includes('/wake_settings/') || ['kind', 'type', 'mode', 'source_type', 'source_origin', 'monitor', 'target', 'op', 'strategy', 'operation', 'comparison'].includes(pointer.split('/').pop() ?? '');
    const labels = options.map((v) => typeof v === 'object' ? JSON.stringify(v) : typeof v === 'boolean' || platformField ? codeLabel(v) : String(v));
    const unique = new Set(labels).size === options.length;
    const rawIds = options.map((value) => typeof value === 'object' ? JSON.stringify(value) : String(value));
    const ids = new Set(rawIds).size === options.length ? rawIds : options.map((_, index) => String(index));
    return (
      <Fld label={caption} pointer={pointer}>
        {(p) => (
          <Select {...p} value={selected < 0 ? '' : ids[selected]} onChange={(e) => onChange(options[ids.indexOf(e.target.value)])}>
            {selected < 0 && <option value="" disabled>{uiText("editors.SchemaForm.wert_wahlen_c1328a")}</option>}
            {options.map((v, i) => <option key={i} value={ids[i]}>{unique ? labels[i] : JSON.stringify(v)}</option>)}
          </Select>
        )}
      </Fld>
    );
  }

  const types = typesOf(node).filter((t) => t !== 'null');
  const nullableType = typesOf(node).includes('null');
  const primary = types[0];

  if (primary === 'boolean' && types.length === 1) return <Checkbox checked={value === true} onCheckedChange={onChange} label={caption} />;
  if ((primary === 'integer' || primary === 'number') && types.length === 1) {
    return (
      <NumberField
        label={caption}
        pointer={pointer}
        value={typeof value === 'number' ? value : 0}
        min={typeof node.minimum === 'number' ? node.minimum : typeof node.exclusiveMinimum === 'number' && /grace_s$/.test(pointer) ? node.exclusiveMinimum + 0.000001 : undefined}
        max={typeof node.maximum === 'number' ? node.maximum : undefined}
        step={primary === 'integer' ? 1 : undefined}
        hint={typeof node.exclusiveMinimum === "number" ? uiText("editors.SchemaForm.gro_er_als_v0_f2a9b6", { v0: (node.exclusiveMinimum) }) : nullableType ? undefined : undefined}
        onChange={onChange}
      />
    );
  }
  if (primary === 'string' && types.length === 1) {
    return (
      <Fld label={caption} pointer={pointer} hint={typeof node.pattern === "string" ? <>{uiText("editors.SchemaForm.muster_905f9d")}<code>{node.pattern}</code></> : undefined}>
        {(p) => <Input {...p} value={typeof value === 'string' ? value : ''} onChange={(e) => onChange(e.target.value)} />}
      </Fld>
    );
  }

  if (primary === 'array' && types.length === 1) {
    const list = Array.isArray(value) ? value : [];
    if (!node.items) return <ValueEditor label={caption} pointer={pointer} value={list} onChange={onChange} allowedKinds={['array']} />;
    return (
      <div className="flex flex-col gap-2">
        <SectionTitle aside={<AddButton onClick={() => onChange([...list, defaultFor(root, node.items, depth + 1)])}>{uiText("editors.SchemaForm.eintrag_99b925")}</AddButton>}>{caption}</SectionTitle>
        {list.map((item, i) => (
          <div key={i} className="flex items-start gap-2 rounded-[var(--radius-md)] border border-line p-2">
            <div className="min-w-0 flex-1">
              {rec({ node: node.items, value: item, label: uiText("editors.SchemaForm.v0_cdb96d", { v0: (i + 1) }), pointer: join(pointer, i), required: false, onChange: (next) => onChange(list.map((x, j) => (j === i ? (next ?? null) : x))) })}
            </div>
            <IconButton label={uiText("editors.SchemaForm.eintrag_entfernen_706a15")} tone="danger" onClick={() => onChange(list.filter((_, j) => j !== i))}>
              <Trash2 size={14} />
            </IconButton>
          </div>
        ))}
        {list.length === 0 && <p className="text-xs text-faint">{uiText("editors.SchemaForm.leer_f13f8e")}</p>}
      </div>
    );
  }

  const hasProperties = isObject(node.properties) && Object.keys(node.properties).length > 0;
  if ((primary === 'object' || hasProperties) && types.length <= 1) {
    const properties = isObject(node.properties) ? node.properties : {};
    const requiredKeys = Array.isArray(node.required) ? (node.required as string[]) : [];
    const current = isObject(value) ? value : {};
    const open = node.additionalProperties !== false;
    const extraKeys = Object.keys(current).filter((k) => !(k in properties));
    const additional = isObject(node.additionalProperties) ? node.additionalProperties : undefined;
    const set = (key: string, next: Json | undefined) => {
      const copy: JsonObject = { ...current };
      if (next === undefined) delete copy[key];
      else copy[key] = next;
      onChange(copy);
    };
    return (
      <div className="flex flex-col gap-3">
        {props.label !== undefined && <SectionTitle>{caption}</SectionTitle>}
        {Object.entries(properties).map(([key, child]) => {
          const spec = deref(root, child as Json);
          if (key === 'op' && spec && 'const' in spec) return null; // the operator itself is chosen in the header
          const optionalNullable = !requiredKeys.includes(key) && spec && ('default' in spec ? spec.default === null : false);
          return (
            <div key={key}>
              {rec({ node: child as Json, label: undefined, value: current[key], pointer: join(pointer, key), required: requiredKeys.includes(key), onChange: (next) => set(key, next === undefined || (next === null && optionalNullable) ? undefined : next) })}
            </div>
          );
        })}
        {(open || extraKeys.length > 0) && (
          <div className="flex flex-col gap-2">
            {extraKeys.length > 0 || open ? <SectionTitle aside={open ? <AddButton onClick={() => { let n = extraKeys.length + 1; while (`schluessel${n}` in current) n += 1; set(`schluessel${n}`, additional ? defaultFor(root, additional, depth + 1) : ''); }}>{uiText("editors.SchemaForm.eigenschaft_f854ee")}</AddButton> : undefined}>{uiText("editors.SchemaForm.weitere_eigenschaften_5d81c5")}</SectionTitle> : null}
            {extraKeys.map((key) => (
              <div key={key} className="grid items-start gap-2 sm:grid-cols-[minmax(8rem,12rem)_1fr_auto]">
                <Input
                  aria-label={uiText("editors.SchemaForm.v0_schlussel_v1_59f39d", { v0: (caption), v1: (key) })}
                  defaultValue={pointer.endsWith('/wake_settings/profiles') ? fieldLabel(key) : key}
                  readOnly={pointer.endsWith('/wake_settings/profiles')}
                  key={key}
                  onBlur={(e) => {
                    if (pointer.endsWith('/wake_settings/profiles')) return;
                    const to = e.target.value;
                    if (!to || to === key || to in current) return void (e.target.value = key);
                    onChange(Object.fromEntries(Object.entries(current).map(([k, v]) => [k === key ? to : k, v])));
                  }}
                />
                {additional ? rec({ node: additional as Json, label: pointer.endsWith('/wake_settings/profiles') ? fieldLabel(key) : key, value: current[key], pointer: join(pointer, key), onChange: (next) => set(key, next ?? null) }) : <ValueEditor label={key} pointer={join(pointer, key)} value={current[key] ?? null} expert={false} onChange={(next) => set(key, next)} />}
                <IconButton label={uiText("editors.SchemaForm.v0_entfernen_92fea7", { v0: (key) })} tone="danger" onClick={() => set(key, undefined)}><Trash2 size={14} /></IconButton>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // Mixed or unspecified: the typed value editor (graphical; JSON only as an optional view).
  return <ValueEditor label={caption || uiText("editors.SchemaForm.wert_7e347d")} pointer={pointer} value={value ?? null} onChange={onChange} />;
}
