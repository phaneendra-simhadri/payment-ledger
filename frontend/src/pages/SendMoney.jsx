import { useEffect, useState } from 'react'
import { v4 as uuid } from 'uuid'
import client from '../api/client'
import IdempotencyDemo from '../components/IdempotencyDemo'

export default function SendMoney() {
    const [accounts, setAccounts] = useState([]); const [recipient, setRecipient] = useState(''); const [amount, setAmount] = useState('10.00'); const [description, setDescription] = useState(''); const [result, setResult] = useState(null); const [error, setError] = useState(''); const fromAccount = accounts[0]
    useEffect(() => { client.get('/accounts').then(({ data }) => setAccounts(data)).catch(() => setError('Could not load wallet')) }, [])
    const submit = async (event) => { event.preventDefault(); setError(''); setResult(null); try { const { data } = await client.post('/transfers', { from_account_id: fromAccount.account_id, to_account_id: recipient, amount, currency: fromAccount.currency, description }, { headers: { 'Idempotency-Key': uuid() } }); setResult(data) } catch (err) { setError(err.response?.data?.detail?.message || 'Transfer failed') } }
    if (!fromAccount) return <><div className="eyebrow">Transfer desk</div><h1>Create a wallet first.</h1><p className="muted">Return to the overview to open an INR wallet.</p></>
    return <><div className="eyebrow">Transfer desk / {fromAccount.currency}</div><h1>Send money with confidence.</h1><p className="muted">The sender is locked before balance is checked. The ledger records the movement atomically.</p><div className="dashboard-grid"><div className="panel"><form onSubmit={submit}><label>Recipient account ID<input required value={recipient} onChange={e => setRecipient(e.target.value)} placeholder="uuid" /></label><label>Amount<input required pattern="^\d+(\.\d{1,2})?$" value={amount} onChange={e => setAmount(e.target.value)} /></label><label>Description <span className="muted">optional</span><input value={description} onChange={e => setDescription(e.target.value)} /></label>{error && <div className="error">{error}</div>}{result && <div className="success">Transfer completed: <span className="mono">{result.transaction_id}</span></div>}<button className="primary">Send transfer</button></form></div><IdempotencyDemo fromAccount={fromAccount.account_id} toAccount={recipient} amount={amount} /></div></>
}
