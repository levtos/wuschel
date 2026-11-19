import { t } from '@/i18n';
import { asArray, str } from '@/features/authoring/model';
import { bindingHref, definitionHref, ObjectReadLink } from '@/features/authoring/ObjectReadLinks';
import { isObject, type JsonObject } from '@/lib/json';
import type { Registry } from '@/lib/types';
import type { ContractSource } from './presentation';

export function ContractTemplateLink({ contract, config, showVersion = false }: { contract: JsonObject; config: Registry | null; showVersion?: boolean }) {
  const id = str(contract.type_id);
  const version = Number(contract.type_version);
  if (!id || !version) return null;
  const definition = asArray(config?.definitions).filter(isObject).find(row => row.type_id === id && row.type_version === version);
  return <span className="inline-flex flex-wrap items-center gap-x-2">
    <ObjectReadLink to={definitionHref(id, version)}>{showVersion ? t('a2.read.templateVersion', { name: str(definition?.display_name) || id, version }) : t('a2.read.viewTemplate')}</ObjectReadLink>
    {showVersion && <span className="text-xs text-muted">{t(definition ? 'a2.read.ownTemplate' : 'a2.read.packageTemplate')}</span>}
  </span>;
}

export function InputReadLinks({ input, config, includeTarget = true }: { input: ContractSource; config: Registry | null; includeTarget?: boolean }) {
  const contract = input.kind === 'contract' ? asArray(config?.contracts).filter(isObject).find(row => row.contract_id === input.id) : undefined;
  return <span className="inline-flex flex-wrap gap-x-3 gap-y-1">
    {includeTarget && <ObjectReadLink to={input.href}>{t(input.kind === 'source' ? 'a2.read.viewSource' : 'a2.read.viewContract')}</ObjectReadLink>}
    {input.kind === 'source' && asArray(config?.bindings).filter(isObject).filter(row => row.source_id === input.id).map(row =>
      <ObjectReadLink key={str(row.binding_id)} to={bindingHref(str(row.binding_id))}>{str(row.display_name) || t('a2.read.viewBinding')}</ObjectReadLink>)}
    {contract && <ContractTemplateLink contract={contract} config={config} />}
  </span>;
}
