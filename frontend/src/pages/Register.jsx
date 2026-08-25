import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Register() {
    const { register } = useAuth(); const navigate = useNavigate(); const [form, setForm] = useState({ email: '', name: '', password: '' }); const [error, setError] = useState('')
    const submit = async (event) => { event.preventDefault(); setError(''); try { await register(form); navigate('/login') } catch (err) { setError(err.response?.data?.detail?.message || 'Unable to create account') } }
    return <div className="auth"><div className="auth-card"><div className="eyebrow">Payment ledger / registration</div><h1>Open your wallet.</h1><p className="muted">Your balance will always come from the ledger, never a mutable number.</p><form onSubmit={submit}><label>Name<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label><label>Email<input type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label><label>Password<input type="password" minLength="6" required value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>{error && <div className="error">{error}</div>}<button className="primary">Create account</button></form><p className="muted">Already registered? <Link className="auth-link" to="/login">Sign in</Link></p></div></div>
}
