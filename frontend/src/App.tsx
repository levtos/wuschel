import { t as uiText } from '@/i18n';
import { useEffect, useState } from 'react';
import { PageLoading } from '@/components/PageLoading';
import { SetupWizard } from '@/setup/SetupWizard';
import { setupRequest, type SetupStatus } from '@/setup/setup';
import { Workbench } from '@/Workbench';
import { ConnectionNotice } from '@/components/ConnectionNotice';

/**
 * The setup status is asked first: before an installation identity exists only
 * the Ingress-only setup routes answer, so nothing else is requested until the
 * assistant is finished (or the operator deliberately opens the workbench).
 */
export function App() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [wizard, setWizard] = useState(true);
  const [manual, setManual] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setupRequest<SetupStatus>('status')
      .then((value) => {
        if (cancelled) return;
        setStatus(value);
        setWizard(value.phase !== 'completed' || !value.onboarding_complete);
      })
      .catch(() => {
        if (!cancelled) setError(uiText("App.einrichtungsstatus_nicht_erreichbar_weboberflach_04e47b"));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <main className="mx-auto max-w-2xl p-10">
        <PageLoading loading={!error} />
        <ConnectionNotice />
        <p role="alert" className="mt-3 text-bad">{error}</p>
      </main>
    );
  }
  if (!status) {
    return (
      <main className="mx-auto max-w-2xl p-10">
        <PageLoading loading={!error} />
      </main>
    );
  }
  if (wizard) {
    return <><ConnectionNotice /><SetupWizard initial={status} automatic={!manual} oncomplete={() => setWizard(false)} /></>;
  }
  return (
    <Workbench
      onSetup={() => {
        setManual(true);
        setWizard(true);
      }}
    />
  );
}
