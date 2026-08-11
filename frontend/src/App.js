import { useEffect, useState, useCallback } from "react";
import "@/App.css";
import axios from "axios";
import { SCRAPER } from "@/constants/testIds";
import { Download, Loader2, Trash2, Link2, Inbox, ChevronRight, AlertTriangle, Terminal, Copy, Check } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// ------------ helpers ------------
const formatDate = (iso) => {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
};

const sourceBadge = (src) => {
  const map = {
    cricbuzz: { label: "Cricbuzz", cls: "bg-amber-50 text-amber-800 border-amber-200" },
    cricinfo: { label: "Cricinfo", cls: "bg-sky-50 text-sky-800 border-sky-200" },
    cricheroes: { label: "CricHeroes", cls: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  };
  const s = map[src] || { label: src || "?", cls: "bg-neutral-100 text-neutral-700 border-neutral-200" };
  return (
    <span className={`inline-flex items-center rounded-sm border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${s.cls}`}>
      {s.label}
    </span>
  );
};

// ------------ Tables ------------
const BattingTable = ({ rows }) => {
  const hasPid = rows.some((r) => r.player_id);
  return (
  <div data-testid={SCRAPER.battingTable} className="w-full overflow-x-auto border border-neutral-200 rounded-sm">
    <table className="w-full text-left border-collapse">
      <thead>
        <tr className="bg-neutral-50/80 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          {hasPid && <th className="py-2.5 px-4">Player ID</th>}
          <th className="py-2.5 px-4">Batter</th>
          <th className="py-2.5 px-4">Dismissal</th>
          <th className="py-2.5 px-4 text-right">R</th>
          <th className="py-2.5 px-4 text-right">B</th>
          <th className="py-2.5 px-4 text-right">4s</th>
          <th className="py-2.5 px-4 text-right">6s</th>
          <th className="py-2.5 px-4 text-right">SR</th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr><td colSpan={hasPid ? 8 : 7} className="py-6 px-4 text-center text-sm text-neutral-400">No batting data</td></tr>
        )}
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-neutral-100 hover:bg-neutral-50/60">
            {hasPid && <td className="py-2.5 px-4 text-xs font-mono text-neutral-500">{r.player_id}</td>}
            <td className="py-2.5 px-4 text-sm font-semibold text-neutral-900">{r.batter}</td>
            <td className="py-2.5 px-4 text-sm text-neutral-600">{r.dismissal}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-900 text-right font-semibold">{r.runs}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.balls}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.fours}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.sixes}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.sr}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
  );
};

const BowlingTable = ({ rows }) => {
  const hasPid = rows.some((r) => r.player_id);
  return (
  <div data-testid={SCRAPER.bowlingTable} className="w-full overflow-x-auto border border-neutral-200 rounded-sm">
    <table className="w-full text-left border-collapse">
      <thead>
        <tr className="bg-neutral-50/80 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          {hasPid && <th className="py-2.5 px-4">Player ID</th>}
          <th className="py-2.5 px-4">Bowler</th>
          <th className="py-2.5 px-4 text-right">O</th>
          <th className="py-2.5 px-4 text-right">M</th>
          <th className="py-2.5 px-4 text-right">R</th>
          <th className="py-2.5 px-4 text-right">W</th>
          <th className="py-2.5 px-4 text-right">NB</th>
          <th className="py-2.5 px-4 text-right">WD</th>
          <th className="py-2.5 px-4 text-right">ECON</th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr><td colSpan={hasPid ? 9 : 8} className="py-6 px-4 text-center text-sm text-neutral-400">No bowling data</td></tr>
        )}
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-neutral-100 hover:bg-neutral-50/60">
            {hasPid && <td className="py-2.5 px-4 text-xs font-mono text-neutral-500">{r.player_id}</td>}
            <td className="py-2.5 px-4 text-sm font-semibold text-neutral-900">{r.bowler}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.overs}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.maidens}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.runs}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-900 text-right font-semibold">{r.wickets}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.no_balls}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.wides}</td>
            <td className="py-2.5 px-4 text-sm font-mono text-neutral-700 text-right">{r.econ}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
  );
};

