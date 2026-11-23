import { Panel, PanelHeader } from '@/components/ui/layout';
import { t } from '@/i18n';
import { Fragment } from 'react';

const dualRows = [
  ['yes', 'yes', 'open'], ['yes', 'no', 'conflict'], ['no', 'yes', 'tilted'], ['no', 'no', 'closed'],
  ['yes', 'unknown', 'partial'], ['no', 'unknown', 'unknown'], ['unknown', 'any', 'unknown'],
];
const singleRows = [['yes', 'open'], ['no', 'closed'], ['unknown', 'unknown']];
const zoneFacts = ['any_open', 'any_tilted', 'unclear', 'any_held', 'all_closed'];

export function BuiltInTemplateReading({ typeId }: { typeId: string }) {
  const dual = typeId === 'opening.dual_contact';
  const zone = typeId === 'opening_zone.facts';
  if (!dual && !zone && typeId !== 'opening.single_contact') return null;
  return <Panel className="mt-4 max-w-4xl">
    <PanelHeader title={t('a2.opening.rules')} />
    <div className="space-y-4 p-4 pt-0 text-sm">
      {zone ? <>
        <dl className="grid gap-2 sm:grid-cols-[max-content_1fr]">
          {zoneFacts.map(field => <Fragment key={field}>
            <dt className="font-medium">{t('a2.opening.zone.' + field)}</dt>
            <dd>{t('a2.opening.zone.' + field + '.help')}</dd>
          </Fragment>)}
        </dl>
        <p>{t('a2.opening.zone.quality')}</p>
      </> : <>
        <p>{t('a2.opening.freshInputs')}</p>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <caption className="sr-only">{t('a2.opening.rules')}</caption>
            <thead><tr>{(dual ? ['openInput', 'tiltInput', 'result'] : ['contact', 'result']).map(name => <th key={name} scope="col" className="border-b border-line p-2">{t('a2.opening.' + name)}</th>)}</tr></thead>
            <tbody>{(dual ? dualRows : singleRows).map((row, index) => <tr key={index} className="border-b border-line">
              {row.map((cell, column) => <td key={column} className="p-2">{t('a2.opening.state.' + cell)}</td>)}
            </tr>)}</tbody>
          </table>
        </div>
        <h3 className="font-semibold">{t('a2.opening.time')}</h3>
        <p>{t('a2.opening.hold')}</p>
        {dual && <p>{t('a2.opening.conflict')}</p>}
        <p>{t('a2.opening.restore')}</p>
      </>}
    </div>
  </Panel>;
}
