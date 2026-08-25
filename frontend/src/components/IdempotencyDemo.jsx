import { useState } from 'react'
import { v4 as uuid } from 'uuid'
import client from '../api/client'

export default function IdempotencyDemo({ fromAccount, toAccount, amount }) {
    const [responses, setResponses] = useState([]); const [running, setRunning] = useState(false); const [error, setError] = useState('')
    const prove = async () => { setRunning(true); setError(''); setResponses([]); const key = uuid(); const payload = { from_account_id: fromAccount, to_account_id: toAccount, amount, currency: 'INR' }; try { const results = await Promise.all([client.post('/transfers', payload, { headers: { 'Idempotency-Key': key } }), client.post('/transfers', payload, { headers: { 'Idempotency-Key': key } })]); setResponses(results.map(result => result.data)) } catch (err) { setError(err.response?.data?.detail?.message || 'Both requests did not complete') } finally { setRunning(false) } }
    return <div className="panel"><div className="eyebrow">Concurrency proof</div><h2>Prove idempotency</h2><p className="muted">Two identical requests. One key. One transaction.</p><button className="secondary" disabled={running || !fromAccount || !toAccount} onClick={prove}>{running ? 'Sending twice...' : 'Prove idempotency'}</button>{error && <div className="error" style={{ marginTop: 16 }}>{error}</div>}{responses.length > 0 && <div className="account-strip">{responses.map((response, index) => <div className="account-chip" key={index}>response {index + 1}<br /><strong>{response.transaction_id}</strong><br />{response.status}</div>)}</div>}</div>
}
