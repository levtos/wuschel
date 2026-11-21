import { fieldLabel, validationLabel } from '@/i18n/codes';
import { hasTranslation, t as uiText } from '@/i18n';
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import type { Issue } from '@/lib/api';
import { formatNumber } from '@/lib/format';
import { fromPointer, getAt, isObject, join, type JsonObject } from '@/lib/json';
import { asArray, asObject, str } from './model';

/** Server findings for the document being edited, addressed by JSON Pointer. */
const IssuesContext = createContext<Issue[]>([]);
export function IssuesProvider({ value, config, children }: { value: Issue[]; config?: JsonObject | null; children: ReactNode }) {
  const findings = useMemo(() => value.map(issue => parameterRelation(issue, config)), [value, config]);
  return <IssuesContext.Provider value={findings}>{children}</IssuesContext.Provider>;
}

function parameterRelation(issue: Issue, config?: JsonObject | null): Issue {
  if (!config || issue.code !== 'parameter.parameter_relation_failed' || !issue.parameters?.left || !issue.parameters.right) return issue;
  const [, index] = fromPointer(issue.path);
  const contract = asObject(getAt(config, `/contracts/${index}`));
  const definition = asArray(config.definitions).filter(isObject).find(row => row.type_id === contract.type_id && row.type_version === contract.type_version);
  const parameterSchema = asObject(definition?.parameter_schema);
  const labelValue = (field: string) => {
    const declaration = asObject(parameterSchema[field]);
    const name = str(declaration.label) || fieldLabel(field);
    const value = getAt(contract, join('/parameters', field)) ?? declaration.default;
    if (value === undefined || value === null) return uiText('a2.edit.emptyParameter', { name });
    const unit = str(asObject(declaration.schema).unit);
    const shown = typeof value === 'number' ? formatNumber(value) : typeof value === 'boolean' ? uiText(value ? 'alpha7.device.yes' : 'alpha7.device.no') : String(value);
    return `${name} (${shown}${unit ? ` ${unit}` : ''})`;
  };
  return { ...issue, parameters: { ...issue.parameters, leftText: labelValue(issue.parameters.left), rightText: labelValue(issue.parameters.right) } };
}

/** Findings addressed to exactly this pointer. */
export function useIssues(pointer: string): Issue[] {
  const all = useContext(IssuesContext);
  return useMemo(() => all.filter((issue) => issue.path === pointer), [all, pointer]);
}

/** Findings at or below a pointer, for container badges. */
export function useIssuesUnder(pointer: string): Issue[] {
  const all = useContext(IssuesContext);
  return useMemo(() => all.filter((issue) => issue.path === pointer || issue.path.startsWith(pointer + '/')), [all, pointer]);
}

export function useAllIssues(): Issue[] {
  return useContext(IssuesContext);
}

/** Display text for a server code; the code itself stays visible next to it. */
const TEXT: Record<string, string> = {
  duplicate_identity: uiText("features.authoring.issues.diese_id_ist_bereits_vergeben_20f34e"),
  immutable_definition_version: uiText("features.authoring.issues.diese_definition_ist_als_version_bereits_aktivie_d64870"),
  input_type_mismatch: uiText("features.authoring.issues.typ_katalog_oder_einheit_passen_nicht_zum_erwart_6a5b1f"),
  input_reference: uiText("features.authoring.issues.die_referenz_ist_nicht_eindeutig_oder_unbekannt_3be019"),
  type_reference: uiText("features.authoring.issues.typ_und_version_sind_nicht_registriert_599b2c"),
  contract_cycle: uiText("features.authoring.issues.die_contracts_bilden_einen_zyklus_b02297"),
  source_unbound: uiText("features.authoring.issues.die_quelle_hat_kein_binding_a917c9"),
  binding_source: uiText("features.authoring.issues.das_binding_verweist_auf_keine_vorhandene_quelle_82115e"),
  field_reference: uiText("features.authoring.issues.das_referenzierte_feld_existiert_nicht_aaa234"),
  fingerprint_upgrade_required: uiText("features.authoring.issues.deklarative_definitionen_erfordern_fingerprint_v_04509f"),
  'definition.rule_type_mismatch': uiText("features.authoring.issues.die_typen_von_operator_und_ausgabe_passen_nicht__6c3171"),
  'definition.unknown_rule_input': uiText("features.authoring.issues.der_eingang_ist_in_dieser_definition_nicht_dekla_901b2e"),
  'definition.unknown_internal_reference': uiText("features.authoring.issues.die_interne_regel_existiert_nicht_47d7f3"),
  'definition.internal_cycle': uiText("features.authoring.issues.die_benannten_regeln_verweisen_im_kreis_aufeinan_2f903a"),
  'definition.duplicate_stateful_id': uiText("features.authoring.issues.die_id_eines_bucket_temporal_knotens_muss_in_der_ccd952"),
  'definition.duplicate_transition_event': uiText("features.authoring.issues.pro_quellzustand_und_ereignis_ist_nur_eine_trans_aee3c9"),
  'definition.unreachable_machine': uiText("features.authoring.issues.die_state_machine_wird_von_keiner_ausgabe_verwen_63e564"),
  'definition.stateful_guard_requires_contract_input': uiText("features.authoring.issues.guards_mussen_zustandslos_sein_zeitliche_wahrhei_8935df"),
  'definition.invalid_map_key': uiText("features.authoring.issues.map_schlussel_mussen_kanonische_json_skalare_sei_70986e"),
  'definition.unknown_formula_version': uiText("features.authoring.issues.diese_formel_version_ist_nicht_registriert_8bbb4e"),
  'definition.unknown_fusion_strategy': uiText("features.authoring.issues.diese_fusionsstrategie_ist_nicht_registriert_4a6f9a"),
  'definition.comparison_type_mismatch': uiText("features.authoring.issues.die_operanden_sind_nicht_vergleichbar_250187"),
  'definition.unordered_comparison': uiText("features.authoring.issues.ordnungsvergleiche_gelten_nur_fur_zahl_text_und__d224d3"),
};
export function explain(finding: Issue, config?: JsonObject | null): string {
  const issue = parameterRelation(finding, config);
  if (issue.code === 'parameter.parameter_relation_failed') {
    const data = issue.parameters;
    const relation = `a2.edit.relation.${data?.comparison}`;
    if (data?.left && data.right && hasTranslation(relation)) return uiText(relation, { left: data.leftText ?? fieldLabel(data.left), right: data.rightText ?? fieldLabel(data.right) });
  }
  if (hasTranslation(`validation.${issue.code}`)) return validationLabel(issue);
  if (['input_type_mismatch', 'binding_cast_required', 'binding_type_mismatch', 'source_type_mismatch', 'immutable_source_type', 'immutable_source_catalog', 'contract_cycle'].includes(issue.code)) return validationLabel(issue);
  return TEXT[issue.code] ?? validationLabel(issue);
}
