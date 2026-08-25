import axios from 'axios'

const client = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1' })
client.interceptors.request.use((config) => {
    const token = sessionStorage.getItem('ledger_token')
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
})
export default client
