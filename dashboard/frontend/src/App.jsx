import { useState } from "react";
import { QueryClient, QueryClientProvider, useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast, { Toaster } from "react-hot-toast";
import { getTickers, addTicker, updateTicker, deleteTicker, getPositions, getSignalLog, getTradeLog, getStatus } from "./api";

const qc = new QueryClient();

const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1H", "2H", "4H", "1D"];
const STATE_COLORS = { WAITING: "bg-gray-500", LONG: "bg-green-600", SHORT: "bg-red-600", BLOCKED: "bg-yellow-600" };

function StatusBar() {
  const { data } = useQuery({ queryKey: ["status"], queryFn: getStatus, refetchInterval: 10000 });
  if (!data) return null;
  return (
    <div className="flex gap-6 text-sm px-4 py-2 bg-gray-900 border-b border-gray-700 text-gray-300">
      <span>Engine: <b className={data.engine_running ? "text-green-400" : "text-red-400"}>{data.engine_running ? "RUNNING" : "STOPPED"}</b></span>
      <span>IBKR: <b className={data.ibkr_connected ? "text-green-400" : "text-red-400"}>{data.ibkr_connected ? "CONNECTED" : "DISCONNECTED"}</b></span>
      <span>Active: <b className="text-white">{data.active_tickers}</b></span>
      {data.halted_tickers?.length > 0 && <span className="text-yellow-400">Halted: {data.halted_tickers.join(", ")}</span>}
    </div>
  );
}

function WatchlistManager() {
  const qclient = useQueryClient();
  const { data: tickers = [] } = useQuery({ queryKey: ["tickers"], queryFn: getTickers, refetchInterval: 5000 });
  const [newTicker, setNewTicker] = useState({ ticker: "", dollar_amount: 1000, timeframe: "1H", active: true, order_type: "market", indicator_params: {} });
  const [selected, setSelected] = useState([]);

  const addMut = useMutation({ mutationFn: addTicker, onSuccess: () => { qclient.invalidateQueries(["tickers"]); toast.success("Ticker added"); } });
  const delMut = useMutation({ mutationFn: deleteTicker, onSuccess: () => { qclient.invalidateQueries(["tickers"]); toast.success("Removed"); } });
  const updMut = useMutation({ mutationFn: ({ ticker, payload }) => updateTicker(ticker, payload), onSuccess: () => qclient.invalidateQueries(["tickers"]) });

  const stateLabel = (cfg) => {
    if (cfg.blocked) return "BLOCKED";
    const s = cfg.state?.toUpperCase();
    if (s === "FLAT") return "WAITING";
    return s || "WAITING";
  };

  return (
    <div className="p-4">
      <h2 className="text-lg font-semibold mb-3">Watchlist ({tickers.length} / 50)</h2>

      {/* Add ticker form */}
      <div className="flex gap-2 mb-4 flex-wrap">
        <input className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm w-24 uppercase" placeholder="TICKER"
          value={newTicker.ticker} onChange={e => setNewTicker(p => ({ ...p, ticker: e.target.value.toUpperCase() }))} />
        <input type="number" className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm w-24" placeholder="$ Amount"
          value={newTicker.dollar_amount} onChange={e => setNewTicker(p => ({ ...p, dollar_amount: +e.target.value }))} />
        <select className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm"
          value={newTicker.timeframe} onChange={e => setNewTicker(p => ({ ...p, timeframe: e.target.value }))}>
          {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
        </select>
        <button className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded text-sm"
          onClick={() => addMut.mutate(newTicker)}>Add</button>
      </div>

      {/* Ticker table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-gray-400 border-b border-gray-700">
            <tr>
              <th className="text-left py-1 pr-3"><input type="checkbox" onChange={e => setSelected(e.target.checked ? tickers.map(t => t.ticker) : [])} /></th>
              <th className="text-left py-1 pr-3">Ticker</th>
              <th className="text-left py-1 pr-3">Active</th>
              <th className="text-left py-1 pr-3">$ Amount</th>
              <th className="text-left py-1 pr-3">Timeframe</th>
              <th className="text-left py-1 pr-3">Status</th>
              <th className="text-left py-1">Actions</th>
            </tr>
          </thead>
          <tbody>
            {tickers.map(cfg => (
              <tr key={cfg.ticker} className="border-b border-gray-800 hover:bg-gray-800/50">
                <td className="py-1 pr-3"><input type="checkbox" checked={selected.includes(cfg.ticker)} onChange={e => setSelected(p => e.target.checked ? [...p, cfg.ticker] : p.filter(t => t !== cfg.ticker))} /></td>
                <td className="py-1 pr-3 font-mono font-semibold">{cfg.ticker}</td>
                <td className="py-1 pr-3">
                  <input type="checkbox" checked={cfg.active} onChange={e => updMut.mutate({ ticker: cfg.ticker, payload: { ...cfg, active: e.target.checked } })} />
                </td>
                <td className="py-1 pr-3">
                  <input type="number" className="bg-gray-800 border border-gray-600 rounded px-1 w-20 text-sm" value={cfg.dollar_amount}
                    onBlur={e => updMut.mutate({ ticker: cfg.ticker, payload: { ...cfg, dollar_amount: +e.target.value } })}
                    onChange={() => {}} />
                </td>
                <td className="py-1 pr-3">
                  <select className="bg-gray-800 border border-gray-600 rounded px-1 text-sm" value={cfg.timeframe}
                    onChange={e => updMut.mutate({ ticker: cfg.ticker, payload: { ...cfg, timeframe: e.target.value } })}>
                    {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
                  </select>
                </td>
                <td className="py-1 pr-3">
                  <span className={`text-xs px-2 py-0.5 rounded text-white ${STATE_COLORS[stateLabel(cfg)] || "bg-gray-600"}`}>{stateLabel(cfg)}</span>
                </td>
                <td className="py-1">
                  <button className="text-red-400 hover:text-red-300 text-xs" onClick={() => delMut.mutate(cfg.ticker)}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PositionsPanel() {
  const { data: raw_positions } = useQuery({ queryKey: ["positions"], queryFn: getPositions, refetchInterval: 30000 });
  const positions = Array.isArray(raw_positions) ? raw_positions : [];
  return (
    <div className="p-4">
      <h2 className="text-lg font-semibold mb-3">Live Positions ({positions.length})</h2>
      {positions.length === 0 ? <p className="text-gray-500 text-sm">No open positions</p> : (
        <table className="w-full text-sm">
          <thead className="text-gray-400 border-b border-gray-700">
            <tr><th className="text-left py-1 pr-3">Ticker</th><th className="text-left pr-3">Side</th><th className="text-left pr-3">Qty</th><th className="text-left">Price</th></tr>
          </thead>
          <tbody>
            {positions.map(p => (
              <tr key={p.ticker} className="border-b border-gray-800">
                <td className="py-1 pr-3 font-mono font-semibold">{p.ticker}</td>
                <td className={`pr-3 ${p.side === "long" ? "text-green-400" : "text-red-400"}`}>{p.side.toUpperCase()}</td>
                <td className="pr-3">{p.qty}</td>
                <td>${p.current_price?.toFixed(2) || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Logs() {
  const { data: signals = [] } = useQuery({ queryKey: ["signals"], queryFn: getSignalLog, refetchInterval: 10000 });
  const { data: trades = [] } = useQuery({ queryKey: ["trades"], queryFn: getTradeLog, refetchInterval: 10000 });

  return (
    <div className="p-4 grid grid-cols-2 gap-4">
      <div>
        <h2 className="text-lg font-semibold mb-2">Signal Log</h2>
        <div className="space-y-1 max-h-80 overflow-y-auto">
          {signals.map((s, i) => (
            <div key={i} className="text-xs bg-gray-800 rounded px-2 py-1 flex justify-between">
              <span className="font-mono">{s.ticker} <b className={s.signal === "flip_long" ? "text-green-400" : "text-red-400"}>{s.signal}</b></span>
              <span className="text-gray-400">{s.outcome}</span>
            </div>
          ))}
        </div>
      </div>
      <div>
        <h2 className="text-lg font-semibold mb-2">Trade Log</h2>
        <div className="space-y-1 max-h-80 overflow-y-auto">
          {trades.map((t, i) => (
            <div key={i} className="text-xs bg-gray-800 rounded px-2 py-1 flex justify-between">
              <span className="font-mono">{t.ticker} <b className={t.signal === "flip_long" ? "text-green-400" : "text-red-400"}>{t.signal}</b></span>
              <span className="text-gray-400">${t.dollar_amount}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const TABS = ["Watchlist", "Positions", "Logs"];

function Dashboard() {
  const [tab, setTab] = useState("Watchlist");
  return (
    <div className="min-h-screen bg-gray-950 text-white font-sans">
      <div className="border-b border-gray-700 px-4 py-3 flex items-center justify-between">
        <h1 className="font-bold text-base">IBKR Algo Dashboard</h1>
        <div className="flex gap-1">
          {TABS.map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-3 py-1 rounded text-sm ${tab === t ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"}`}>{t}</button>
          ))}
        </div>
      </div>
      <StatusBar />
      {tab === "Watchlist" && <WatchlistManager />}
      {tab === "Positions" && <PositionsPanel />}
      {tab === "Logs" && <Logs />}
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <Toaster position="top-right" toastOptions={{ style: { background: "#1f2937", color: "#fff" } }} />
      <Dashboard />
    </QueryClientProvider>
  );
}
