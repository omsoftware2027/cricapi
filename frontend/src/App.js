import { useState, useEffect } from "react";
import "@/App.css";
import { Terminal, Copy, Check, Lock, AlertTriangle, ShieldAlert } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const ADMIN_DOCS_TOKEN = process.env.REACT_APP_ADMIN_DOCS_TOKEN || "";

// ------------ Utility components ------------
const CopyBtn = ({ text }) => {
  const [copied, setCopied] = useState(false);
  return (
    <button
      data-testid="copy-btn"
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
      className="inline-flex items-center gap-1 text-[10px] text-neutral-500 hover:text-neutral-900 transition-colors"
      aria-label="Copy"
    >
      {copied ? <><Check size={11}/> Copied</> : <><Copy size={11}/> Copy</>}
    </button>
  );
};

const Snippet = ({ label, body }) => (
  <div className="border border-neutral-200 rounded-sm bg-white">
    <div className="flex items-center justify-between px-3 py-2 border-b border-neutral-200 bg-neutral-50/60">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">{label}</span>
      <CopyBtn text={body}/>
    </div>
    <pre className="px-3 py-2 text-[11.5px] font-mono text-neutral-800 overflow-x-auto whitespace-pre max-h-[420px]">{body}</pre>
  </div>
);

// ------------ Access gate ------------
const AccessDenied = () => (
  <div className="min-h-screen bg-white flex items-center justify-center px-6">
    <div className="max-w-md text-center">
      <div className="inline-flex items-center justify-center w-12 h-12 rounded-sm border border-neutral-200 bg-neutral-50 mb-4">
        <Lock size={20} className="text-neutral-400"/>
      </div>
      <h1 className="font-heading text-xl font-bold tracking-tight text-neutral-900">Private API docs</h1>
      <p className="mt-2 text-sm text-neutral-500">This page is not publicly accessible. Append <code className="font-mono bg-neutral-100 border border-neutral-200 px-1.5 py-0.5 rounded-sm">?admin_token=…</code> to the URL to view.</p>
      <p className="mt-6 text-[11px] text-neutral-400">If you're the API owner, check your <code className="font-mono">frontend/.env</code> for the value of <code className="font-mono">REACT_APP_ADMIN_DOCS_TOKEN</code>.</p>
    </div>
  </div>
);

