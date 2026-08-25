import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import client from '../api/client'
import BalanceCard from '../components/BalanceCard'

export default function Dashboard() {
    const [accounts, setAccounts] = useState([]); const [balance, setBalance] = useState(null); const [error, setError] = useState(''); const account = accounts[0]
    const load = async () => { try { const { data } = await client.get('/accounts'); setAccounts(data); if (data[0]) { const balanceResponse = await client.get(`/accounts/${data[0].account_id}/balance`); setBalance(balanceResponse.data.balance) } } catch (err) { setError(err.response?.data?.detail?.message || 'Could not load wallet') } }
    useEffect(() => { load() }, [])
    const create = async () => { try { await client.post('/accounts', { currency: 'INR' }); await load() } catch (err) { setError(err.response?.data?.detail?.message || 'Could not create wallet') } }
    return <><div className="eyebrow">Personal wallet / overview</div><h1>Quietly keep the books.</h1><p className="muted">Every movement below is backed by two ledger entries.</p>{error && <div className="error">{error}</div>}{account ? <><div className="dashboard-grid"><BalanceCard account={account} balance={balance} /><div className="panel"><div className="eyebrow">Next action</div><h2>Send money</h2><p className="muted">Create a transfer with a fresh idempotency key, or run the live duplicate-request proof.</p><Link className="primary" style={{ display: 'inline-block', marginTop: 14 }} to="/send">Open transfer desk</Link></div></div><div className="account-strip"><span className="account-chip">{accounts.length} wallet{accounts.length === 1 ? '' : 's'} linked</span><span className="account-chip">currency / {account.currency}</span></div></> : <div className="panel" style={{ marginTop: 42 }}><h2>No wallet yet</h2><p className="muted">Create your first INR wallet to begin.</p><button className="primary" onClick={create}>Create wallet</button></div>}</>
}
