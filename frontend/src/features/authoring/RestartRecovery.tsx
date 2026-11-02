import { useEffect, useRef, useState } from 'react';
import { t } from '@/i18n';
import { Notice } from '@/components/ui/layout';
import { browserSession, reloadAfterRestart } from '@/lib/browser-session';
import { useLive } from '@/lib/live-context';
import { useWorkingDocument } from './WorkingDocument';

/** Preserve begun document edits before the one automatic restart reload. A
 * conflict or failed save keeps the document open for the existing recovery UI. */
export function RestartRecovery() {
  const live = useLive(); const doc = useWorkingDocument();
  const previous = useRef(live.epochId);
  const attempted = useRef<string | null>(null);
  const reloading = useRef<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (previous.current && live.epochId && previous.current !== live.epochId) {
      setPending(live.epochId); setFailed(false);
    }
    if (live.epochId) previous.current = live.epochId;
  }, [live.epochId]);
  useEffect(() => {
    if (!pending || doc.saving || browserSession.getSnapshot().blocked) return;
    if (!doc.dirty) {
      if (reloading.current !== pending) { reloading.current = pending; reloadAfterRestart(pending); }
      return;
    }
    if (attempted.current === pending) return;
    attempted.current = pending;
    void doc.save().then(result => { if (result !== 'saved') setFailed(true); }).catch(() => setFailed(true));
  }, [pending, doc.dirty, doc.saving, doc.save]);
  return pending && failed && !browserSession.getSnapshot().blocked ? <Notice tone="warn">{t('b22.connection.keepEdits')}</Notice> : null;
}
