import { Link } from 'react-router';
import { t } from '@/i18n';
import { asArray, str } from '@/features/authoring/model';
import { useAuthoringSource } from '@/features/authoring/useAuthoringSource';
import { isObject } from '@/lib/json';
import { Empty, PageHeader, Panel } from '@/components/ui/layout';

export default function ContractChooser() {
  const source = useAuthoringSource();
  const contracts = asArray(source.config?.contracts).filter(isObject);
  return <>
    <PageHeader title={t('alpha7.nav.contract')} description={t('alpha7.contract.choose')} />
    <Panel><ul className="divide-y divide-line">{contracts.map(contract => <li key={str(contract.contract_id)}>
      <Link className="block whitespace-normal px-5 py-4 hover:bg-raised [overflow-wrap:anywhere]" to={`/contracts/view/${encodeURIComponent(str(contract.contract_id))}`}>{str(contract.display_name) || str(contract.contract_id)}</Link>
    </li>)}</ul>{!contracts.length && <Empty title={t('alpha7.contract.empty')}><Link to="/contracts/list/all" className="text-fg underline">{t('alpha7.nav.mine')}</Link></Empty>}</Panel>
  </>;
}
