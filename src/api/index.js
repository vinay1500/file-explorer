// src/api/index.js
import axios from 'axios';

const api = axios.create({
  baseURL: 'http://vinay1234.pythonanywhere.com /api',
});

export default api;
