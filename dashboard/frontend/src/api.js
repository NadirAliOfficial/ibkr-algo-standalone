import axios from "axios";

const creds = btoa(
  `${process.env.REACT_APP_DASH_USER || "admin"}:${process.env.REACT_APP_DASH_PASS || "changeme"}`
);

const api = axios.create({
  baseURL: "/api",
  headers: { Authorization: `Basic ${creds}` },
});

export const getTickers = () => api.get("/tickers/").then((r) => r.data);
export const addTicker = (payload) => api.post("/tickers/", payload).then((r) => r.data);
export const updateTicker = (ticker, payload) => api.put(`/tickers/${ticker}`, payload).then((r) => r.data);
export const deleteTicker = (ticker) => api.delete(`/tickers/${ticker}`).then((r) => r.data);
export const bulkEdit = (payload) => api.post("/tickers/bulk-edit", payload).then((r) => r.data);

export const getPositions = () => api.get("/positions/").then((r) => r.data);
export const getSignalLog = () => api.get("/logs/signals").then((r) => r.data);
export const getTradeLog = () => api.get("/logs/trades").then((r) => r.data);
export const getStatus = () => api.get("/system/status").then((r) => r.data);
export const clearHalt = (ticker) => api.post(`/system/clear-halt/${ticker}`).then((r) => r.data);
export const getEarningsLog = () => api.get("/logs/earnings").then((r) => r.data);
