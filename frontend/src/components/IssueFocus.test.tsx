import { render, screen, waitFor } from '@testing-library/react';
import { useEffect, useState } from 'react';
import { MemoryRouter } from 'react-router';
import { expect, it } from 'vitest';
import { IssueFocus } from './IssueFocus';

it('focuses the addressed field when its editor mounts asynchronously', async () => {
  function Page() {
    const [ready, setReady] = useState(false);
    useEffect(() => { setReady(true); }, []);
    return <>{ready && <div data-pointer="/sources/1/freshness/mode"><input aria-label="Ziel" /></div>}<IssueFocus /></>;
  }
  render(<MemoryRouter initialEntries={['/sources?focus=%2Fsources%2F1%2Ffreshness%2Fmode']}><Page /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Ziel' })).toHaveFocus());
});

it('waits for editing to enable the field and opens its details', async () => {
  function Page() {
    const [disabled, setDisabled] = useState(true);
    useEffect(() => { setDisabled(false); }, []);
    return <><fieldset disabled={disabled}><details><summary>Details</summary><div data-pointer="/contracts/0/inputs"><input aria-label="Verknüpfung" /></div></details></fieldset><IssueFocus /></>;
  }
  render(<MemoryRouter initialEntries={['/contract?focus=%2Fcontracts%2F0%2Finputs']}><Page /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Verknüpfung' })).toHaveFocus());
});
