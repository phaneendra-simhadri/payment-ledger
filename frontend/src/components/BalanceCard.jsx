export default function BalanceCard({ account, balance }) {
    return <div className="panel balance-panel"><div><div className="eyebrow">Available balance / {account.currency}</div><div className="balance">{balance ?? '—'}</div></div><div className="account-id mono">wallet {account.account_id}</div></div>
}
