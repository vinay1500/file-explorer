import axios from 'axios';

const api = axios.create({
  baseURL: 'https://file-explorer-backend.onrender.com/api',
});

export default api;
