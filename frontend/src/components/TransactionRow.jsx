export default function TransactionRow({ transaction }) {
    const sign = transaction.direction === 'credit' ? '+' : '-'
    return <div className="row"><div className={`row-type ${transaction.direction}`}><strong>{transaction.direction}</strong></div><div><strong>{transaction.type}</strong><br /><small className="mono">{transaction.transaction_id}</small></div><div className={transaction.direction}><strong>{sign}{transaction.amount}</strong></div><small>{new Date(transaction.created_at).toLocaleString()}</small></div>
}
