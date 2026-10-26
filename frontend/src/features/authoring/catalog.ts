import { t as uiText } from '@/i18n';
import type { Capabilities, TypeDescription } from '@/lib/capabilities';
import { isObject, type JsonObject } from '@/lib/json';
import { asArray, asObject, str, type FieldSchema } from './model';

/**
 * Types an instance can use: package types and active definitions from the
 * capability catalog, overlaid with the definitions of the draft being edited
 * (a draft definition is not registered anywhere until activation).
 */
export interface TypeEntry {
  key: string;
  type_id: string;
  type_version: number;
  origin: 'package' | 'active-definition' | 'draft-definition';
  inputs: { name: string; required: boolean; schema?: FieldSchema }[];
  fields: Record<string, FieldSchema>;
  parameterSchema?: JsonObject;
  declarative: boolean;
  machine: boolean;
  lifecycle?: string;
}

export const typeKey = (id: string, version: number | string): string => `${id}@${version}`;

export function typeCatalog(caps: Capabilities | undefined, config: JsonObject | null | undefined): TypeEntry[] {
  const entries = new Map<string, TypeEntry>();
  for (const t of caps?.types ?? []) {
    entries.set(typeKey(t.type_id, t.type_version), fromDescription(t));
  }
  for (const raw of asArray(config?.definitions)) {
    if (!isObject(raw)) continue;
    const id = str(raw.type_id);
    const version = Number(raw.type_version);
    if (!id || !Number.isFinite(version)) continue;
    const fields = asObject(asObject(raw.schema).fields) as Record<string, FieldSchema>;
    entries.set(typeKey(id, version), {
      key: typeKey(id, version),
      type_id: id,
      type_version: version,
      origin: 'draft-definition',
      declarative: true,
      machine: isObject(raw.machine),
      lifecycle: str(raw.lifecycle) || 'active',
      fields,
      parameterSchema: declarativeParameterSchema(raw, caps?.structured_parameter_schemas),
      inputs: asArray(raw.inputs)
        .filter(isObject)
        .map((i) => {
          const schema = asObject(i.schema) as FieldSchema;
          return { name: str(i.name), required: schema.required !== false, schema };
        }),
    });
  }
  return [...entries.values()].sort((a, b) => a.type_id.localeCompare(b.type_id) || a.type_version - b.type_version);
}

function fromDescription(t: TypeDescription): TypeEntry {
  const declarative = t.definition !== undefined;
  return {
    key: typeKey(t.type_id, t.type_version),
    type_id: t.type_id,
    type_version: t.type_version,
    origin: declarative ? 'active-definition' : 'package',
    declarative,
    lifecycle: str(t.definition?.lifecycle) || 'active',
    machine: t.producer === 'state_machine' || (declarative && isObject(t.definition?.machine)),
    fields: t.schema.fields as Record<string, FieldSchema>,
    inputs: t.inputs.map((i) => ({ name: i.name, required: i.required })),
    parameterSchema: t.parameter_schema,
  };
}

export function declarativeParameterSchema(definition: JsonObject, structured?: Record<string, JsonObject>): JsonObject {
  const properties: JsonObject = {};
  const definitions: JsonObject = {};
  const required: string[] = [];
  for (const [name, declaration] of Object.entries(asObject(definition.parameter_schema))) {
    if (!isObject(declaration)) continue;
    const schema = asObject(declaration.schema);
    if (schema.required === true) required.push(name);
    const model = structured?.[str(declaration.structured_schema)];
    if (model) {
      const { $defs, ...body } = model;
      Object.assign(definitions, asObject($defs));
      properties[name] = { ...body, title: str(declaration.label) || name };
      continue;
    }
    properties[name] = {
      type: schema.type === 'bool' ? 'boolean' : schema.type === 'number' ? 'number' : 'string',
      title: schema.unit ? uiText('alpha8.parameters.unitHint', { description: str(declaration.label) || name, unit: str(schema.unit) }) : str(declaration.label) || name,
      ...(schema.unit ? { description: uiText('alpha8.parameters.unitHint', { description: str(declaration.description), unit: str(schema.unit) }) } : declaration.description ? { description: declaration.description } : {}),
      ...(Array.isArray(schema.value_catalog) && schema.value_catalog.length ? { enum: schema.value_catalog } : {}),
      ...(schema.type === 'timestamp' ? { format: 'date-time' } : {}),
      ...(typeof declaration.minimum === 'number' ? { minimum: declaration.minimum } : {}),
      ...(typeof declaration.maximum === 'number' ? { maximum: declaration.maximum } : {}),
    };
  }
  return { type: 'object', properties, required, additionalProperties: false, ...(Object.keys(definitions).length ? { $defs: definitions } : {}) };
}

/** Reference candidates for an instance input: sources and `contract.field` outputs. */
export interface RefOption {
  value: string;
  label: string;
  kind: 'source' | 'contract';
  detail?: string;
  schema?: FieldSchema;
}

export function refOptions(config: JsonObject | null | undefined, types: TypeEntry[], excludeContract?: string): RefOption[] {
  const out: RefOption[] = [];
  for (const source of asArray(config?.sources).filter(isObject)) {
    const id = str(source.source_id);
    const kind = ({ boolean: 'bool', numeric: 'number', text: "text" } as Record<string, string>)[str(source.source_type)];
    const schema = isObject(source.value_schema) ? source.value_schema as FieldSchema : kind ? { type: kind, required: true, physical_state: false } as FieldSchema : undefined;
    if (id) out.push({ value: id, label: id, kind: 'source', detail: str(source.source_type), schema });
  }
  for (const contract of asArray(config?.contracts).filter(isObject)) {
    const id = str(contract.contract_id);
    if (!id || id === excludeContract) continue;
    const type = types.find((t) => t.key === typeKey(str(contract.type_id), Number(contract.type_version)));
    out.push({ value: id, label: id, kind: 'contract', detail: uiText("features.authoring.catalog.wert_7e347d"), schema: type?.fields.value });
    for (const [field, schema] of Object.entries(type?.fields ?? {})) {
      out.push({ value: `${id}.${field}`, label: uiText("features.authoring.catalog.v0_v1_a30df1", { v0: (id), v1: (field) }), kind: 'contract', detail: str((schema as FieldSchema).type), schema });
    }
  }
  return out;
}