// ------------ Docs page ------------
const DocsPage = () => {
  const [tab, setTab] = useState("json");
  const sampleUrl = "https://cricheroes.com/scorecard/25954216/individual/x/live";
  const encoded = encodeURIComponent(sampleUrl);
  const base = BACKEND_URL;

  const jsonSnippets = [
    { label: "1. Single match → JSON (GET)", body:
`curl "${base}/api/cricheroes/25954216" \\
  -H "Authorization: Bearer YOUR_TOKEN"` },
    { label: "2. Batch: many match ids → JSON (POST)", body:
`curl -X POST "${base}/api/cricheroes/batch" \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"match_ids":["25954216","25954217","25954218"]}'` },
    { label: "3. Any scorecard URL → JSON (GET)", body:
`curl "${base}/api/json?url=${encoded}" \\
  -H "Authorization: Bearer YOUR_TOKEN"` },
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
      "data": {
        "match_title": "Team A vs Team B",
        "result": "Team A won by 78 runs",
        "venue": "...",
        "toss": "...",
        "innings": [
          {
            "innings_number": 1,
            "team": "Team A",
            "total": "142/12",
            "overs": "23.0",
            "batting": [
              { "player_id": "31225605", "batter": "Krrish",
                "dismissal": "b Arjun Yadav",
                "runs": "9", "balls": "11", "fours": "1", "sixes": "0", "sr": "81.82" }
            ],
            "bowling": [
              { "player_id": "50458833", "bowler": "Adhi Vijay Sai",
                "overs": "3", "maidens": "0", "runs": "10", "wickets": "0",
                "no_balls": "1", "wides": "0", "econ": "3.33" }
            ],
            "yet_to_bat": [ { "player_id": "8093609", "name": "Bhola" } ],
            "extras": "Extras 67 (nb 12, wd 45, b 10)",
            "total_line": "Total 142/12 (23.0 Overs)",
            "fall_of_wickets": "Fall of Wickets: 8-1 (Vidhun, 3 ov), ..."
          }
        ]
      }
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
    { label: "1. Single match → CSV file", body:
`curl -L "${base}/api/cricheroes/25954216/csv" \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -o scorecard.csv` },
    { label: "2. Any scorecard URL → CSV", body:
`curl -L "${base}/api/csv?url=${encoded}" \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -o scorecard.csv` },
    { label: "3. POST body (any URL) → CSV", body:
`curl -X POST "${base}/api/csv" \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"url":"${sampleUrl}"}' \\
  -o scorecard.csv` },
  ];

  const lovable = `// Supabase Edge Function: import-matches
// Env: CRICHEROES_API_TOKEN (do NOT expose in frontend bundle)
const TOKEN = Deno.env.get("CRICHEROES_API_TOKEN")!;
const API   = "${base}";

const num = (v: unknown) => Number(v) || 0;

export async function importMatches(matchIds: string[]) {
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

  const matchesRows: any[] = [], battingRows: any[] = [], bowlingRows: any[] = [], ytbRows: any[] = [];
  for (const item of results) {
    if (!item.ok) { console.warn("skip", item.key, item.error); continue; }
    const card = item.data;
    const id = item.key;
    matchesRows.push({
      cricheroes_match_id: id,
      title: card.match_title, venue: card.venue,
      toss: card.toss, result: card.result, raw_url: card.url,
    });
    for (const inn of card.innings) {
      for (const b of inn.batting) battingRows.push({
        match_id: id, innings: inn.innings_number, team: inn.team,
        player_id: b.player_id, batter: b.batter, dismissal: b.dismissal,
        runs: num(b.runs), balls: num(b.balls),
        fours: num(b.fours), sixes: num(b.sixes),
        strike_rate: num(b.sr),
      });
      for (const b of inn.bowling) bowlingRows.push({
        match_id: id, innings: inn.innings_number, team: inn.team,
        player_id: b.player_id, bowler: b.bowler, overs: b.overs,
        maidens: num(b.maidens), runs: num(b.runs), wickets: num(b.wickets),
        no_balls: num(b.no_balls), wides: num(b.wides), economy: num(b.econ),
      });
      for (const p of (inn.yet_to_bat || [])) ytbRows.push({
        match_id: id, innings: inn.innings_number,
        player_id: p.player_id, name: p.name,
      });
    }
  }

  await supabase.from("matches").upsert(matchesRows, { onConflict: "cricheroes_match_id" });
  if (battingRows.length) await supabase.from("batting_rows").insert(battingRows);
  if (bowlingRows.length) await supabase.from("bowling_rows").insert(bowlingRows);
  if (ytbRows.length)     await supabase.from("yet_to_bat").insert(ytbRows);
}`;

  const active = tab === "json" ? jsonSnippets : csvSnippets;

  return (
    <div className="min-h-screen bg-white text-neutral-900 font-body">
      <header className="px-6 sm:px-10 pt-10 pb-6 border-b border-neutral-100">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-2.5 h-2.5 rounded-full bg-[#BE123C]" />
          <span className="font-heading text-xs font-bold tracking-wider uppercase text-neutral-500">CricHeroes Scorecard API</span>
        </div>
        <h1 className="font-heading text-2xl sm:text-3xl font-bold tracking-tight">API Documentation</h1>
        <p className="mt-2 text-sm text-neutral-500 max-w-3xl">
          A pure API service — no interactive UI, no database on our side. Consumers (Lovable, n8n, Zapier, etc.) call these endpoints
          and store the response in their own database.
        </p>
      </header>

      <section className="px-6 sm:px-10 py-8 max-w-6xl">
        <div className="mb-8 flex items-start gap-3 rounded-sm border border-amber-200 bg-amber-50 px-4 py-3">
          <ShieldAlert size={16} className="mt-0.5 text-amber-700 shrink-0"/>
          <div className="text-sm text-amber-900">
            <strong className="font-semibold">Bearer token required.</strong> Every request must include
            {" "}<code className="font-mono text-xs bg-white border border-amber-200 px-1.5 py-0.5 rounded-sm">Authorization: Bearer &lt;YOUR_TOKEN&gt;</code>{" "}
            or <code className="font-mono text-xs bg-white border border-amber-200 px-1.5 py-0.5 rounded-sm">X-API-Key: &lt;YOUR_TOKEN&gt;</code>.
            The token itself is <span className="font-semibold">not shown here</span> — paste yours from 1Password / Supabase secrets into <code className="font-mono">YOUR_TOKEN</code>.
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 mb-4">
          <Terminal size={14} className="text-[#BE123C]"/>
          <h2 className="font-heading font-semibold text-neutral-900">Endpoints</h2>
          <div className="ml-auto inline-flex items-center border border-neutral-200 rounded-sm bg-white p-0.5">
            {[
              { k: "json", label: "JSON" },
              { k: "csv", label: "CSV" },
            ].map((t) => (
              <button
                key={t.k}
                data-testid={`docs-tab-${t.k}`}
                onClick={() => setTab(t.k)}
                className={`px-3 py-1 text-xs font-medium rounded-sm transition-colors ${tab === t.k ? "bg-[#BE123C] text-white" : "text-neutral-600 hover:text-neutral-900"}`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <p className="text-sm text-neutral-600 mb-4 max-w-3xl">
          {tab === "json"
            ? "Nested JSON, ideal for storing in Supabase / Firebase / any DB. Batch endpoint scrapes up to 50 match ids in parallel."
            : "Downloadable CSV with Content-Disposition: attachment. Best for spreadsheets and one-off downloads."}
        </p>

        <div className="grid gap-4 md:grid-cols-2">
          {active.map((s) => <Snippet key={s.label} label={s.label} body={s.body}/>)}
        </div>

        {tab === "json" && (
          <>
            <div className="mt-10 mb-3 flex items-center gap-2">
              <Terminal size={14} className="text-[#BE123C]"/>
              <h2 className="font-heading font-semibold text-neutral-900">Lovable + Supabase — ready-to-paste Edge Function</h2>
            </div>
            <Snippet label="import-matches.ts" body={lovable}/>
          </>
        )}

        <div className="mt-10 pt-6 border-t border-neutral-200">
          <h3 className="font-heading text-sm font-semibold text-neutral-900 mb-3">Errors</h3>
          <ul className="text-xs text-neutral-600 space-y-1 font-mono">
            <li><span className="text-neutral-400">401</span> — missing or invalid bearer token</li>
            <li><span className="text-neutral-400">400</span> — malformed request (bad match_id, empty batch, batch &gt; 50)</li>
            <li><span className="text-neutral-400">422</span> — the target site returned no data or a CricHeroes-side error</li>
            <li><span className="text-neutral-400">500</span> — unexpected server error</li>
          </ul>
          <p className="mt-4 text-[11px] text-neutral-500 flex items-start gap-1.5">
            <AlertTriangle size={12} className="mt-0.5 shrink-0"/>
            <span>All errors return a JSON body <code className="font-mono">{`{"detail": "…"}`}</code>. In batch mode, per-item errors are inside <code className="font-mono">results[].error</code> instead of the outer status.</span>
          </p>
        </div>
      </section>
    </div>
  );
};

// ------------ App root ------------
function App() {
  const [allowed, setAllowed] = useState(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const supplied = params.get("admin_token") || "";
    if (!ADMIN_DOCS_TOKEN || supplied === ADMIN_DOCS_TOKEN) {
      setAllowed(true);
    } else {
      setAllowed(false);
    }
  }, []);
  if (allowed === null) return null;
  return allowed ? <DocsPage/> : <AccessDenied/>;
}

export default App;
