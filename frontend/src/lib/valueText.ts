import { t as uiText } from '@/i18n';
import type { FieldSchema } from '@/features/authoring/model';
import { PLATFORM_VALUES } from './platform-values';
import { asObject, str } from '@/features/authoring/model';

export function hasValueLabel(value: string, schema?: FieldSchema): boolean { return !!str(asObject(asObject(schema?.legend)[value]).display_name) || !!PLATFORM_VALUES[value]; }

/** Translate declared enums only; identical words in text fields remain user data. */
export function valueText(value: unknown, schema?: FieldSchema): string {
  if (typeof value === 'boolean') return value ? uiText('beta3.cde9e58a9a4e') : uiText('beta3.90ebc1bde6f3');
  if (schema?.type === 'enum') return str(asObject(asObject(schema.legend)[String(value)]).display_name) || PLATFORM_VALUES[String(value)] || uiText(Array.isArray(schema.value_catalog) && schema.value_catalog.includes(String(value)) ? 'b22.values.missing' : 'beta3.022e1c98e18a');
  return String(value ?? '—');
}

export function countSentence(count: number, kind: 'changes' | 'contracts' | 'sources' | 'drafts'): string {
  const sentences = {
    changes: [uiText('beta3.e6c5344cca41'), uiText('beta3.f20394e1dc61'), uiText('beta3.d55f3f5fbb60', { v0: (count) })],
    contracts: [uiText('beta3.e6f0a1714c57'), uiText('beta3.10b7e1fe9f41'), uiText('beta3.bb2d973984da', { v0: (count) })],
    sources: [uiText('beta3.669810205c76'), uiText('beta3.cc06c19a8c7b'), `${count} Quellen`],
    drafts: [uiText('beta3.21639f9cc8eb'), uiText('beta3.6e007a22b082'), uiText('beta3.47fc55349897', { v0: (count) })],
  };
  return sentences[kind][count === 0 ? 0 : count === 1 ? 1 : 2]!;
}
