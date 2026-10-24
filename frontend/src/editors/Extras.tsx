import { t as uiText } from '@/i18n';
import { Trash2 } from 'lucide-react';
import { Collapsible } from '@/components/ui/controls';
import { deref } from '@/lib/capabilities';
import { isObject, join, type Json, type JsonObject } from '@/lib/json';
import { useRegistrySchema } from '@/lib/schema-context';
import { IconButton } from './common';
import { merged, SchemaForm, type SchemaFormProps } from './SchemaForm';
import { ValueEditor } from './ValueEditor';

/** Properties each specialised editor renders itself; everything else comes from the schema. */
export const HANDLED = {
  FieldSchema: ['type', 'required', 'physical_state', 'value_catalog', 'unit'],
  InputRule: ['op', 'name'],
  ReferenceRule: ['op', 'name'],
  LiteralRule: ['op', 'schema', 'value'],
  BooleanRule: ['op', 'args'],
  CompareRule: ['op', 'comparison', 'left', 'right'],
  MapRule: ['op', 'arg', 'schema', 'entries'],
  FirstMatchRule: ['op', 'cases'],
  Case: ['when', 'then'],
  BucketRule: ['op', 'id', 'arg', 'initial', 'lower', 'upper'],
  FormulaRule: ['op', 'name', 'version', 'args'],
  FusionRule: ['op', 'strategy', 'args'],
  TemporalRule: ['op', 'id', 'operation', 'args', 'duration_s', 'accepts_held'],
  MachineRule: ['op'],
  NotApplicableRule: ['op', 'arg'],
  MachineDefinition: ['id', 'states', 'initial', 'transitions', 'commands', 'deadline_s', 'deadline_by_state', 'absolute_transition', 'unknown_deadline', 'sessions', 'reinitialize_when', 'allow_manual_restart'],
  TransitionDefinition: ['source', 'target', 'event', 'guard', 'reason', 'new_episode', 'ends_episode', 'argument_equals', 'deadline_action'],
  CommandDefinition: ['arguments'],
  DeclarativeDefinition: ['type_id', 'type_version', 'language_version', 'display_name', 'description', 'lifecycle', 'reactivation_note', 'inputs', 'refresh_inputs', 'schema', 'rules', 'outputs', 'machine', 'output_origins'],
  TypedInput: ['name', 'schema'],
  Schema: ['fields', 'available_projection', 'grace_capable', 'grace_parameter', 'fixture'],
  Source: ['source_id', 'source_type', 'source_origin', 'freshness', 'dependency_id', 'physical_source_key', 'display_name', 'value_schema', 'sensor_profile', 'sensor_parameters', 'value_origin', 'origin', 'manual_allow_future', 'manual_minimum', 'manual_maximum'],
  Freshness: ['mode', 'future_tolerance_s', 'interval_s', 'liveness_source'],
  Binding: ['binding_id', 'source_id', 'adapter', 'transform', 'display_name', 'refresh'],
  AdapterConfig: ['kind', 'ha_entity_id', 'ha_attribute', 'ha_time_attribute', 'mqtt_topic', 'mqtt_value_path', 'mqtt_time_path', 'window', 'refresh_s', 'max_age_s'],
  Transform: ['kind', 'target', 'factor', 'offset', 'catalog_id'],
  Catalog: ['catalog_id', 'kind', 'version', 'entries'],
  Contract: ['contract_id', 'type_id', 'type_version', 'enabled', 'ha_projection', 'inputs', 'parameters', 'display_name', 'description', 'refresh_plan'],
  Dependency: ['dependency_id', 'kind', 'monitor', 'source_id'],
} satisfies Record<string, string[]>;

export type DefName = keyof typeof HANDLED;

/**
 * Surfaces what the server's schema declares for `def` but the dedicated editor
 * does not render (new optional properties), and keeps members the schema does not
 * know visible and editable instead of hiding them. Nothing here removes data.
 */
export function Extras({
  def,
  value,
  onChange,
  pointer,
  renderRule,
}: {
  def: DefName | string;
  value: JsonObject;
  onChange: (next: JsonObject) => void;
  pointer: string;
  renderRule?: SchemaFormProps['renderRule'];
}) {
  const root = useRegistrySchema();
  const handled = new Set<string>((HANDLED as Record<string, string[]>)[def] ?? []);
  const raw = root ? deref(root, { $ref: `#/$defs/${def}` }) : undefined;
  const definition = root && raw ? merged(root, raw) : undefined;
  const properties = definition && isObject(definition.properties) ? definition.properties : {};
  const required = definition && Array.isArray(definition.required) ? (definition.required as string[]) : [];
  const declared = Object.keys(properties).filter((k) => !handled.has(k) && k !== 'op');
  const unknown = Object.keys(value).filter((k) => !handled.has(k) && !(k in properties) && k !== 'op');
  if (declared.length === 0 && unknown.length === 0) return null;
  const set = (key: string, next: Json | undefined) => {
    const copy: JsonObject = { ...value };
    if (next === undefined) delete copy[key];
    else copy[key] = next;
    onChange(copy);
  };
  return (
    <Collapsible title={uiText("editors.Extras.weitere_eigenschaften_v0_b0c6ee", { v0: (declared.length + unknown.length) })} defaultOpen className="border-dashed" >
      <div className="flex flex-col gap-3" data-testid={`extras-${def}`}>
        {root &&
          declared.map((key) => (
            <SchemaForm
              key={key}
              root={root}
              node={properties[key]}
              value={value[key]}
              pointer={join(pointer, key)}
              required={required.includes(key)}
              renderRule={renderRule}
              onChange={(next) => set(key, next)}
            />
          ))}
        {unknown.map((key) => (
          <div key={key} className="grid items-start gap-2 sm:grid-cols-[1fr_auto]">
            <ValueEditor label={uiText("editors.Extras.v0_dem_schema_unbekannt_erhalten_beffb5", { v0: (key) })} pointer={join(pointer, key)} value={value[key] ?? null} onChange={(next) => set(key, next)} />
            <IconButton label={uiText("editors.Extras.v0_entfernen_92fea7", { v0: (key) })} tone="danger" onClick={() => set(key, undefined)}><Trash2 size={14} /></IconButton>
          </div>
        ))}
      </div>
    </Collapsible>
  );
}
