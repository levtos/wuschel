import { t as uiText } from '@/i18n';
import { Checkbox, Collapsible } from '@/components/ui/controls';
import { Input } from '@/components/ui/form';
import { InlineHelp } from '@/components/ui/inline-help';
import { AddButton, Fld, IconButton, IssueCount, IssuesAt, SectionTitle } from './common';
import { FieldSchemaEditor } from './FieldSchemaEditor';
import { asArray, asObject, newFieldSchema, renameKey, type FieldSchema } from '@/features/authoring/model';
import { join, type Json, type JsonObject } from '@/lib/json';
import { Trash2 } from 'lucide-react';
import { Extras } from './Extras';

/** Output schema of a definition: independently typed fields + the fields that define "available". */
export function SchemaEditor({
  value,
  onChange,
  pointer,
  onRenameField,
  onRemoveField,
  onAddField,
  extra,
}: {
  value: JsonObject;
  onChange: (next: JsonObject) => void;
  pointer: string;
  /** Called so the owner can keep `outputs` (one rule per field) in sync. */
  onRenameField: (from: string, to: string) => void;
  onRemoveField: (name: string) => void;
  onAddField: (name: string) => void;
  /** Additional content per field (the output rule). */
  extra?: (name: string) => React.ReactNode;
}) {
  const fields = asObject(value.fields) as Record<string, FieldSchema>;
  const projection = asArray(value.available_projection).map(String);
  const names = Object.keys(fields);
  const setProjection = (next: string[]) => onChange({ ...value, available_projection: next });

  return (
    <div className="flex flex-col gap-3">
      <SectionTitle
        aside={
          <AddButton
            onClick={() => {
              let n = names.length + 1;
              while (names.includes(`feld${n}`)) n += 1;
              onAddField(`feld${n}`);
            }}
          >
            {uiText("editors.SchemaEditor.ausgabefeld_894864")}</AddButton>
        }
      >
        {uiText("editors.SchemaEditor.ausgabefelder_e646ec")}</SectionTitle>
      <IssuesAt pointer={pointer} />
      {names.map((name) => {
        const fp = join(pointer, 'fields', name);
        return (
          <Collapsible key={name} defaultOpen title={<code>{name}</code>} aside={<><IssueCount pointer={fp} /><IconButton label={uiText("editors.SchemaEditor.feld_v0_entfernen_3a5487", { v0: (name) })} tone="danger" disabled={names.length <= 1} onClick={() => onRemoveField(name)}><Trash2 size={14} /></IconButton></>}>
            <div className="flex flex-col gap-3">
              <Fld label={uiText("editors.SchemaEditor.feldname_bc85f6")} pointer={fp}>
                {(props) => (
                  <Input
                    {...props}
                    defaultValue={name}
                    key={name}
                    onBlur={(e) => {
                      const to = e.target.value.trim();
                      if (to && to !== name && !names.includes(to)) onRenameField(name, to);
                      else e.target.value = name;
                    }}
                  />
                )}
              </Fld>
              <FieldSchemaEditor
                value={fields[name] ?? newFieldSchema()}
                pointer={fp}
                onChange={(next) => onChange({ ...value, fields: { ...fields, [name]: next as unknown as Json } as JsonObject })}
              />
              <div className="flex items-center gap-2"><Checkbox
                checked={projection.includes(name)}
                onCheckedChange={(on) => setProjection(on ? [...projection, name] : projection.filter((p) => p !== name))}
                label={uiText("editors.SchemaEditor.zahlt_zur_verfugbarkeit_available_projection_0fd998")}
              /><InlineHelp text={uiText('a2.help.availability')} /></div>
              {extra?.(name)}
            </div>
          </Collapsible>
        );
      })}
      <Extras def="Schema" value={value} pointer={pointer} onChange={onChange} />
    </div>
  );
}

export { renameKey };
