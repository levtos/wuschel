import { t as uiText } from '@/i18n';
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tip } from '@/components/ui/controls';
import { Field, Input, Select, Textarea } from '@/components/ui/form';
import { explain, useIssues, useIssuesUnder } from '@/features/authoring/issues';
import type { FieldSchema } from '@/features/authoring/model';
import { isObject, type Json } from '@/lib/json';
import { cn } from '@/lib/utils';
import { ValueEditor } from './ValueEditor';
import { minuteHint, DurationField } from '@/components/SecondsField';

/** Label + control with the server findings for exactly this JSON Pointer shown at the input. */
export function Fld({
  label,
  pointer,
  hint,
  className,
  children,
}: {
  label: ReactNode;
  pointer: string;
  hint?: ReactNode;
  className?: string;
  children: Parameters<typeof Field>[0]['children'];
}) {
  const issues = useIssues(pointer);
  return (
    <Field
      pointer={pointer}
      label={label}
      hint={hint}
      className={className}
      errors={issues.map((issue) => ({ code: issue.code, message: explain(issue) }))}
    >
      {children}
    </Field>
  );
}

/** Findings addressed to a container itself (not to one of its inputs). */
export function IssuesAt({ pointer }: { pointer: string }) {
  const issues = useIssues(pointer);
  if (issues.length === 0) return null;
  return (
    <ul className="space-y-1">
      {issues.map((issue, i) => (
        <li key={i} role="alert" className="rounded-[var(--radius-sm)] border border-bad/40 bg-bad/10 px-2.5 py-1.5 text-xs text-bad">
          <span className="mono mr-1.5 opacity-70">{uiText("common.validationError")}</span>
          {explain(issue)}
        </li>
      ))}
    </ul>
  );
}

export function IssueCount({ pointer }: { pointer: string }) {
  const issues = useIssuesUnder(pointer);
  if (issues.length === 0) return null;
  return (
    <Tip label={issues.map(issue => explain(issue)).join('\n')}>
      <span tabIndex={0}>
        <Badge tone="bad">{issues.length} {issues.length === 1 ? uiText("editors.common.fehler_f6cc01") : uiText("editors.common.fehler_f6cc01")}</Badge>
      </span>
    </Tip>
  );
}

export function IconButton({ label, onClick, disabled, disabledReason, children, tone }: { label: string; onClick: () => void; disabled?: boolean; disabledReason?: string; children: ReactNode; tone?: 'danger' }) {
  return (
    <Tip label={label}>
      <Button variant="ghost" size="icon" aria-label={label} disabled={disabled} disabledReason={disabledReason ?? label} onClick={onClick} className={cn(tone === 'danger' && 'hover:text-fg')}>
        {children}
      </Button>
    </Tip>
  );
}

export function RowActions({ index, count, onMove, onRemove, removeDisabled }: { index: number; count: number; onMove: (from: number, to: number) => void; onRemove: () => void; removeDisabled?: boolean }) {
  return (
    <div className="flex shrink-0 items-center">
      {index > 0 && <IconButton label={uiText("editors.common.nach_oben_ed1652")} onClick={() => onMove(index, index - 1)}>
        <ArrowUp size={14} />
      </IconButton>}
      {index < count - 1 && <IconButton label={uiText("editors.common.nach_unten_542457")} onClick={() => onMove(index, index + 1)}>
        <ArrowDown size={14} />
      </IconButton>}
      <IconButton label={uiText("editors.common.entfernen_382837")} tone="danger" disabled={removeDisabled} disabledReason={uiText('alpha7.copy.requiredRow')} onClick={onRemove}>
        <Trash2 size={14} />
      </IconButton>
    </div>
  );
}

export function AddButton({ children, onClick, disabled }: { children: ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={onClick} disabled={disabled}>
      <Plus size={13} />
      {children}
    </Button>
  );
}

