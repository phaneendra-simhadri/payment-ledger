import { createContext, useContext, useState } from 'react'
import client from '../api/client'

const AuthContext = createContext(null)
export function AuthProvider({ children }) {
    const [token, setToken] = useState(sessionStorage.getItem('ledger_token'))
    const [user, setUser] = useState(null)
    const login = async (email, password) => {
        const { data } = await client.post('/auth/login', { email, password })
        sessionStorage.setItem('ledger_token', data.access_token)
        setToken(data.access_token)
    }
    const register = async (payload) => client.post('/auth/register', payload)
    const logout = () => { sessionStorage.removeItem('ledger_token'); setToken(null); setUser(null) }
    return <AuthContext.Provider value={{ token, user, setUser, login, register, logout }}>{children}</AuthContext.Provider>
}
export const useAuth = () => useContext(AuthContext)