// ------------ Skeleton ------------
const RowSkeleton = () => (
  <div className="border border-neutral-200 rounded-sm">
    {Array.from({ length: 5 }).map((_, i) => (
      <div key={i} className="h-10 border-t first:border-t-0 border-neutral-100 animate-pulse bg-neutral-50/40" />
    ))}
  </div>
);

// ------------ Preview ------------
const Preview = ({ data, onDownload }) => {
  const [tab, setTab] = useState(0);
  const innings = data.innings || [];
  useEffect(() => { setTab(0); }, [data.id]);

  return (
    <section data-testid={SCRAPER.previewSection} className="mt-10">
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 pb-6 border-b border-neutral-200">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-2">
            {sourceBadge(data.source)}
            <span className="text-xs text-neutral-500">Scraped {formatDate(data.scraped_at)}</span>
          </div>
          <h2 className="text-xl sm:text-2xl font-bold tracking-tight text-neutral-900 font-heading">
            {data.match_title || "Untitled match"}
          </h2>
          <dl className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-x-6 gap-y-1 text-sm">
            {data.venue && (<><dt className="text-neutral-500 uppercase text-[10px] tracking-wider">Venue</dt><dd className="sm:col-span-2 text-neutral-800">{data.venue}</dd></>)}
            {data.toss && (<><dt className="text-neutral-500 uppercase text-[10px] tracking-wider">Toss</dt><dd className="sm:col-span-2 text-neutral-800">{data.toss}</dd></>)}
            {data.result && (<><dt className="text-neutral-500 uppercase text-[10px] tracking-wider">Result</dt><dd className="sm:col-span-2 text-neutral-800 font-medium">{data.result}</dd></>)}
          </dl>
        </div>
        <button
          data-testid={SCRAPER.downloadCsvBtn}
          onClick={onDownload}
          className="shrink-0 inline-flex items-center gap-2 rounded-sm bg-[#BE123C] text-white font-medium px-5 py-2.5 text-sm transition-colors hover:bg-[#9F1239]"
        >
          <Download size={16}/> Download CSV
        </button>
      </div>

      {innings.length > 1 && (
        <div className="mt-6 flex flex-wrap gap-1 border-b border-neutral-200">
          {innings.map((inn, idx) => (
            <button
              key={idx}
              data-testid={`${SCRAPER.inningsTab}-${idx}`}
              onClick={() => setTab(idx)}
              className={`px-4 py-2 -mb-px text-sm font-medium transition-colors border-b-2 ${tab===idx ? 'border-[#BE123C] text-[#BE123C]' : 'border-transparent text-neutral-500 hover:text-neutral-800'}`}
            >
              Inn {inn.innings_number}: {inn.team} {inn.total && <span className="font-mono ml-1 text-xs">{inn.total}{inn.overs?` (${inn.overs})`:''}</span>}
            </button>
          ))}
        </div>
      )}

      {innings[tab] && (
        <div className="mt-6">
          <h3 className="text-base font-semibold text-neutral-800 mb-3">Batting</h3>
          <BattingTable rows={innings[tab].batting || []} />
          {(innings[tab].extras || innings[tab].total_line || innings[tab].did_not_bat || innings[tab].fall_of_wickets || (innings[tab].yet_to_bat && innings[tab].yet_to_bat.length > 0)) && (
            <div className="mt-3 grid gap-1 text-xs text-neutral-600">
              {innings[tab].total_line && <div><span className="text-neutral-500 font-semibold uppercase tracking-wider mr-2">Total</span>{innings[tab].total_line.replace(/^Total\s*/i,'')}</div>}
              {innings[tab].extras && <div><span className="text-neutral-500 font-semibold uppercase tracking-wider mr-2">Extras</span>{innings[tab].extras.replace(/^Extras\s*/i,'')}</div>}
              {innings[tab].fall_of_wickets && <div className="break-words"><span className="text-neutral-500 font-semibold uppercase tracking-wider mr-2">FOW</span>{innings[tab].fall_of_wickets.replace(/^Fall of Wickets\s*/i,'')}</div>}
              {innings[tab].yet_to_bat && innings[tab].yet_to_bat.length > 0 ? (
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="text-neutral-500 font-semibold uppercase tracking-wider">Yet to Bat</span>
                  {innings[tab].yet_to_bat.map((p, i) => (
                    <span key={p.player_id || i} className="text-neutral-700">
                      {p.name}
                      {p.player_id && <span className="ml-1 font-mono text-[10px] text-neutral-400">#{p.player_id}</span>}
                      {i < innings[tab].yet_to_bat.length - 1 && <span className="ml-2 text-neutral-300">·</span>}
                    </span>
                  ))}
                </div>
              ) : (
                innings[tab].did_not_bat && <div><span className="text-neutral-500 font-semibold uppercase tracking-wider mr-2">DNB</span>{innings[tab].did_not_bat.replace(/^(Did not bat|Yet to bat)\s*:?\s*/i,'')}</div>
              )}
            </div>
          )}
          <h3 className="text-base font-semibold text-neutral-800 mb-3 mt-8">Bowling</h3>
          <BowlingTable rows={innings[tab].bowling || []} />
        </div>
      )}
    </section>
  );
};

// ------------ ApiDocs ------------
const CopyBtn = ({ text }) => {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
      className="inline-flex items-center gap-1 text-[10px] text-neutral-500 hover:text-neutral-900 transition-colors"
      aria-label="Copy"
    >
      {copied ? <><Check size={11}/> Copied</> : <><Copy size={11}/> Copy</>}
    </button>
  );
};

