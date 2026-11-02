import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './index.css';
import { t } from './i18n';
import { ApiError } from './lib/api';

document.title = t('product.name');

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: (count, error) => !(error instanceof ApiError && error.kind === 'auth') && count < 1, refetchOnWindowFocus: false, staleTime: 2_000 },
    mutations: { retry: false },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