/** Editable list of strings (e.g. the value catalog of an enum). */
export function StringListEditor({ value, onChange, pointer, label, placeholder }: { value: string[]; onChange: (next: string[]) => void; pointer: string; label: string; placeholder?: string }) {
  const [draft, setDraft] = useState('');
  const commit = () => {
    const text = draft.trim();
    if (text && !value.includes(text)) onChange([...value, text]);
    setDraft('');
  };
  return (
    <Fld label={label} pointer={pointer}>
      {(props) => (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-1.5">
            {value.map((item, i) => (
              <span key={item + i} className="inline-flex items-center gap-1 rounded-full border border-line-strong bg-raised py-0.5 pl-2.5 pr-1 text-xs">
                <code>{item}</code>
                <button type="button" aria-label={uiText("editors.common.v0_entfernen_92fea7", { v0: (item) })} className="cursor-pointer rounded-full px-1 text-muted hover:text-bad" onClick={() => onChange(value.filter((_, j) => j !== i))}>
                  ×
                </button>
              </span>
            ))}
            {value.length === 0 && <span className="text-xs text-faint">{uiText("editors.common.leer_f13f8e")}</span>}
          </div>
          <div className="flex gap-2">
            <Input
              {...props}
              value={draft}
              placeholder={placeholder ?? uiText("editors.common.wert_enter_a30cc4")}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  commit();
                }
              }}
            />
            <Button variant="outline" onClick={commit} disabled={!draft.trim()} disabledReason={uiText('alpha7.copy.copyNeedsValue')}>
              {uiText("editors.common.hinzufugen_cbb95c")}</Button>
          </div>
        </div>
      )}
    </Fld>
  );
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}
/** ISO instant (with zone) → value for <input type="datetime-local"> in local time. */
export function isoToLocalInput(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** Value editor that follows a FieldSchema: type-specific widgets, never free JSON for scalars. */
export function TypedValueEditor({ schema, value, onChange, pointer, label = uiText('editors.SchemaForm.wert_7e347d') }: { schema: FieldSchema; value: Json; onChange: (next: Json) => void; pointer: string; label?: string }) {
  const catalog = Array.isArray(schema.value_catalog) ? (schema.value_catalog as string[]) : [];
  switch (schema.type) {
    case 'bool':
      return (
        <Fld label={label} pointer={pointer}>
          {(props) => (
            <Select {...props} value={String(value)} onChange={(e) => onChange(e.target.value === 'true')}>
              <option value="true">{uiText("editors.common.wahr_167f97")}</option>
              <option value="false">{uiText("editors.common.falsch_b3c3e0")}</option>
            </Select>
          )}
        </Fld>
      );
    case 'number':
      return <NumberField label={label} pointer={pointer} value={typeof value === 'number' ? value : 0} onChange={onChange} />;
    case 'enum':
      return (
        <Fld label={label} pointer={pointer}>
          {(props) => (
            <Select {...props} value={String(value)} onChange={(e) => onChange(e.target.value)}>
              {!catalog.includes(String(value)) && <option value={String(value)}>{String(value) || '—'} {uiText("editors.common.nicht_im_katalog_fa85e6")}</option>}
              {catalog.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </Select>
          )}
        </Fld>
      );
    case 'timestamp':
      return (
        <Fld label={label} pointer={pointer} hint={uiText("editors.common.zeitpunkt_mit_zeitzone_rfc_3339_z_b_2026_01_01t1_ccb975")}>
          {(props) => (
            <div className="flex gap-2">
              <Input {...props} value={String(value)} onChange={(e) => onChange(e.target.value)} className="font-mono text-xs" />
              <Input
                type="datetime-local"
                step={1}
                aria-label={uiText("editors.common.v0_kalender_4c1966", { v0: (label) })}
                className="w-52"
                value={isoToLocalInput(String(value))}
                onChange={(e) => e.target.value && onChange(new Date(e.target.value).toISOString().replace('Z', '+00:00'))}
              />
            </div>
          )}
        </Fld>
      );
    case 'object':
      // Object-typed values are any JSON value except null: structured, graphical editing.
      return <ValueEditor label={label} pointer={pointer} value={value} onChange={onChange} allowedKinds={['string', 'number', 'boolean', 'object', 'array']} />;
    default:
      return (
        <Fld label={label} pointer={pointer}>
          {(props) => <Input {...props} value={typeof value === 'string' ? value : ''} onChange={(e) => onChange(e.target.value)} />}
        </Fld>
      );
  }
}

/** Number input that tolerates intermediate states ("", "-", "1.") while typing. */
export function NumberField({ label, pointer, value, onChange, hint, min, max, step }: { label: ReactNode; pointer: string; value: number; onChange: (next: number) => void; hint?: ReactNode; min?: number; max?: number; step?: number }) {
  const [text, setText] = useState(String(value));
  const minutes = /(?:_s|_seconds)$/.test(pointer) ? minuteHint(value) : undefined;
  useEffect(() => {
    if (Number(text) !== value) setText(String(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  if (/(?:_s|_seconds)$/.test(pointer) && (/\/sources\//.test(pointer) || /grace_s$/.test(pointer) || /max_age_s$/.test(pointer))) return <DurationField label={label} value={value} onChange={onChange} min={min ?? 0} max={max ?? (/grace_s$/.test(pointer) ? 31536000 : undefined)} integerSeconds={step === 1} />;
  return (
    <Fld label={label} pointer={pointer} hint={minutes && hint !== minutes ? <>{hint}{hint && ' · '}{minutes}</> : hint}>
      {(props) => (
        <Input
          {...props}
          type="number"
          inputMode="decimal"
          min={min}
          step={step ?? 'any'}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            const parsed = Number(e.target.value);
            if (e.target.value.trim() !== '' && Number.isFinite(parsed)) onChange(parsed);
          }}
        />
      )}
    </Fld>
  );
}

/** Raw JSON for a subtree (expert mode and object-typed values); invalid text never overwrites the document. */
export function JsonValueEditor({ label, pointer, value, onChange, rows = 6, hint }: { label: ReactNode; pointer: string; value: Json; onChange: (next: Json) => void; rows?: number; hint?: ReactNode }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    try {
      if (JSON.stringify(JSON.parse(text)) !== JSON.stringify(value)) setText(JSON.stringify(value, null, 2));
    } catch {
      /* keep the user's in-progress text */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <Fld label={label} pointer={pointer} hint={hint}>
      {(props) => (
        <div className="flex flex-col gap-1">
          <Textarea
            {...props}
            rows={rows}
            spellCheck={false}
            value={text}
            aria-invalid={props['aria-invalid'] || error !== null}
            onChange={(e) => {
              setText(e.target.value);
              try {
                const parsed = JSON.parse(e.target.value) as Json;
                setError(null);
                onChange(parsed);
              } catch {
                setError(uiText("common.invalidJson"));
              }
            }}
          />
          {error && <p role="alert" className="text-xs text-bad">{uiText("editors.common.ungultiges_json_anderung_noch_nicht_ubernommen_ffa9c9")}{error}</p>}
        </div>
      )}
    </Fld>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{children}</h3>
      {aside}
    </div>
  );
}

export function isNonEmptyObject(value: unknown): value is Record<string, Json> {
  return isObject(value) && Object.keys(value).length > 0;
}
