import { Field, Input, Select } from '@/components/ui/form';
import { t } from '@/i18n';
import type { Catalog } from './CatalogPage';

export type ClassValue = string | number | boolean;
export interface ClassDefinition {
  class_key: string;
  display_name: string;
  kind: 'enum' | 'integer' | 'boolean';
  default: ClassValue;
  values?: Record<string, { display_name: string; description: string }> | null;
  unit?: string | null;
  minimum?: number | null;
  maximum?: number | null;
}

export function uniqueClasses(catalogs: Catalog[]): ClassDefinition[] {
  return [...new Map(catalogs.flatMap(catalog => (catalog.class_definitions ?? []).map(definition => [definition.class_key, definition] as const))).values()];
}

export function classLabel(definition: ClassDefinition, value: ClassValue | undefined) {
  if (value === undefined) return '–';
  if (typeof value === 'boolean') return t(value ? 'b31.classes.yes' : 'b31.classes.no');
  if (typeof value === 'number') return `${value}${definition.unit ? ' ' + definition.unit : ''}`;
  return definition.values?.[value]?.display_name ?? value;
}

export function ClassField({ definition, value, onChange, label, emptyLabel, disabled = false }: {
  definition: ClassDefinition;
  value?: ClassValue;
  onChange: (value: ClassValue | undefined) => void;
  label?: string;
  emptyLabel?: string;
  disabled?: boolean;
}) {
  const variants: ClassValue[] = definition.kind === 'boolean' ? [true, false] : Object.keys(definition.values ?? {});
  const unit = definition.unit ? ` (${definition.unit})` : '';
  return <Field label={label ?? definition.display_name + unit}
    help={typeof value === 'string' ? definition.values?.[value]?.description : undefined}>
    {props => definition.kind === 'integer'
      ? <Input {...props} disabled={disabled} type="number" min={definition.minimum ?? undefined}
        max={definition.maximum ?? undefined} step={1} value={typeof value === 'number' ? value : ''}
        onChange={event => onChange(event.target.value === '' ? undefined : event.target.valueAsNumber)} />
      : <Select {...props} disabled={disabled} value={value === undefined ? '' : JSON.stringify(value)}
        onChange={event => onChange(event.target.value ? JSON.parse(event.target.value) as ClassValue : undefined)}>
        <option value="">{emptyLabel ?? t('a2.care.classFromDefault')}</option>
        {variants.map(item => <option key={String(item)} value={JSON.stringify(item)}>{classLabel(definition, item)}</option>)}
      </Select>}
  </Field>;
}

export function ClassFields({ definitions, values, onChange, disabled }: {
  definitions: ClassDefinition[];
  values: Record<string, ClassValue>;
  onChange: (values: Record<string, ClassValue>) => void;
  disabled?: boolean;
}) {
  return <>{definitions.map(definition => <ClassField
    key={definition.class_key} definition={definition} value={values[definition.class_key]} disabled={disabled}
    onChange={value => {
      const next = { ...values };
      if (value === undefined) delete next[definition.class_key];
      else next[definition.class_key] = value;
      onChange(next);
    }}
  />)}</>;
}
