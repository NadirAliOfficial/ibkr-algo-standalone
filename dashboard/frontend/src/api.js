import axios from "axios";

const STORAGE_KEY = "ibkr_dash_creds";

export function saveCredentials(username, password) {
  localStorage.setItem(STORAGE_KEY, btoa(`${username}:${password}`));
}

export function loadCredentials() {
  return localStorage.getItem(STORAGE_KEY);
}

export function clearCredentials() {
  localStorage.removeItem(STORAGE_KEY);
}

function getApi() {
  const creds = loadCredentials();
  return axios.create({
    baseURL: "/api",
    headers: creds ? { Authorization: `Basic ${creds}` } : {},
  });
}

const call = (fn) => fn().then((r) => r.data);

export const verifyAuth = () => call(() => getApi().get("/auth/verify"));
export const getTickers = () => call(() => getApi().get("/tickers/"));
export const addTicker = (p) => call(() => getApi().post("/tickers/", p));
export const updateTicker = (ticker, p) => call(() => getApi().put(`/tickers/${ticker}`, p));
export const deleteTicker = (ticker) => call(() => getApi().delete(`/tickers/${ticker}`));
export const bulkEdit = (p) => call(() => getApi().post("/tickers/bulk-edit", p));
export const getPositions = () => call(() => getApi().get("/positions/"));
export const getSignalLog = () => call(() => getApi().get("/logs/signals"));
export const getTradeLog = () => call(() => getApi().get("/logs/trades"));
export const getStatus = () => call(() => getApi().get("/system/status"));
export const clearHalt = (ticker) => call(() => getApi().post(`/system/clear-halt/${ticker}`));
export const getEarningsLog = () => call(() => getApi().get("/logs/earnings"));
export const getMarket = () => call(() => getApi().get("/system/market"));
export const getEngineLog = () => call(() => getApi().get("/logs/engine?lines=150"));
