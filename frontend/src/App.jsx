import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from './context/AuthContext'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import SendMoney from './pages/SendMoney'
import History from './pages/History'

function Shell() {
    const { token, logout } = useAuth(); const location = useLocation(); const navigate = useNavigate()
    if (!token && !['/login', '/register'].includes(location.pathname)) return <Navigate to="/login" replace />
    if (token && ['/login', '/register'].includes(location.pathname)) return <Navigate to="/" replace />
    if (!token) return <Routes><Route path="/login" element={<Login />} /><Route path="/register" element={<Register />} /><Route path="*" element={<Navigate to="/login" replace />} /></Routes>
    return <div className="app-shell"><header><Link className="brand" to="/">ledger<span>/</span></Link><nav><Link className={location.pathname === '/' ? 'active' : ''} to="/">Overview</Link><Link className={location.pathname === '/send' ? 'active' : ''} to="/send">Send money</Link><Link className={location.pathname === '/history' ? 'active' : ''} to="/history">History</Link></nav><button className="quiet-button" onClick={() => { logout(); navigate('/login') }}>Sign out</button></header><main><Routes><Route path="/" element={<Dashboard />} /><Route path="/send" element={<SendMoney />} /><Route path="/history" element={<History />} /><Route path="*" element={<Navigate to="/" replace />} /></Routes></main></div>
}
export default Shell
