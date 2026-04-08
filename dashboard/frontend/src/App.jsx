import { useState, useEffect } from "react";
import { QueryClient, QueryClientProvider, useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast, { Toaster } from "react-hot-toast";
import { getTickers, addTicker, updateTicker, deleteTicker, bulkEdit, getPositions, getSignalLog, getTradeLog, getStatus, clearHalt, getEarningsLog, verifyAuth, saveCredentials, loadCredentials, clearCredentials } from "./api";

const qc = new QueryClient();

function LoginPage({ onLogin }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    saveCredentials(username, password);
    try {
      await verifyAuth();
      onLogin();
    } catch {
      clearCredentials();
      setError("Invalid username or password");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl p-8 w-full max-w-sm shadow-2xl">
        <h1 className="text-white text-xl font-bold mb-1">IBKR Algo Dashboard</h1>
        <p className="text-gray-400 text-sm mb-6">Sign in to continue</p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Username</label>
            <input
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Password</label>
            <input
              type="password"
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              value={password}
              onChange={e => setPassword(e.target.value)}
            />
          </div>
          {error && <p className="text-red-400 text-xs">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-medium py-2 rounded-lg text-sm transition"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1H", "2H", "3H", "4H", "1D"];
const STATE_COLORS = { WAITING: "bg-gray-500", LONG: "bg-green-600", SHORT: "bg-red-600", BLOCKED: "bg-yellow-600" };

function StatusBar() {
  const qclient = useQueryClient();
  const { data } = useQuery({ queryKey: ["status"], queryFn: getStatus, refetchInterval: 10000 });
  const haltMut = useMutation({
    mutationFn: clearHalt,
    onSuccess: (_, ticker) => { qclient.invalidateQueries(["status"]); toast.success(`Halt cleared: ${ticker}`); },
    onError: () => toast.error("Failed to clear halt"),
  });
  if (!data) return null;
  return (
    <div className="flex gap-6 text-sm px-4 py-2 bg-gray-900 border-b border-gray-700 text-gray-300 flex-wrap items-center">
      <span>Engine: <b className={data.engine_running ? "text-green-400" : "text-red-400"}>{data.engine_running ? "RUNNING" : "STOPPED"}</b></span>
      <span>IBKR: <b className={data.ibkr_connected ? "text-green-400" : "text-red-400"}>{data.ibkr_connected ? "CONNECTED" : "DISCONNECTED"}</b></span>
      <span>Mode: <b className={data.account_mode === "paper" ? "text-yellow-400" : "text-green-400"}>{(data.account_mode || "—").toUpperCase()}</b></span>
      <span>Active: <b className="text-white">{data.active_tickers}</b></span>
      {data.halted_tickers?.length > 0 && (
        <span className="flex items-center gap-2 flex-wrap">
          <span className="text-yellow-400">Halted:</span>
          {data.halted_tickers.map(t => (
            <span key={t} className="flex items-center gap-1">
              <span className="text-yellow-300 font-mono">{t}</span>
              <button
                className="text-xs bg-yellow-700 hover:bg-yellow-600 text-white px-1.5 py-0.5 rounded"
                onClick={() => haltMut.mutate(t)}
              >clear</button>
            </span>
          ))}
        </span>
      )}
      {data.last_signal && (
        <span className="text-gray-400">Last: <b className="text-white">{data.last_signal.ticker}</b> {data.last_signal.signal} — {data.last_signal.outcome}</span>
      )}
    </div>
  );
}

function ParamsEditor({ params, onChange }) {
  const [raw, setRaw] = useState(JSON.stringify(params || {}, null, 2));
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    try {
      const parsed = JSON.parse(raw);
      setError("");
      onChange(parsed);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      setError("Invalid JSON");
    }
  };

  return (
    <div className="space-y-2">
      <textarea
        className={`bg-gray-900 border ${error ? "border-red-500" : "border-gray-600"} rounded px-2 py-1 text-xs font-mono w-full h-20 resize-none focus:outline-none focus:border-blue-500`}
        value={raw}
        onChange={e => { setRaw(e.target.value); setSaved(false); }}
        placeholder='{"slow_len": 50, "fast_len": 15}'
      />
      {error && <p className="text-red-400 text-xs">{error}</p>}
      <button
        onClick={handleSave}
        className={`px-3 py-1 rounded text-xs font-medium transition ${saved ? "bg-green-700 text-green-200" : "bg-blue-600 hover:bg-blue-700 text-white"}`}
      >
        {saved ? "Saved!" : "Save params"}
      </button>
    </div>
  );
}

function WatchlistManager() {
  const qclient = useQueryClient();
  const { data: tickers = [] } = useQuery({ queryKey: ["tickers"], queryFn: getTickers, refetchInterval: 5000 });
  const [newTicker, setNewTicker] = useState({ ticker: "", dollar_amount: 5000, timeframe: "1H", active: true, order_type: "market", indicator_params: {} });
  const [selected, setSelected] = useState([]);
  const [bulkDollar, setBulkDollar] = useState("");
  const [bulkTf, setBulkTf] = useState("");
  const [expandedParams, setExpandedParams] = useState(null);

  const addMut = useMutation({ mutationFn: addTicker, onSuccess: () => { qclient.invalidateQueries(["tickers"]); toast.success("Ticker added"); setNewTicker(p => ({ ...p, ticker: "" })); } });
  const delMut = useMutation({ mutationFn: deleteTicker, onSuccess: () => { qclient.invalidateQueries(["tickers"]); toast.success("Removed"); } });
  const updMut = useMutation({ mutationFn: ({ ticker, payload }) => updateTicker(ticker, payload), onSuccess: () => qclient.invalidateQueries(["tickers"]) });
  const bulkMut = useMutation({ mutationFn: bulkEdit, onSuccess: () => { qclient.invalidateQueries(["tickers"]); toast.success(`Updated ${selected.length} tickers`); setSelected([]); } });

  const stateLabel = (cfg) => {
    if (cfg.blocked) return "BLOCKED";
    const s = cfg.state?.toUpperCase();
    if (!s || s === "FLAT") return "WAITING";
    return s;
  };

  const handleBulkEdit = () => {
    if (selected.length === 0) { toast.error("Select tickers first"); return; }
    const payload = { tickers: selected };
    if (bulkDollar) payload.dollar_amount = parseFloat(bulkDollar);
    if (bulkTf) payload.timeframe = bulkTf;
    bulkMut.mutate(payload);
  };

  return (
    <div className="p-4">
      <h2 className="text-lg font-semibold mb-3">Watchlist ({tickers.length} / 50)</h2>

      {/* Add ticker */}
      <div className="flex gap-2 mb-4 flex-wrap items-end">
        <div>
          <label className="text-xs text-gray-400 block mb-1">Ticker</label>
          <input className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm w-24 uppercase" placeholder="AAPL"
            value={newTicker.ticker} onChange={e => setNewTicker(p => ({ ...p, ticker: e.target.value.toUpperCase() }))} />
        </div>
        <div>
          <label className="text-xs text-gray-400 block mb-1">$ Amount</label>
          <input type="number" className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm w-24"
            value={newTicker.dollar_amount} onChange={e => setNewTicker(p => ({ ...p, dollar_amount: +e.target.value }))} />
        </div>
        <div>
          <label className="text-xs text-gray-400 block mb-1">Timeframe</label>
          <select className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm"
            value={newTicker.timeframe} onChange={e => setNewTicker(p => ({ ...p, timeframe: e.target.value }))}>
            {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs text-gray-400 block mb-1">Order</label>
          <select className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm"
            value={newTicker.order_type} onChange={e => setNewTicker(p => ({ ...p, order_type: e.target.value }))}>
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
        </div>
        <button className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded text-sm h-8"
          onClick={() => addMut.mutate(newTicker)}>+ Add</button>
      </div>

      {/* Bulk edit bar */}
      {selected.length > 0 && (
        <div className="flex gap-2 mb-3 p-2 bg-gray-800 rounded items-center flex-wrap">
          <span className="text-xs text-gray-400">{selected.length} selected:</span>
          <input type="number" className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs w-24" placeholder="$ bulk amount"
            value={bulkDollar} onChange={e => setBulkDollar(e.target.value)} />
          <select className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs"
            value={bulkTf} onChange={e => setBulkTf(e.target.value)}>
            <option value="">-- TF --</option>
            {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
          </select>
          <button className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded text-xs" onClick={handleBulkEdit}>Apply</button>
          <button className="text-gray-400 hover:text-white text-xs" onClick={() => setSelected([])}>Cancel</button>
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-gray-400 border-b border-gray-700 text-xs">
            <tr>
              <th className="text-left py-1 pr-2 w-6">
                <input type="checkbox" onChange={e => setSelected(e.target.checked ? tickers.map(t => t.ticker) : [])} />
              </th>
              <th className="text-left py-1 pr-3">Ticker</th>
              <th className="text-left py-1 pr-3">Active</th>
              <th className="text-left py-1 pr-3">$ Amount</th>
              <th className="text-left py-1 pr-3">Timeframe</th>
              <th className="text-left py-1 pr-3">Order</th>
              <th className="text-left py-1 pr-3">Status</th>
              <th className="text-left py-1 pr-3">Params</th>
              <th className="text-left py-1">Actions</th>
            </tr>
          </thead>
          <tbody>
            {tickers.map(cfg => (
              <>
                <tr key={cfg.ticker} className="border-b border-gray-800 hover:bg-gray-800/40">
                  <td className="py-1 pr-2">
                    <input type="checkbox" checked={selected.includes(cfg.ticker)}
                      onChange={e => setSelected(p => e.target.checked ? [...p, cfg.ticker] : p.filter(t => t !== cfg.ticker))} />
                  </td>
                  <td className="py-1 pr-3 font-mono font-semibold">{cfg.ticker}</td>
                  <td className="py-1 pr-3">
                    <input type="checkbox" checked={cfg.active}
                      onChange={e => updMut.mutate({ ticker: cfg.ticker, payload: { ...cfg, active: e.target.checked } })} />
                  </td>
                  <td className="py-1 pr-3">
                    <input type="number" className="bg-gray-800 border border-gray-600 rounded px-1 w-20 text-sm"
                      defaultValue={cfg.dollar_amount}
                      onBlur={e => updMut.mutate({ ticker: cfg.ticker, payload: { ...cfg, dollar_amount: +e.target.value } })} />
                  </td>
                  <td className="py-1 pr-3">
                    <select className="bg-gray-800 border border-gray-600 rounded px-1 text-sm" value={cfg.timeframe}
                      onChange={e => updMut.mutate({ ticker: cfg.ticker, payload: { ...cfg, timeframe: e.target.value } })}>
                      {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
                    </select>
                  </td>
                  <td className="py-1 pr-3">
                    <select className="bg-gray-800 border border-gray-600 rounded px-1 text-sm" value={cfg.order_type}
                      onChange={e => updMut.mutate({ ticker: cfg.ticker, payload: { ...cfg, order_type: e.target.value } })}>
                      <option value="market">Market</option>
                      <option value="limit">Limit</option>
                    </select>
                  </td>
                  <td className="py-1 pr-3">
                    <span className={`text-xs px-2 py-0.5 rounded text-white ${STATE_COLORS[stateLabel(cfg)] || "bg-gray-600"}`}>{stateLabel(cfg)}</span>
                  </td>
                  <td className="py-1 pr-3">
                    <button className="text-xs text-blue-400 hover:text-blue-300"
                      onClick={() => setExpandedParams(expandedParams === cfg.ticker ? null : cfg.ticker)}>
                      {expandedParams === cfg.ticker ? "Hide" : "Edit"}
                    </button>
                  </td>
                  <td className="py-1">
                    <button className="text-red-400 hover:text-red-300 text-xs"
                      onClick={() => { if (window.confirm(`Remove ${cfg.ticker}?`)) delMut.mutate(cfg.ticker); }}>
                      Remove
                    </button>
                  </td>
                </tr>
                {expandedParams === cfg.ticker && (
                  <tr key={`${cfg.ticker}-params`} className="border-b border-gray-800 bg-gray-900/60">
                    <td colSpan={9} className="px-4 py-2">
                      <p className="text-xs text-gray-400 mb-1">Indicator params (JSON) — changes take effect on next candle cycle</p>
                      <ParamsEditor
                        params={cfg.indicator_params}
                        onChange={newParams => updMut.mutate({ ticker: cfg.ticker, payload: { ...cfg, indicator_params: newParams } })}
                      />
                    </td>
                  </tr>
                )}
              </>
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
      {positions.length === 0 ? (
        <p className="text-gray-500 text-sm">No open positions</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-gray-400 border-b border-gray-700 text-xs">
            <tr>
              <th className="text-left py-1 pr-3">Ticker</th>
              <th className="text-left pr-3">Side</th>
              <th className="text-left pr-3">Qty</th>
              <th className="text-left pr-3">Entry</th>
              <th className="text-left pr-3">Current</th>
              <th className="text-left pr-3">Invested</th>
              <th className="text-left">Unrealised P&L</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(p => (
              <tr key={p.ticker} className="border-b border-gray-800">
                <td className="py-1 pr-3 font-mono font-semibold">{p.ticker}</td>
                <td className={`pr-3 font-semibold ${p.side === "long" ? "text-green-400" : "text-red-400"}`}>{p.side.toUpperCase()}</td>
                <td className="pr-3">{p.qty}</td>
                <td className="pr-3">${p.entry_price?.toFixed(2) || "—"}</td>
                <td className="pr-3">${p.current_price?.toFixed(2) || "—"}</td>
                <td className="pr-3">${p.invested?.toFixed(0) || "—"}</td>
                <td className={p.unrealised_pnl >= 0 ? "text-green-400" : "text-red-400"}>
                  {p.unrealised_pnl >= 0 ? "+" : ""}${p.unrealised_pnl?.toFixed(2) || "—"}
                </td>
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
  const { data: earnings = [] } = useQuery({ queryKey: ["earnings"], queryFn: getEarningsLog, refetchInterval: 30000 });
  const [tab, setTab] = useState("signals");

  return (
    <div className="p-4">
      <div className="flex gap-2 mb-3">
        {["signals", "trades", "earnings"].map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-1 rounded text-xs ${tab === t ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400 hover:text-white"}`}>
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === "signals" && (
        <div className="space-y-1 max-h-96 overflow-y-auto">
          {signals.length === 0 && <p className="text-gray-500 text-sm">No signals yet</p>}
          {signals.map((s, i) => (
            <div key={i} className="text-xs bg-gray-800 rounded px-3 py-1.5 flex justify-between items-center">
              <span>
                <span className="text-gray-400 mr-2">{s.timestamp?.slice(11, 19)}</span>
                <span className="font-mono font-semibold mr-2">{s.ticker}</span>
                <span className={s.signal === "flip_long" ? "text-green-400" : "text-red-400"}>{s.signal}</span>
              </span>
              <span className={`px-2 py-0.5 rounded text-xs ${s.outcome === "executed" ? "bg-green-800 text-green-300" : s.outcome === "blocked" ? "bg-yellow-800 text-yellow-300" : "bg-gray-700 text-gray-400"}`}>
                {s.outcome}
              </span>
            </div>
          ))}
        </div>
      )}

      {tab === "trades" && (
        <div className="space-y-1 max-h-96 overflow-y-auto">
          {trades.length === 0 && <p className="text-gray-500 text-sm">No trades yet</p>}
          {trades.map((t, i) => (
            <div key={i} className="text-xs bg-gray-800 rounded px-3 py-1.5 flex justify-between items-center">
              <span>
                <span className="text-gray-400 mr-2">{t.timestamp?.slice(11, 19)}</span>
                <span className="font-mono font-semibold mr-2">{t.ticker}</span>
                <span className={t.signal === "flip_long" ? "text-green-400" : "text-red-400"}>{t.signal}</span>
              </span>
              <span className="text-gray-400">${t.dollar_amount} · {t.order_type}</span>
            </div>
          ))}
        </div>
      )}

      {tab === "earnings" && (
        <div className="space-y-1 max-h-96 overflow-y-auto">
          {earnings.length === 0 && <p className="text-gray-500 text-sm">No earnings events</p>}
          {earnings.map((e, i) => (
            <div key={i} className="text-xs bg-gray-800 rounded px-3 py-1.5 flex justify-between items-center">
              <span>
                <span className="text-gray-400 mr-2">{e.timestamp?.slice(0, 10)}</span>
                <span className="font-mono font-semibold mr-2">{e.ticker}</span>
                <span className="text-yellow-400">{e.action}</span>
                <span className="text-gray-400 ml-2">earnings: {e.earnings_date}</span>
              </span>
              {e.pnl !== 0 && (
                <span className={e.pnl >= 0 ? "text-green-400" : "text-red-400"}>
                  {e.pnl >= 0 ? "+" : ""}${e.pnl?.toFixed(2)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const TABS = ["Watchlist", "Positions", "Logs"];

function Dashboard({ onLogout }) {
  const [tab, setTab] = useState("Watchlist");
  return (
    <div className="min-h-screen bg-gray-950 text-white font-sans">
      <div className="border-b border-gray-700 px-4 py-3 flex items-center justify-between">
        <h1 className="font-bold text-sm tracking-wide">IBKR Algo Dashboard</h1>
        <div className="flex gap-1 items-center">
          {TABS.map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-3 py-1 rounded text-sm ${tab === t ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"}`}>{t}</button>
          ))}
          <button
            onClick={onLogout}
            className="ml-3 text-xs text-gray-500 hover:text-gray-300 border border-gray-700 px-2 py-1 rounded"
          >Logout</button>
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
  const [authed, setAuthed] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!loadCredentials()) { setChecking(false); return; }
    verifyAuth()
      .then(() => setAuthed(true))
      .catch(() => { clearCredentials(); })
      .finally(() => setChecking(false));
  }, []);

  const handleLogout = () => { clearCredentials(); setAuthed(false); };

  if (checking) {
    return <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-500 text-sm">Loading...</div>;
  }

  return (
    <QueryClientProvider client={qc}>
      <Toaster position="top-right" toastOptions={{ style: { background: "#1f2937", color: "#fff" } }} />
      {authed ? <Dashboard onLogout={handleLogout} /> : <LoginPage onLogin={() => setAuthed(true)} />}
    </QueryClientProvider>
  );
}
