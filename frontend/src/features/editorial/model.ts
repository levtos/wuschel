import { asArray, asObject, str } from '@/features/authoring/model';
import { resolveRef } from '@/features/authoring/usage';
import { isObject, type JsonObject } from '@/lib/json';
import type { HaEntity, Registry } from '@/lib/types';

export const CATEGORIES = ['climate', 'light_cover', 'presence', 'sleep_wake', 'media', 'other', 'openings', 'time', 'devices'] as const;
export type Category = typeof CATEGORIES[number];
export const categoryKeys: Record<Category, string> = { climate: 'climate', light_cover: 'lightCover', presence: 'presence', sleep_wake: 'sleepWake', media: 'media', other: 'other', openings: 'openings', time: 'time', devices: 'devices' };
export const categoryIcons: Record<Category, string> = { climate: 'mdi:thermometer', light_cover: 'mdi:lightbulb', presence: 'mdi:account-check', sleep_wake: 'mdi:sleep', media: 'mdi:play-circle', other: 'mdi:shape', openings: 'mdi:window-open-variant', time: 'mdi:weather-sunset', devices: 'mdi:devices' };
export interface ObjectRef { kind: 'contract' | 'template'; id: string }
export interface Room { mode: 'suggested' | 'manual' | 'none'; area_id: string | null; name: string; icon: string; missing: boolean }
export interface EditorialEntry { object: ObjectRef; expected_editorial_version: number; category: Category; room: Room }
export interface EditorialRow { object: ObjectRef; editorial_version: number; category: Category; room: Room }
export interface EditorialResponse { items: EditorialRow[]; draft_items: EditorialRow[] }
export const NO_ROOM: Room = { mode: 'none', area_id: null, name: '', icon: 'mdi:home', missing: false };
export const objectKey = (object: ObjectRef) => `${object.kind}:${object.id}`;
export const roomKey = (room: Room) => room.mode === 'none' ? 'none' : room.area_id ? `area:${room.area_id}` : `name:${room.name}`;
export function findMetadata(data: EditorialResponse | undefined, object: ObjectRef): EditorialRow {
  return [...(data?.draft_items ?? []), ...(data?.items ?? [])].find(row => objectKey(row.object) === objectKey(object)) ?? { object, editorial_version: 0, category: 'other', room: NO_ROOM };
}
export function safeIcon(icon: unknown): string { return typeof icon === 'string' && /^mdi:[a-z0-9-]{1,64}$/.test(icon) ? icon : 'mdi:home'; }
export function areaRoom(entity: HaEntity): Room | null {
  return entity.area_id && entity.area_name ? { mode: 'suggested', area_id: entity.area_id, name: entity.area_name, icon: safeIcon(entity.area_icon), missing: false } : null;
}
/** A suggestion is limited to direct, fully known HA sources. */
export function suggestedRoom(config: Registry | null, contract: JsonObject | undefined, entities: HaEntity[]): { room: Room | null; multiple: boolean } {
  const inputs = asArray(contract?.inputs).filter(isObject);
  if (!inputs.length) return { room: null, multiple: false };
  const areas: Room[] = [];
  for (const input of inputs) {
    const target = resolveRef(str(input.ref), config);
    if (target?.kind !== 'source') return { room: null, multiple: false };
    const bindings = asArray(config?.bindings).filter(isObject).filter(binding => binding.source_id === target.id);
    if (!bindings.length) return { room: null, multiple: false };
    for (const binding of bindings) {
      const adapter = asObject(binding.adapter);
      if (!['ha_state', 'ha_attribute'].includes(str(adapter.kind))) return { room: null, multiple: false };
      const entity = entities.find(item => item.entity_id === adapter.ha_entity_id);
      const room = entity && areaRoom(entity);
      if (!room) return { room: null, multiple: false };
      areas.push(room);
    }
  }
  const ids = new Set(areas.map(room => room.area_id));
  return { room: ids.size === 1 ? areas[0]! : null, multiple: ids.size > 1 };
}
export function groupItems<T>(items: T[], key: (item: T) => string): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const item of items) { const id = key(item); const group = groups.get(id) ?? []; group.push(item); groups.set(id, group); }
  return [...groups.entries()];
}
