import { t } from '@/i18n';
import { formatTime } from '@/lib/format';
import { isObject, type JsonObject } from '@/lib/json';

export function isCalendarCache(value: unknown): value is JsonObject {
  return isObject(value) && typeof value.window_start === 'string'
    && typeof value.window_end === 'string' && Array.isArray(value.events);
}

export function CalendarReading({ cache }: { cache: JsonObject }) {
  const events = Array.isArray(cache.events) ? cache.events.filter(isObject) : [];
  return <div className="space-y-2 text-sm font-normal">
    <p>{t('a2.calendar.cacheWindow', { start: formatTime(String(cache.window_start)), end: formatTime(String(cache.window_end)) })}</p>
    {events.length ? <ul aria-label={t('a2.calendar.events')} className="divide-y divide-line">
      {events.map((event, index) => <li className="py-2" key={index}>
        <p className="font-medium">{typeof event.summary === 'string' && event.summary ? event.summary : t('a2.calendar.untitled')}</p>
        <p>{t('a2.calendar.range', { start: formatTime(typeof event.start === 'string' ? event.start : null), end: formatTime(typeof event.end === 'string' ? event.end : null) })}{event.all_day === true && ' · ' + t('a2.calendar.allDay')}</p>
      </li>)}
    </ul> : <p>{t('a2.calendar.noEvents')}</p>}
  </div>;
}
