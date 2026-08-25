import { useEffect, useState } from 'react'
import client from '../api/client'
import TransactionRow from '../components/TransactionRow'

export default function History() {
    const [accounts, setAccounts] = useState([]); const [data, setData] = useState({ transactions: [], total: 0 }); const [page, setPage] = useState(1); const pageSize = 20; const account = accounts[0]
    useEffect(() => { client.get('/accounts').then(({ data }) => setAccounts(data)).catch(() => { }) }, [])
    useEffect(() => { if (account) client.get(`/accounts/${account.account_id}/transactions`, { params: { page, page_size: pageSize } }).then(({ data }) => setData(data)) }, [account, page])
    if (!account) return <><div className="eyebrow">Ledger / history</div><h1>No wallet history.</h1><p className="muted">Create a wallet from the overview first.</p></>
    return <><div className="section-head"><div><div className="eyebrow">Ledger / history</div><h1>Every entry accounted for.</h1></div><span className="account-chip">{data.total} total</span></div><div className="rows">{data.transactions.length ? data.transactions.map(item => <TransactionRow key={item.transaction_id} transaction={item} />) : <div className="panel"><p className="muted">No transactions yet.</p></div>}</div><div className="pagination"><button className="secondary" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button><span className="mono">page {page}</span><button className="secondary" disabled={page * pageSize >= data.total} onClick={() => setPage(page + 1)}>Next</button></div></>
}
