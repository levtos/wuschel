import type { Json, JsonObject } from './json';
import { getAt, isObject } from './json';

/**
 * Typed view of GET /api/v1/capabilities. The editors interpret this metadata
 * instead of hard-coding the synthetic `test.*` types; unknown additions stay
 * reachable through the generic schema form.
 */
export type JsonSchema = JsonObject;

export interface FieldSchemaValue extends JsonObject {
  type: 'bool' | 'number' | 'enum' | 'text' | 'timestamp' | 'object';
  required: boolean;
  physical_state: boolean;
}

export interface TypeDescription {
  type_id: string;
  type_version: number;
  schema: { fields: Record<string, FieldSchemaValue>; available_projection: string[] } & JsonObject;
  producer: string;
  parameter_schema: JsonSchema;
  inputs: { name: string; required: boolean }[];
  restore: string[];
  commands?: Record<string, JsonSchema>;
  refresh_inputs?: string[];
  grace_outputs?: string[];
  definition?: JsonObject;
  definition_fingerprint?: string;
}

export interface Operator {
  op: string;
  stateful: boolean;
  schema_ref: string;
}

export interface Capabilities {
  capability_version: number;
  history: { kinds: string[]; limit: number } & JsonObject;
  draft_id: { pattern: string };
  types: TypeDescription[];
  schemas: { registry: JsonSchema; definition: JsonSchema; history_query: JsonSchema };
  operators: Operator[];
  structured_parameter_schemas?: Record<string, JsonObject>;
  manual_structured_schemas?: Record<string, JsonObject>;
  formulas: { name: string; version: number; input_schema: FieldSchemaValue; output_schema: FieldSchemaValue; inputs?: { name: string; schema: FieldSchemaValue; quantity: string; required: boolean }[]; category?: string; lifecycle?: string }[];
  fusion: { strategy: string; priority: string | null; conflict_detection: boolean }[];
  temporal: { operation: string; arguments: number; stateful: boolean; restores_absolute_deadlines: boolean }[];
  limits: Record<string, Json>;
  reasons: { version: number; codes: { code: string; description: string }[] };
  map_keys: JsonObject;
  fingerprint_versions: number[];
}

/** Resolve a local `#/$defs/…` reference. */
export function deref(root: JsonSchema, node: Json | undefined, seen = new Set<string>()): JsonObject | undefined {
  if (!isObject(node)) return undefined;
  const ref = node.$ref;
  if (typeof ref === 'string' && ref.startsWith('#/')) {
    if (seen.has(ref)) return undefined;
    seen.add(ref);
    const target = deref(root, getAt(root, ref.slice(1)), seen);
    if (!target) return undefined;
    const siblings = { ...node };
    delete siblings.$ref;
    return Object.keys(siblings).length ? { ...target, ...siblings } : target;
  }
  return node;
}

/** Enum values at `$defs/<def>/properties/<prop>/enum`; `fallback` if the server omits them. */
export function enumAt(
  schema: JsonSchema | undefined,
  def: string,
  prop: string,
  fallback: string[] = [],
): string[] {
  const value = schema ? getAt(schema, `/$defs/${def}/properties/${prop}/enum`) : undefined;
  return Array.isArray(value) && value.every((v) => typeof v === 'string') ? (value as string[]) : fallback;
}

export function numberLimit(caps: Capabilities | undefined, key: string, fallback: number): number {
  const value = caps?.limits?.[key];
  return typeof value === 'number' ? value : fallback;
}

export function findType(caps: Capabilities | undefined, typeId: string, version: number): TypeDescription | undefined {
  return caps?.types.find((t) => t.type_id === typeId && t.type_version === version);
}

/** Declared outputs of a type, used to offer `contract.field` references. */
export function typeFields(type: TypeDescription | undefined): string[] {
  return type ? Object.keys(type.schema.fields) : [];
}

/**
 * Fields the schema requires when `prop` has `value`, read from the server's
 * if/then conditions (e.g. Freshness.mode, AdapterConfig.kind). The browser
 * only interprets the metadata; it does not decide validity.
 */
export function requiredWhen(root: JsonSchema | undefined, def: string, prop: string, value: string): string[] {
  const conditions = root ? getAt(root, `/$defs/${def}/allOf`) : undefined;
  if (!Array.isArray(conditions)) return [];
  for (const condition of conditions) {
    if (!isObject(condition)) continue;
    const matches = getAt(condition, `/if/properties/${prop}/const`) === value;
    const required = getAt(condition, '/then/required');
    if (matches && Array.isArray(required)) return required.map(String);
  }
  return [];
}
