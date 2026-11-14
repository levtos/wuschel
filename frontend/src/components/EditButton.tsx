import { Pencil } from 'lucide-react';
import { t } from '@/i18n';
import { Button, type ButtonProps } from './ui/button';
import { Tip } from './ui/controls';

/** One quiet, keyboard-accessible action for object rows. */
export function EditButton(props: Omit<ButtonProps, 'children'>) {
  return <Tip label={t('b22.object.edit')}><Button variant="ghost" size="icon" aria-label={t('b22.object.edit')} {...props}><Pencil size={16} aria-hidden="true" /></Button></Tip>;
}
