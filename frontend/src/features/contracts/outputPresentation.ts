import { t } from '@/i18n';
import type { FieldSchema } from '@/features/authoring/model';
import type { FieldValue } from '@/lib/types';

/** Non-semantic annotations on the frontend output-schema projection. They never
 * enter a stored definition, its fingerprint or an engine's field values. */
const SLEEP_GROUPS = [
  { key: 'now', fields: ['value', 'since_at', 'sleep_context_active'] },
  { key: 'wake', fields: ['wake_next_wake_start', 'next_wake_start', 'wake_window_start', 'window_start', 'wake_window_end', 'window_end', 'wake_plan_date', 'plan_date', 'wake_status', 'status', 'wake_decision', 'decision', 'wake_minimum_sleep_applied', 'minimum_sleep_applied'] },
  { key: 'episode', fields: ['episode_active', 'episode_started_at', 'entry_origin', 'last_action_origin', 'sleep_started_at', 'deadline_at', 'episode_id'] },
];
export const technicalValue = (name: string, field?: FieldValue) => name === 'episode_id' || typeof field?.value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(field.value);
export function outputFields(typeId: string, fields: Record<string, FieldSchema>): Record<string, FieldSchema> {
  if (!['sleep.flow', 'sleep.state_view', 'wake.plan_view'].includes(typeId)) return fields;
  return Object.fromEntries(Object.entries(fields).map(([name, field]) => {
    const groupIndex = SLEEP_GROUPS.findIndex(group => group.fields.includes(name));
    const group = SLEEP_GROUPS[groupIndex];
    return [name, { ...field, ...(group ? { display_group: t(`b22.output.group.${group.key}`), group_order: groupIndex, display_order: group.fields.indexOf(name) } : {}), ...(name === 'episode_id' ? { technical: true } : {}) }];
  }));
}
export function orderedFields(fields: Record<string, FieldSchema>, values: Record<string, FieldValue>) {
  const names = [...Object.keys(fields), ...Object.keys(values).filter(name => !(name in fields))];
  return names.filter(name => values[name] && fields[name]?.technical !== true && !technicalValue(name, values[name]))
    .map((name, index) => ({ name, field: values[name]!, schema: fields[name], index }))
    .sort((a, b) => Number(a.schema?.group_order ?? a.index) - Number(b.schema?.group_order ?? b.index) || Number(a.schema?.display_order ?? a.index) - Number(b.schema?.display_order ?? b.index));
}
