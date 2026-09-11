import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Page from '../app/page';
import { api, premiumApi } from '../lib/api';
vi.mock('../lib/api',()=>({api:{status:vi.fn(),settings:vi.fn(),dashboard:vi.fn(),metrics:vi.fn(),errors:vi.fn()},premiumApi:{capabilities:vi.fn()},waitForAction:vi.fn()}));
beforeEach(()=>{Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:vi.fn(),setItem:vi.fn()}});vi.resetAllMocks();vi.mocked(api.status).mockResolvedValue({configured:true,authenticated:true});vi.mocked(api.settings).mockResolvedValue({revision:0} as never);vi.mocked(api.dashboard).mockResolvedValue({settings_revision:0,channel_count:0,operations:[]});vi.mocked(api.errors).mockResolvedValue([]);window.history.replaceState({},'','/');});
afterEach(cleanup);
it('hides Premium navigation when its backend routes are disabled',async()=>{vi.mocked(premiumApi.capabilities).mockResolvedValue(null);render(<Page/>);const nav=await screen.findByRole('navigation');expect(within(nav).queryByRole('button',{name:'Premium поиск'})).not.toBeInTheDocument();});
it('adds Premium navigation when capabilities are enabled',async()=>{vi.mocked(premiumApi.capabilities).mockResolvedValue({enabled:true,publisher_configured:false,tracks:[]});render(<Page/>);expect(await screen.findByRole('button',{name:'Premium поиск'})).toBeInTheDocument();});