const Snippet = ({ label, body }) => (
  <div className="border border-neutral-200 rounded-sm bg-neutral-50/50">
    <div className="flex items-center justify-between px-3 py-2 border-b border-neutral-200 bg-white">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">{label}</span>
      <CopyBtn text={body}/>
    </div>
    <pre className="px-3 py-2 text-[11.5px] font-mono text-neutral-800 overflow-x-auto whitespace-pre">{body}</pre>
  </div>
);

const ApiDocs = ({ backend, authRequired }) => {
  const [tab, setTab] = useState("json");
  const sampleUrl = "https://cricheroes.com/scorecard/25954216/individual/x/live";
  const encoded = encodeURIComponent(sampleUrl);
  const authHdrCurl = authRequired ? `  -H "Authorization: Bearer YOUR_TOKEN" \\\n` : "";
  const authHdrShort = authRequired ? ` -H "Authorization: Bearer YOUR_TOKEN"` : "";

  const jsonSnippets = [
    { label: "1. Match ID → JSON (GET)", body: authRequired
      ? `curl "${backend}/api/cricheroes/25954216"${authHdrShort}`
      : `${backend}/api/cricheroes/25954216` },
    { label: "2. Batch: many match ids → JSON (POST)", body:
`curl -X POST "${backend}/api/cricheroes/batch" \\
${authHdrCurl}  -H "Content-Type: application/json" \\
  -d '{"match_ids":["25954216","25954217","25954218"]}'` },
    { label: "3. Lovable batch import → Supabase", body:
`const TOKEN = "YOUR_TOKEN";
const API = "${backend}";

// Pass any number of ids (max 50 per call, ~5 scraped in parallel)
async function importMatches(matchIds) {
  const r = await fetch(\`\${API}/api/cricheroes/batch\`, {
    method: "POST",
    headers: {
      Authorization: \`Bearer \${TOKEN}\`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ match_ids: matchIds }),
  });
  if (!r.ok) throw new Error(await r.text());
  const { results } = await r.json();

  for (const item of results) {
    if (!item.ok) {
      console.warn("skip", item.key, item.error);
      continue;
    }
    const card = item.data;
    await supabase.from("matches").upsert({
      cricheroes_match_id: item.key,
      title: card.match_title, venue: card.venue,
      toss: card.toss, result: card.result,
    });
    // ...flatten batting/bowling into their tables (see single-match snippet)
  }
}

// importMatches(["25954216","25954217","25954218"]);` },
    { label: "4. Batch response shape", body:
`{
  "total": 3,
  "successful": 2,
  "failed": 1,
  "results": [
    {
      "key": "25954216",
      "url": "https://cricheroes.com/scorecard/25954216/individual/match/live",
      "ok": true,
      "data": { /* same nested JSON as single-match endpoint */ }
    },
    {
      "key": "abc",
      "url": "",
      "ok": false,
      "status": 400,
      "error": "match_id must be numeric"
    }
  ]
}` },
  ];

  const csvSnippets = [
    { label: "1. Any URL → CSV (GET)", body: authRequired
      ? `curl -L "${backend}/api/csv?url=${encoded}"${authHdrShort} -o card.csv`
      : `${backend}/api/csv?url=${encoded}` },
    { label: "2. CricHeroes match id → CSV (GET)", body: authRequired
      ? `curl -L "${backend}/api/cricheroes/25954216/csv"${authHdrShort} -o card.csv`
      : `${backend}/api/cricheroes/25954216/csv` },
    { label: "3. POST JSON body (any URL)", body:
`curl -X POST "${backend}/api/csv" \\
${authHdrCurl}  -H "Content-Type: application/json" \\
  -d '{"url":"${sampleUrl}"}' \\
  -o scorecard.csv` },
    { label: "4. Save to file (GET)", body:
`curl -L "${backend}/api/cricheroes/25954216/csv"${authHdrShort} -o scorecard.csv` },
  ];

  const active = tab === "json" ? jsonSnippets : csvSnippets;

  return (
    <section data-testid="api-docs-panel" className="px-6 sm:px-10 pt-6 pb-2 border-b border-neutral-100 bg-neutral-50/30">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <Terminal size={14} className="text-[#BE123C]"/>
        <h2 className="font-heading font-semibold text-neutral-900">Use as an API</h2>
        {authRequired ? (
          <span className="ml-2 inline-flex items-center rounded-sm border border-amber-200 bg-amber-50 text-amber-800 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider">Bearer auth required</span>
        ) : (
          <span className="ml-2 inline-flex items-center rounded-sm border border-emerald-200 bg-emerald-50 text-emerald-800 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider">Open</span>
        )}
        <div className="ml-auto inline-flex items-center border border-neutral-200 rounded-sm bg-white p-0.5">
          {[
            { k: "json", label: "JSON" },
            { k: "csv", label: "CSV" },
          ].map((t) => (
            <button
              key={t.k}
              data-testid={`api-tab-${t.k}`}
              onClick={() => setTab(t.k)}
              className={`px-3 py-1 text-xs font-medium rounded-sm transition-colors ${tab === t.k ? "bg-[#BE123C] text-white" : "text-neutral-600 hover:text-neutral-900"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <p className="text-sm text-neutral-600 mb-4 max-w-3xl">
        {tab === "json" ? (
          <>Nested JSON response, perfect for storing in Supabase / Firebase / any DB. Best pick for Lovable-style apps that want to normalise rows into their own schema.</>
        ) : (
          <>Downloadable CSV with <code className="font-mono text-xs bg-white border border-neutral-200 px-1.5 py-0.5 rounded-sm">Content-Disposition: attachment</code>. Best for spreadsheets and browser downloads.</>
        )}
        {authRequired && <> All endpoints require an <code className="font-mono">Authorization: Bearer &lt;token&gt;</code> or <code className="font-mono">X-API-Key: &lt;token&gt;</code> header.</>}
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        {active.map((s) => <Snippet key={s.label} label={s.label} body={s.body}/>)}
      </div>
      <p className="mt-4 text-[11px] text-neutral-500">Errors return <code className="font-mono">401</code> (bad token), <code className="font-mono">422</code> (bad URL) or <code className="font-mono">500</code> with a JSON <code className="font-mono">{`{detail}`}</code> body.</p>
      {!authRequired && (
        <p className="mt-2 text-[11px] text-neutral-500">🔓 Auth is currently disabled. To turn it on, set <code className="font-mono bg-white border border-neutral-200 px-1.5 py-0.5 rounded-sm">API_AUTH_TOKEN=your-secret</code> in <code className="font-mono">backend/.env</code> and restart the server.</p>
      )}
    </section>
  );
};

// ------------ App ------------
function App() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [current, setCurrent] = useState(null);
  const [history, setHistory] = useState([]);
  const [showApi, setShowApi] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);

  useEffect(() => {
    axios.get(`${API}/`).then((r) => setAuthRequired(!!r.data?.auth_required)).catch(() => {});
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/scorecards`);
      setHistory(r.data || []);
    } catch (e) { /* silent */ }
  }, []);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const handleScrape = async (e) => {
    e?.preventDefault?.();
    const val = url.trim();
    if (!val) { setError("Please enter a URL."); return; }
    if (!/^https?:\/\//i.test(val)) { setError("URL must start with http:// or https://"); return; }
    setLoading(true); setError(""); setCurrent(null);
    try {
      const r = await axios.post(`${API}/scrape`, { url: val });
      setCurrent(r.data);
      loadHistory();
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to scrape. Please check the URL and try again.");
    } finally { setLoading(false); }
  };

  const handleDownload = async () => {
    if (!current?.id) return;
    try {
      const r = await axios.get(`${API}/scorecards/${current.id}/csv`, { responseType: "blob" });
      const blob = new Blob([r.data], { type: "text/csv" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      const safe = (current.match_title || "scorecard").replace(/[^a-z0-9-_ ]/gi, "_").slice(0, 80).trim() || "scorecard";
      link.download = `${safe}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (e) { setError("Failed to download CSV."); }
  };

  const openHistoryItem = async (id) => {
    setLoading(true); setError(""); setCurrent(null);
    try {
      const r = await axios.get(`${API}/scorecards/${id}`);
      setCurrent(r.data);
      setUrl(r.data.url || "");
    } catch { setError("Failed to load scorecard."); }
    finally { setLoading(false); }
  };

  const deleteHistoryItem = async (id, ev) => {
    ev?.stopPropagation?.();
    if (!window.confirm("Delete this scorecard from history?")) return;
    try {
      await axios.delete(`${API}/scorecards/${id}`);
      if (current?.id === id) setCurrent(null);
      loadHistory();
    } catch { setError("Failed to delete."); }
  };

  return (
    <div className="min-h-screen bg-white text-neutral-900 font-body flex">
      {/* Sidebar */}
      <aside data-testid={SCRAPER.historySidebar} className="hidden md:flex w-72 border-r border-neutral-200 bg-neutral-50/40 flex-col sticky top-0 h-screen">
        <div className="p-6 border-b border-neutral-200">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full bg-[#BE123C]" />
            <span className="font-heading text-sm font-bold tracking-tight uppercase">Cricket Scraper</span>
          </div>
          <p className="mt-1 text-[11px] text-neutral-500">Paste. Preview. Download.</p>
        </div>
        <div className="px-6 pt-5 pb-2 text-[10px] uppercase tracking-wider font-semibold text-neutral-500">History</div>
        <div className="flex-1 overflow-y-auto pb-6">
          {history.length === 0 ? (
            <div data-testid={SCRAPER.historyEmpty} className="px-6 py-8 text-center">
              <Inbox size={20} className="mx-auto text-neutral-300" />
              <p className="mt-2 text-xs text-neutral-500">No scorecards yet.</p>
            </div>
          ) : (
            <ul>
              {history.map((h) => (
                <li key={h.id}>
                  <div
                    data-testid={`${SCRAPER.historyItem}-${h.id}`}
                    onClick={() => openHistoryItem(h.id)}
                    className={`group cursor-pointer px-6 py-3 border-b border-neutral-100 hover:bg-white flex items-start gap-2 ${current?.id===h.id ? 'bg-white border-l-2 border-l-[#BE123C]' : ''}`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">{sourceBadge(h.source)}</div>
                      <div className="text-sm font-medium text-neutral-900 line-clamp-2 leading-tight">{h.match_title || h.url}</div>
                      <div className="text-[11px] text-neutral-500 mt-1">{formatDate(h.scraped_at)}</div>
                    </div>
                    <button
                      data-testid={`${SCRAPER.historyItemDelete}-${h.id}`}
                      onClick={(e) => deleteHistoryItem(h.id, e)}
                      className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-neutral-100 text-neutral-400 hover:text-[#BE123C]"
                      aria-label="Delete"
                    >
                      <Trash2 size={14}/>
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col min-h-screen">
        <header className="px-6 sm:px-10 pt-10 pb-6 border-b border-neutral-100 flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div>
            <h1 className="font-heading text-2xl sm:text-3xl font-bold tracking-tight">Cricket Scorecard Scraper</h1>
            <p className="mt-2 text-sm text-neutral-500 max-w-2xl">Paste a public Cricbuzz, ESPN Cricinfo or CricHeroes scorecard URL. Preview the batting &amp; bowling for every innings, then download the full match as a single CSV.</p>
          </div>
          <button
            data-testid="api-docs-btn"
            onClick={() => setShowApi((v) => !v)}
            className="shrink-0 inline-flex items-center gap-2 rounded-sm border border-neutral-200 bg-white text-neutral-900 font-medium px-4 py-2 text-sm hover:bg-neutral-50"
          >
            <Terminal size={14}/> {showApi ? "Hide API" : "Use as API"}
          </button>
        </header>

        {showApi && <ApiDocs backend={BACKEND_URL} authRequired={authRequired} />}

        <div className="px-6 sm:px-10 py-8">
          <form onSubmit={handleScrape} className="flex flex-col sm:flex-row gap-3 items-stretch">
            <div className="flex-1 relative">
              <Link2 size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400"/>
              <input
                data-testid={SCRAPER.urlInput}
                type="text"
                placeholder="https://www.cricbuzz.com/live-cricket-scorecard/..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className="h-11 w-full rounded-sm border border-neutral-300 bg-white pl-9 pr-4 py-2 text-sm text-neutral-900 placeholder:text-neutral-400 focus:border-[#BE123C] focus:outline-none focus:ring-1 focus:ring-[#BE123C]"
                disabled={loading}
              />
            </div>
            <button
              data-testid={SCRAPER.scrapeButton}
              type="submit"
              disabled={loading || !url.trim()}
              className="inline-flex items-center justify-center gap-2 rounded-sm bg-[#BE123C] text-white font-medium px-6 py-2.5 text-sm transition-colors hover:bg-[#9F1239] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? <><Loader2 size={16} className="animate-spin"/> Extracting…</> : <>Scrape <ChevronRight size={16}/></>}
            </button>
          </form>

          {error && (
            <div data-testid={SCRAPER.errorMessage} className="mt-4 flex items-start gap-2 rounded-sm border border-red-200 bg-red-50 text-red-800 px-4 py-3 text-sm">
              <AlertTriangle size={16} className="mt-0.5 shrink-0"/>
              <span>{error}</span>
            </div>
          )}

          <div className="mt-3 text-[11px] text-neutral-400 flex flex-wrap gap-x-3 gap-y-1">
            <span>Supported: <span className="text-neutral-600 font-medium">cricbuzz.com</span>, <span className="text-neutral-600 font-medium">espncricinfo.com</span>, <span className="text-neutral-600 font-medium">cricheroes.com</span>.</span>
          </div>

          {loading && (
            <div className="mt-10">
              <div className="h-8 w-2/3 bg-neutral-100 animate-pulse rounded-sm mb-4" />
              <div className="h-4 w-1/2 bg-neutral-100 animate-pulse rounded-sm mb-6" />
              <RowSkeleton />
            </div>
          )}

          {!loading && current && (
            <Preview data={current} onDownload={handleDownload}/>
          )}

          {!loading && !current && !error && (
            <div className="mt-16 text-center max-w-md mx-auto">
              <div className="inline-flex items-center justify-center w-12 h-12 rounded-sm border border-neutral-200 bg-neutral-50 mb-4">
                <Link2 size={20} className="text-neutral-400"/>
              </div>
              <h3 className="font-heading font-semibold text-neutral-800">Start by pasting a scorecard link</h3>
              <p className="mt-1 text-sm text-neutral-500">Try a completed or live match page from Cricbuzz. The full batting and bowling tables will appear here.</p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default App;
