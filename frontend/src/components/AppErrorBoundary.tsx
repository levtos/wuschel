import { Component, type ErrorInfo, type ReactNode } from 'react';
import { t } from '@/i18n';
import { Button } from './ui/button';

/** Includes providers and setup, which live outside the router's error handling. */
export class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  componentDidCatch(error: Error, info: ErrorInfo) { console.error(error, info); }

  render() {
    if (this.state.failed) return <main className="mx-auto max-w-2xl space-y-4 p-10" role="alert">
      <p>{t('b26.app.failed')}</p>
      <Button onClick={() => window.location.reload()}>{t('b26.app.reload')}</Button>
    </main>;
    return this.props.children;
  }
}
