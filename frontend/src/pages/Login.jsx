import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Login() {
    const { login } = useAuth(); const navigate = useNavigate(); const [form, setForm] = useState({ email: '', password: '' }); const [error, setError] = useState('')
    const submit = async (event) => { event.preventDefault(); setError(''); try { await login(form.email, form.password); navigate('/') } catch (err) { setError(err.response?.data?.detail?.message || 'Unable to sign in') } }
    return <div className="auth"><div className="auth-card"><div className="eyebrow">Payment ledger / access</div><h1>Move money with a paper trail.</h1><p className="muted">A small wallet interface for a serious accounting engine.</p><form onSubmit={submit}><label>Email<input type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label><label>Password<input type="password" required value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>{error && <div className="error">{error}</div>}<button className="primary">Sign in</button></form><p className="muted">New here? <Link className="auth-link" to="/register">Create an account</Link></p></div></div>
}
