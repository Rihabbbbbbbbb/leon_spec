/* AERIS – Multi-supplier TDR technical benchmark (technical offers only). */
(function () {
  'use strict';
  const API = '/api/tdr-bench';
  const root = () => document.getElementById('tab-bench');
  const S = {
    config: null, files: [], suppliers: [], jobId: null, poll: null, result: null,
    view: 'synthesis', editScores: false, draft: null, factFilter: { supplier: '', domain: '', kind: '', grounding: '', q: '' },
  };

  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (sel) => root().querySelector(sel);
  const fmtMB = (b) => (b / 1024 / 1024).toFixed(1) + ' MB';

  async function api(path, opts) {
    const res = await fetch(API + path, opts);
    if (!res.ok) {
      let msg = res.status + ' ' + res.statusText;
      try { const j = await res.json(); if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (e) { /* ignore */ }
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  // A poll or a page click can land on an Azure instance that has not yet
  // seen the shared copy. Retry those reads before telling the user the job
  // is unknown.
  async function apiRead(path) {
    let last;
    for (let attempt = 0; attempt < 3; attempt++) {
      try { return await api(path); }
      catch (e) {
        last = e;
        if (e.status !== 404 || attempt === 2) break;
        await new Promise((resolve) => setTimeout(resolve, 350 * (attempt + 1)));
      }
    }
    throw last;
  }

  function injectCss() {
    if (document.getElementById('tb-css')) return;
    const css = document.createElement('style');
    css.id = 'tb-css';
    css.textContent = `
    #tab-bench .tb-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    #tab-bench label.tb-l{display:block;font-size:12px;font-weight:600;color:var(--muted);margin:8px 0 4px}
    #tab-bench input[type=text],#tab-bench input[type=number],#tab-bench select,#tab-bench textarea{width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid var(--border);border-radius:6px;font:inherit;font-size:13px;background:#fff;color:var(--text)}
    #tab-bench textarea{min-height:60px;resize:vertical}
    #tab-bench table.tb{width:100%;border-collapse:collapse;font-size:13px}
    #tab-bench table.tb th,#tab-bench table.tb td{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;vertical-align:top}
    #tab-bench table.tb th{background:#f5f7fa;font-size:12px;color:var(--muted);position:sticky;top:0;z-index:1}
    #tab-bench .tb-scroll{overflow:auto;max-height:640px;border:1px solid var(--border);border-radius:8px}
    #tab-bench .tb-bar{height:14px;background:#e6ebf2;border-radius:7px;overflow:hidden}
    #tab-bench .tb-bar>div{height:100%;background:var(--accent);transition:width .4s}
    #tab-bench .tb-log{font-family:var(--mono);font-size:11.5px;max-height:180px;overflow:auto;background:#f7f9fc;border:1px solid var(--border);border-radius:6px;padding:6px 8px;margin-top:10px;white-space:pre-wrap}
    #tab-bench .tb-subtabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
    #tab-bench .tb-subtabs button{border:1px solid var(--border);background:#fff;border-radius:16px;padding:5px 12px;cursor:pointer;font-size:13px;color:var(--text)}
    #tab-bench .tb-subtabs button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
    #tab-bench .tb-card{border:1px solid var(--border);border-radius:8px;padding:12px 14px;margin-bottom:10px;background:#fff}
    #tab-bench .tb-rank{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin:10px 0}
    #tab-bench .tb-rank .tb-card h3{margin:0 0 4px;font-size:15px}
    #tab-bench .tb-score{font-size:26px;font-weight:700;color:var(--accent)}
    #tab-bench .tb-pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11.5px;font-weight:600;margin:1px 2px}
    #tab-bench .v-strong_candidate{background:rgba(21,115,71,.12);color:var(--ok)}
    #tab-bench .v-candidate_with_reservations{background:rgba(154,91,0,.14);color:var(--dev)}
    #tab-bench .v-weak_candidate{background:rgba(179,38,30,.12);color:var(--nok)}
    #tab-bench .sev-high{background:rgba(179,38,30,.12);color:var(--nok)}
    #tab-bench .sev-medium{background:rgba(154,91,0,.14);color:var(--dev)}
    #tab-bench .sev-low{background:rgba(91,107,124,.12);color:var(--muted)}
    #tab-bench .g-verified{background:rgba(21,115,71,.12);color:var(--ok)}
    #tab-bench .g-approximate{background:rgba(154,91,0,.14);color:var(--dev)}
    #tab-bench .g-unverified{background:rgba(179,38,30,.12);color:var(--nok)}
    #tab-bench .tb-cite{display:inline-block;font-size:11px;font-family:var(--mono);background:#eef3ff;color:var(--accent);border-radius:4px;padding:0 4px;margin:0 1px;cursor:pointer;border:none}
    #tab-bench .tb-cite:hover{background:var(--accent);color:#fff}
    #tab-bench .heat td.sc{text-align:center;font-weight:700;min-width:60px}
    #tab-bench .heat td.sc input{width:52px;text-align:center}
    #tab-bench .heat td.w input{width:60px}
    #tab-bench .best{outline:2px solid var(--ok);outline-offset:-2px}
    #tab-bench details.tb-dom{border:1px solid var(--border);border-radius:8px;margin-bottom:8px;background:#fff}
    #tab-bench details.tb-dom>summary{cursor:pointer;padding:10px 12px;font-weight:600;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
    #tab-bench details.tb-dom>div{padding:0 12px 12px}
    #tab-bench ul.tb-ul{margin:4px 0 8px 18px;padding:0}
    #tab-bench ul.tb-ul li{margin:2px 0}
    #tab-bench .tb-warn{background:rgba(154,91,0,.08);border:1px solid rgba(154,91,0,.3);border-radius:6px;padding:8px 10px;font-size:12.5px;margin:8px 0}
    #tab-bench .tb-files td input{min-width:160px}
    #tab-bench #tb-jobs,#tab-bench #tb-files{max-width:100%;overflow-x:auto}
    #tab-bench .tb-flex{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    #tb-modal{position:fixed;inset:0;background:rgba(10,20,35,.55);z-index:9999;display:none;align-items:center;justify-content:center}
    #tb-modal.show{display:flex}
    #tb-modal .tb-mbox{background:#fff;border-radius:10px;width:min(1200px,95vw);height:90vh;display:flex;flex-direction:column;overflow:hidden}
    #tb-modal .tb-mhead{padding:10px 14px;border-bottom:1px solid #dde3ec;display:flex;gap:10px;align-items:center}
    #tb-modal .tb-mbody{flex:1;display:grid;grid-template-columns:3fr 2fr;gap:0;min-height:0}
    #tb-modal .tb-mimg{overflow:auto;background:#eef1f6;text-align:center;padding:8px}
    #tb-modal .tb-mimg img{max-width:100%;box-shadow:0 2px 8px rgba(0,0,0,.2)}
    #tb-modal .tb-mtext{overflow:auto;padding:10px 14px;font-size:12.5px;white-space:pre-wrap;font-family:var(--mono)}
    #tb-modal mark{background:#ffe58a}
    @media(max-width:760px){
      #tab-bench .tb-grid{grid-template-columns:minmax(0,1fr)}
      #tab-bench .tb-grid>div{min-width:0}
      #tab-bench .tb-files{overflow-x:auto}
      #tab-bench .tb-flex>*{max-width:100%}
      #tb-modal .tb-mbody{grid-template-columns:minmax(0,1fr)}
    }
    `;
    document.head.appendChild(css);
  }

  // ── Layout ────────────────────────────────────────────────────────
  function layout() {
    root().innerHTML = `
    <div class="panel">
      <h2>Compare supplier technical offers</h2>
      <p class="muted" style="margin-bottom:12px">Upload the technical offers (TDR / technical presentations) of several suppliers.
      The tool extracts every technical fact with a verbatim quote and page, compares suppliers domain by domain, scores them
      (weighted, editable by experts) and produces an executive synthesis. Technical content only – no conformity-matrix check,
      commercial pages are skipped.</p>
      <div class="upload-zone" id="tb-zone">
        <div class="upload-icon">📚</div>
        <p><strong>Click or drop the supplier TDR files here</strong></p>
        <p>.pdf, .pptx, .docx, .txt – several files per supplier allowed (same supplier name ⇒ grouped)</p>
        <input type="file" id="tb-input" accept=".pdf,.pptx,.docx,.txt" multiple style="display:none" />
      </div>
      <div id="tb-files"></div>
      <div class="tb-grid" style="margin-top:6px">
        <div><label class="tb-l">Benchmark title</label><input type="text" id="tb-title" maxlength="200" placeholder="e.g. DM12 display – TDR round 1" /></div>
        <div class="tb-grid">
          <div><label class="tb-l">Output language</label><select id="tb-lang"><option value="fr">Français</option><option value="en">English</option></select></div>
          <div><label class="tb-l">Read diagrams / image slides (vision)</label><select id="tb-vision"><option value="auto">Auto (graphic pages)</option><option value="off">Off (faster)</option><option value="all">All pages</option></select></div>
        </div>
        <div><label class="tb-l">Project context (optional)</label><textarea id="tb-context" maxlength="2000" placeholder="e.g. Stellantis DM12 12.3&quot; cluster display RFQ, SOP 2027…"></textarea></div>
        <div><label class="tb-l">Evaluation focus (optional)</label><textarea id="tb-focus" maxlength="1000" placeholder="e.g. optical performance, functional safety, thermal robustness…"></textarea></div>
      </div>
      <details style="margin-top:10px"><summary class="muted" style="cursor:pointer">⚖️ Domain weights & advanced options</summary>
        <div id="tb-weights" class="tb-grid" style="grid-template-columns:repeat(auto-fill,minmax(230px,1fr));margin-top:8px"></div>
        <div class="tb-grid" style="margin-top:8px">
          <div><label class="tb-l">Max vision pages per supplier</label><input type="number" id="tb-vmax" min="0" max="300" value="40" /></div>
          <div><label class="tb-l">Engine</label><select id="tb-llm"><option value="true">AI (Azure OpenAI)</option><option value="false">Deterministic (no AI, coverage only)</option></select></div>
        </div>
      </details>
      <div id="tb-cfg" class="muted" style="font-size:12px;margin-top:8px"></div>
      <div class="btn-row"><button class="btn btn-primary" id="tb-run" disabled>🏁 Launch benchmark</button></div>
    </div>
    <div class="panel hidden" id="tb-progress">
      <h2 id="tb-ptitle">Analysis in progress…</h2>
      <div class="tb-bar"><div id="tb-pbar" style="width:0%"></div></div>
      <div class="tb-flex" style="margin-top:6px;justify-content:space-between">
        <span id="tb-pstage" class="muted"></span>
        <button class="btn btn-secondary" id="tb-cancel">✖ Cancel</button>
      </div>
      <div class="tb-log" id="tb-plog"></div>
    </div>
    <div id="tb-results"></div>
    <div class="panel">
      <h2>Previous benchmarks</h2>
      <div id="tb-jobs" class="muted">Loading…</div>
    </div>`;
    if (!document.getElementById('tb-modal')) {
      const m = document.createElement('div');
      m.id = 'tb-modal';
      m.innerHTML = `<div class="tb-mbox"><div class="tb-mhead"><strong id="tb-mtitle"></strong><span style="flex:1"></span>
        <button class="btn btn-secondary" id="tb-mprev">◀</button><button class="btn btn-secondary" id="tb-mnext">▶</button>
        <button class="btn btn-secondary" id="tb-mclose">✖ Close</button></div>
        <div id="tb-mquote" style="padding:6px 14px;font-size:12.5px;border-bottom:1px solid #dde3ec;display:none"></div>
        <div class="tb-mbody"><div class="tb-mimg" id="tb-mimg"></div><div class="tb-mtext" id="tb-mtext"></div></div></div>`;
      document.body.appendChild(m);
      m.addEventListener('click', (e) => { if (e.target === m) closeModal(); });
      document.getElementById('tb-mclose').onclick = closeModal;
      document.getElementById('tb-mprev').onclick = () => modalStep(-1);
      document.getElementById('tb-mnext').onclick = () => modalStep(1);
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
    }
    const zone = $('#tb-zone'), input = $('#tb-input');
    zone.onclick = () => input.click();
    input.onchange = () => { addFiles(input.files); input.value = ''; };
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', (e) => { e.preventDefault(); zone.classList.remove('dragover'); addFiles(e.dataTransfer.files); });
    $('#tb-run').onclick = launch;
    $('#tb-cancel').onclick = cancelJob;
    root().addEventListener('click', onRootClick);
  }

  async function loadConfig() {
    try {
      S.config = await api('/config');
      $('#tb-cfg').textContent = S.config.llmAvailable
        ? `AI engine available (${S.config.model}). Limits: ${S.config.limits.fileMB} MB per file, ${S.config.limits.totalMB} MB total.`
        : 'No AI engine configured: the benchmark will run in deterministic mode (coverage-based scores).';
      $('#tb-weights').innerHTML = S.config.domains.map((d) =>
        `<div><label class="tb-l">${esc(d.label_en)}</label><input type="number" min="0" max="10" step="0.5" data-w="${esc(d.key)}" value="${d.weight}" /></div>`).join('');
    } catch (e) {
      $('#tb-cfg').textContent = 'Configuration unavailable: ' + e.message;
    }
  }

  // ── Upload ────────────────────────────────────────────────────────
  async function addFiles(list) {
    const allowed = ['.pdf', '.pptx', '.docx', '.txt'];
    const added = [];
    for (const f of Array.from(list || [])) {
      const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
      if (!allowed.includes(ext)) { alert('Unsupported file: ' + f.name); continue; }
      if (S.files.some((x) => x.file.name === f.name && x.file.size === f.size)) continue;
      const item = { file: f, supplier: '' };
      S.files.push(item); added.push(item);
    }
    if (added.length) {
      try {
        const res = await api('/detect-supplier', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fileNames: added.map((x) => x.file.name) }) });
        added.forEach((x) => { x.supplier = x.supplier || res.suppliers[x.file.name] || ''; });
      } catch (e) { /* names stay editable */ }
    }
    renderFiles();
  }

  function renderFiles() {
    const box = $('#tb-files');
    if (!S.files.length) { box.innerHTML = ''; $('#tb-run').disabled = true; return; }
    const total = S.files.reduce((a, x) => a + x.file.size, 0);
    const groups = new Set(S.files.map((x) => (x.supplier || '').trim().toLowerCase()).filter(Boolean));
    box.innerHTML = `<table class="tb tb-files" style="margin-top:10px"><tr><th>File</th><th>Size</th><th>Supplier (editable)</th><th></th></tr>
      ${S.files.map((x, i) => `<tr><td>${esc(x.file.name)}</td><td>${fmtMB(x.file.size)}</td>
      <td><input type="text" data-sup="${i}" value="${esc(x.supplier)}" maxlength="80" /></td>
      <td><button class="btn btn-secondary" data-rm="${i}" title="Remove">✖</button></td></tr>`).join('')}</table>
      <p class="muted" style="font-size:12px;margin-top:6px">${S.files.length} file(s), ${fmtMB(total)} – ${groups.size} supplier(s).
      ${groups.size < 2 ? '<strong style="color:var(--warn)">A benchmark needs at least 2 suppliers.</strong>' : ''}</p>`;
    box.querySelectorAll('input[data-sup]').forEach((inp) => {
      inp.oninput = () => { S.files[+inp.dataset.sup].supplier = inp.value; updateRunState(); };
      inp.onchange = renderFiles;
    });
    box.querySelectorAll('button[data-rm]').forEach((b) => { b.onclick = () => { S.files.splice(+b.dataset.rm, 1); renderFiles(); }; });
    updateRunState();
  }

  function updateRunState() {
    const groups = new Set(S.files.map((x) => (x.supplier || '').trim().toLowerCase()).filter(Boolean));
    const allNamed = S.files.every((x) => (x.supplier || '').trim());
    $('#tb-run').disabled = !(S.files.length && groups.size >= 2 && allNamed) || !!S.poll;
  }

  async function launch() {
    const fd = new FormData();
    S.files.forEach((x) => fd.append('files', x.file, x.file.name));
    fd.append('suppliers', JSON.stringify(S.files.map((x) => x.supplier.trim())));
    fd.append('title', $('#tb-title').value);
    fd.append('language', $('#tb-lang').value);
    fd.append('vision', $('#tb-vision').value);
    fd.append('visionMaxPages', $('#tb-vmax').value || '40');
    fd.append('projectContext', $('#tb-context').value);
    fd.append('focus', $('#tb-focus').value);
    fd.append('useLlm', $('#tb-llm').value);
    const weights = {};
    root().querySelectorAll('input[data-w]').forEach((i) => { weights[i.dataset.w] = parseFloat(i.value || '0'); });
    fd.append('weights', JSON.stringify(weights));
    $('#tb-run').disabled = true;
    showProgress({ status: 'uploading', progress: 0, stage: 'upload', log: [] });
    $('#tb-pstage').textContent = 'Uploading files…';
    try {
      const state = await api('/jobs', { method: 'POST', body: fd });
      S.jobId = state.id;
      startPolling();
    } catch (e) {
      $('#tb-progress').classList.add('hidden');
      alert('Launch failed: ' + e.message);
      updateRunState();
    }
  }

  // ── Progress ──────────────────────────────────────────────────────
  function showProgress(state) {
    const p = $('#tb-progress');
    p.classList.remove('hidden');
    $('#tb-ptitle').textContent = (state.title ? state.title + ' – ' : '') + ({ queued: 'Queued…', running: 'Analysis in progress…', uploading: 'Uploading…' }[state.status] || state.status);
    $('#tb-pbar').style.width = Math.round(state.progress || 0) + '%';
    const last = (state.log || []).slice(-1)[0];
    $('#tb-pstage').textContent = `${Math.round(state.progress || 0)}% – ${state.stage || ''}${last ? ' – ' + last.message : ''}`;
    $('#tb-plog').textContent = (state.log || []).map((l) => `${(l.time || '').slice(11, 19)}  [${l.stage}] ${l.message}`).join('\n');
    $('#tb-plog').scrollTop = 1e9;
    $('#tb-cancel').classList.toggle('hidden', !['queued', 'running'].includes(state.status));
  }

  function startPolling() {
    stopPolling();
    const tick = async () => {
      try {
        const st = await apiRead('/jobs/' + S.jobId);
        showProgress(st);
        if (['completed', 'failed', 'cancelled', 'interrupted'].includes(st.status)) {
          stopPolling();
          $('#tb-progress').classList.add('hidden');
          if (st.status === 'completed') await openResult(st.id);
          else alert(`Benchmark ${st.status}: ${st.error || ''}`);
          loadJobs(); updateRunState();
        }
      } catch (e) { $('#tb-pstage').textContent = 'Connection issue: ' + e.message + ' (retrying)'; }
    };
    S.poll = setInterval(tick, 2000);
    tick();
  }
  function stopPolling() { if (S.poll) clearInterval(S.poll); S.poll = null; }

  async function cancelJob() {
    if (!S.jobId || !confirm('Cancel this benchmark?')) return;
    try { await api('/jobs/' + S.jobId + '/cancel', { method: 'POST' }); } catch (e) { alert(e.message); }
  }

  // ── Jobs list ─────────────────────────────────────────────────────
  async function loadJobs() {
    try {
      const { jobs } = await api('/jobs');
      const box = $('#tb-jobs');
      if (!jobs.length) { box.textContent = 'No benchmark yet.'; return; }
      box.innerHTML = `<table class="tb"><tr><th>Date</th><th>Title</th><th>Suppliers</th><th>Status</th><th>Mode</th><th></th></tr>
      ${jobs.map((j) => `<tr><td>${esc((j.createdAt || '').replace('T', ' ').slice(0, 16))}</td><td>${esc(j.title)}</td>
        <td>${esc((j.suppliers || []).map((s) => s.name).join(', '))}</td>
        <td>${esc(j.status)}${j.status === 'running' || j.status === 'queued' ? ' ' + Math.round(j.progress || 0) + '%' : ''}</td><td>${esc(j.mode || '')}</td>
        <td class="tb-flex">${j.hasResult ? `<button class="btn btn-secondary" data-open="${esc(j.id)}">Open</button>` : ''}
        ${['running', 'queued'].includes(j.status) ? `<button class="btn btn-secondary" data-follow="${esc(j.id)}">Follow</button>` : ''}
        <button class="btn btn-secondary" data-del="${esc(j.id)}" title="Delete">🗑</button></td></tr>`).join('')}</table>`;
    } catch (e) { $('#tb-jobs').textContent = 'Cannot load jobs: ' + e.message; }
  }

  // ── Results ───────────────────────────────────────────────────────
  async function openResult(jobId) {
    S.jobId = jobId;
    try {
      S.result = await apiRead('/jobs/' + jobId + '/result');
      S.editScores = false; S.draft = null;
      renderResults();
      $('#tb-results').scrollIntoView({ behavior: 'smooth' });
    } catch (e) { alert('Cannot open result: ' + e.message); }
  }

  const R = () => S.result;
  const supName = (sid) => (R().suppliers.find((s) => s.id === sid) || { name: sid }).name;
  const domLabel = (k) => (R().domains.find((d) => d.key === k) || { label: k }).label;
  const factById = () => { if (!R()._fi) { R()._fi = {}; R().facts.forEach((f) => { R()._fi[f.id] = f; }); } return R()._fi; };
  const fr = () => R().language === 'fr';
  const L = (frText, enText) => (fr() ? frText : enText);

  function cites(ids) {
    return (ids || []).map((id) => {
      const f = factById()[id];
      return f ? `<button class="tb-cite" data-fact="${esc(id)}" title="${esc(f.statement)}">p.${f.page}</button>` : '';
    }).join('');
  }
  function citedList(items, withSeverity) {
    if (!items || !items.length) return `<p class="muted" style="font-size:12px">–</p>`;
    return `<ul class="tb-ul">${items.map((x) => `<li>${withSeverity && x.severity ? `<span class="tb-pill sev-${esc(x.severity)}">${esc(x.severity)}</span>` : ''}${esc(x.text)} ${cites(x.fact_ids)}${x.grounded === false && withSeverity !== 'nogflag' ? '' : ''}</li>`).join('')}</ul>`;
  }
  const verdictPill = (v) => v ? `<span class="tb-pill v-${esc(v)}">${esc(R().verdictLabels[v] || v)}</span>` : '';

  function renderResults() {
    const r = R();
    const views = [
      ['synthesis', L('Synthèse', 'Synthesis')], ['scores', L('Scores & pondération', 'Scores & weights')],
      ['domains', L('Comparaison par domaine', 'Domain comparison')], ['params', L('Paramètres clés', 'Key parameters')],
      ['profiles', L('Profils fournisseurs', 'Supplier profiles')], ['questions', L('Questions de clarification', 'Clarification questions')],
      ['facts', L('Faits sources', 'Source facts') + ` (${r.facts.length})`], ['ask', L('Interroger les TDR', 'Ask the TDRs')],
      ['docs', L('Documents & qualité', 'Documents & quality')],
    ];
    $('#tb-results').innerHTML = `<div class="panel">
      <div class="tb-flex" style="justify-content:space-between"><h2 style="margin:0">${esc(r.title || 'Benchmark')}</h2>
      <div class="tb-flex">
        <a class="btn btn-secondary" href="${API}/jobs/${r.jobId}/export?format=xlsx">📗 Excel</a>
        <a class="btn btn-secondary" href="${API}/jobs/${r.jobId}/export?format=docx">📘 Word</a>
        <a class="btn btn-secondary" href="${API}/jobs/${r.jobId}/export?format=json">{ } JSON</a></div></div>
      <p class="muted" style="font-size:12px;margin:6px 0 10px">${esc(r.mode === 'llm' ? 'AI analysis' : 'Deterministic analysis (no AI)')} ·
       ${r.stats.suppliers} ${L('fournisseurs', 'suppliers')} · ${r.stats.documents} documents · ${r.stats.pages} pages ·
       ${r.stats.facts} ${L('faits', 'facts')} (${r.stats.verifiedFacts} ${L('vérifiés', 'verified')}, ${r.stats.approximateFacts} ${L('approx.', 'approx.')}, ${r.stats.unverifiedFacts} ${L('non vérifiés (exclus)', 'unverified (excluded)')}) ·
       ${r.stats.durationSec}s${r.overridesApplied ? ` · <strong style="color:var(--warn)">${L('Ajustements experts appliqués', 'Expert overrides applied')}</strong>` : ''}</p>
      ${(r.warnings || []).length ? `<details class="tb-warn"><summary>${r.warnings.length} ${L('avertissement(s)', 'warning(s)')}</summary><ul class="tb-ul">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></details>` : ''}
      <div class="tb-subtabs">${views.map(([k, l]) => `<button data-view="${k}" class="${S.view === k ? 'on' : ''}">${esc(l)}</button>`).join('')}</div>
      <div id="tb-view"></div></div>`;
    renderView();
  }

  function renderView() {
    const fn = { synthesis: vSynthesis, scores: vScores, domains: vDomains, params: vParams, profiles: vProfiles, questions: vQuestions, facts: vFacts, ask: vAsk, docs: vDocs }[S.view] || vSynthesis;
    $('#tb-view').innerHTML = fn();
    if (S.view === 'facts') renderFactRows();
  }

  function vSynthesis() {
    const r = R(), syn = r.synthesis, rec = syn.recommendation || {};
    const ranked = [...r.suppliers].sort((a, b) => a.rank - b.rank);
    return `
      <div class="tb-rank">${ranked.map((s) => {
        const v = (syn.verdicts || {})[s.id] || {};
        return `<div class="tb-card"${rec.preferred === s.id ? ' style="border:2px solid var(--ok)"' : ''}>
          <div class="muted" style="font-size:12px">#${s.rank}${rec.preferred === s.id ? ' · ⭐ ' + L('recommandé', 'recommended') : ''}</div>
          <h3>${esc(s.name)}</h3><div class="tb-score">${s.score.toFixed(2)}<span class="muted" style="font-size:13px"> / 5</span></div>
          ${verdictPill(v.verdict)}<p style="font-size:12.5px;margin:6px 0 0">${esc(v.headline || '')}</p>
          <p class="muted" style="font-size:11.5px;margin-top:6px">${s.facts} ${L('faits', 'facts')} · ${s.pages} pages · ${s.deviations} ${L('écarts', 'deviations')} · ${s.openPoints} ${L('points ouverts', 'open points')}</p></div>`;
      }).join('')}</div>
      <div class="tb-card"><h3 style="margin-top:0">${L('Synthèse exécutive', 'Executive summary')}</h3><p style="white-space:pre-wrap">${esc(syn.executiveSummary)}</p></div>
      <div class="tb-card"><h3 style="margin-top:0">${L('Recommandation', 'Recommendation')}</h3>
        <p><strong>${esc(supName(rec.preferred || ''))}</strong>${(rec.runnersUp || []).length ? ` – ${L('alternatives', 'runners-up')} : ${rec.runnersUp.map((s) => esc(supName(s))).join(', ')}` : ''}</p>
        ${rec.differsFromScore ? `<div class="tb-warn">${L('La recommandation qualitative diffère du meilleur score pondéré', 'The qualitative recommendation differs from the top weighted score')} (${esc(supName(rec.topScored))}).</div>` : ''}
        <p style="white-space:pre-wrap">${esc(rec.rationale)}</p>
        ${(rec.conditions || []).length ? `<strong>${L('Conditions', 'Conditions')}</strong><ul class="tb-ul">${rec.conditions.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>` : ''}</div>
      ${(syn.majorRisks || []).length ? `<div class="tb-card"><h3 style="margin-top:0">${L('Risques majeurs', 'Major risks')}</h3><ul class="tb-ul">${syn.majorRisks.map((x) => `<li><span class="tb-pill sev-${esc(x.severity)}">${esc(x.severity)}</span><strong>${esc(supName(x.supplier_id))}</strong> – ${esc(x.text)}</li>`).join('')}</ul></div>` : ''}
      ${(syn.crossCutting || []).length ? `<div class="tb-card"><h3 style="margin-top:0">${L('Constats transverses', 'Cross-cutting findings')}</h3><ul class="tb-ul">${syn.crossCutting.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
      ${(syn.nextSteps || []).length ? `<div class="tb-card"><h3 style="margin-top:0">${L('Prochaines étapes', 'Next steps')}</h3><ul class="tb-ul">${syn.nextSteps.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
      ${syn.confidenceNote ? `<p class="muted" style="font-size:12px">ℹ️ ${esc(syn.confidenceNote)}</p>` : ''}
      ${r.overridesApplied ? `<p class="muted" style="font-size:12px">⚠️ ${L('Les scores/pondérations ont été ajustés par un expert ; les textes générés par l’IA ne sont pas régénérés.', 'Scores/weights were adjusted by an expert; AI-generated texts are not regenerated.')}</p>` : ''}`;
  }

  function scoreColor(v) {
    return ['#f1f3f6', '#f8d7d4', '#fbe3c4', '#fff1b8', '#d6efd9', '#a8dcb2'][Math.max(0, Math.min(5, v | 0))];
  }

  function vScores() {
    const r = R(), sids = r.suppliers.map((s) => s.id);
    const ai = r.aiScoreMatrix || r.scoreMatrix, aiW = r.aiWeights || Object.fromEntries(r.domains.map((d) => [d.key, d.weight]));
    if (S.editScores && !S.draft) {
      S.draft = { weights: Object.fromEntries(r.domains.map((d) => [d.key, d.weight])), scores: JSON.parse(JSON.stringify(r.scoreMatrix)),
        comments: Object.assign({}, (r.overrides || {}).comments || {}) };
    }
    const e = S.editScores, D = S.draft;
    const rows = r.domains.map((d) => {
      const best = Math.max(...sids.map((s) => r.scoreMatrix[d.key][s] || 0));
      return `<tr><td>${esc(d.label)}</td><td class="w">${e ? `<input type="number" min="0" max="5" step="0.5" data-ow="${d.key}" value="${D.weights[d.key]}" />` : d.weight}${!e && aiW[d.key] !== d.weight ? ` <span class="muted" title="AI">(${aiW[d.key]})</span>` : ''}</td>
      ${sids.map((s) => {
        const v = r.scoreMatrix[d.key][s] || 0, a = (ai[d.key] || {})[s] || 0, c = r.coverage[d.key][s];
        return `<td class="sc ${v && v === best ? 'best' : ''}" style="background:${scoreColor(e ? D.scores[d.key][s] : v)}" title="${c.facts} facts, ${c.pages} pages, ${c.deviations} dev., ${c.openPoints} open">
          ${e ? `<input type="number" min="0" max="5" step="1" data-os="${d.key}|${s}" value="${D.scores[d.key][s]}" />` : v + (a !== v ? ` <span class="muted" style="font-weight:400;font-size:11px">(IA ${a})</span>` : '')}</td>`;
      }).join('')}</tr>`;
    }).join('');
    const totals = sids.map((s) => `<td class="sc" style="font-size:15px">${r.totals[s].score.toFixed(2)}<div class="muted" style="font-size:11px;font-weight:400">#${r.totals[s].rank} · ${r.totals[s].percent}%</div></td>`).join('');
    const comments = e ? `<div class="tb-grid" style="margin-top:10px">${[['general', L('Commentaire général', 'General comment')], ...sids.map((s) => [s, supName(s)])].map(([k, l]) =>
      `<div><label class="tb-l">${esc(l)}</label><textarea data-oc="${esc(k)}" maxlength="2000">${esc(D.comments[k] || '')}</textarea></div>`).join('')}</div>` :
      Object.keys((r.overrides || {}).comments || {}).length ? `<div class="tb-card" style="margin-top:10px"><strong>${L('Commentaires expert', 'Expert comments')}</strong><ul class="tb-ul">${Object.entries(r.overrides.comments).map(([k, v]) => `<li><strong>${esc(k === 'general' ? L('Général', 'General') : supName(k))}</strong> : ${esc(v)}</li>`).join('')}</ul></div>` : '';
    return `<p class="muted" style="font-size:12.5px">${L('Scores 0–5 par domaine attribués par l’IA à partir des faits cités (0 = non traité). Le total est une moyenne pondérée calculée par l’outil (les domaines non traités par tous sont ignorés). Un expert peut ajuster scores et pondérations.',
      'Domain scores 0–5 assigned by the AI from cited facts (0 = not addressed). The total is a weighted mean computed by the tool (domains nobody addresses are ignored). Experts can adjust scores and weights.')}</p>
      <div class="tb-scroll"><table class="tb heat"><tr><th>${L('Domaine', 'Domain')}</th><th>${L('Poids', 'Weight')}</th>${sids.map((s) => `<th style="text-align:center">${esc(supName(s))}</th>`).join('')}</tr>
      ${rows}<tr><th>${L('Total pondéré', 'Weighted total')}</th><th></th>${totals}</tr></table></div>
      ${comments}
      <div class="btn-row">${e ? `<button class="btn btn-primary" data-act="save-ov">💾 ${L('Enregistrer les ajustements', 'Save overrides')}</button><button class="btn btn-secondary" data-act="cancel-ov">${L('Annuler', 'Cancel')}</button>`
        : `<button class="btn btn-secondary" data-act="edit-ov">✏️ ${L('Ajuster scores / pondérations (expert)', 'Adjust scores / weights (expert)')}</button>`}
        ${r.overridesApplied && !e ? `<button class="btn btn-secondary" data-act="reset-ov">↺ ${L('Revenir aux scores IA', 'Reset to AI scores')}</button>` : ''}</div>`;
  }

  async function saveOverrides(reset) {
    const r = R();
    let payload = {};
    if (!reset) {
      const aiW = r.aiWeights || Object.fromEntries(r.domains.map((d) => [d.key, d.weight]));
      const ai = r.aiScoreMatrix || r.scoreMatrix;
      const D = S.draft;
      root().querySelectorAll('input[data-ow]').forEach((i) => { D.weights[i.dataset.ow] = parseFloat(i.value || '0'); });
      root().querySelectorAll('input[data-os]').forEach((i) => { const [d, s] = i.dataset.os.split('|'); D.scores[d][s] = parseInt(i.value || '0', 10); });
      root().querySelectorAll('textarea[data-oc]').forEach((t) => { D.comments[t.dataset.oc] = t.value; });
      payload = { weights: {}, scores: {}, comments: {} };
      Object.entries(D.weights).forEach(([k, v]) => { if (!isNaN(v) && v !== aiW[k]) payload.weights[k] = Math.max(0, Math.min(5, v)); });
      Object.entries(D.scores).forEach(([d, cells]) => Object.entries(cells).forEach(([s, v]) => {
        if (!isNaN(v) && v !== (ai[d] || {})[s]) (payload.scores[d] = payload.scores[d] || {})[s] = Math.max(0, Math.min(5, v));
      }));
      Object.entries(D.comments).forEach(([k, v]) => { if ((v || '').trim()) payload.comments[k] = v.trim(); });
    }
    try {
      S.result = await api(`/jobs/${r.jobId}/overrides`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      S.editScores = false; S.draft = null;
      renderResults();
    } catch (e) { alert('Save failed: ' + e.message); }
  }

  function vDomains() {
    const r = R(), sids = r.suppliers.map((s) => s.id);
    return r.domains.map((d) => {
      const dr = r.domainResults[d.key];
      if (!dr) return '';
      const scores = sids.map((s) => `<span class="tb-pill" style="background:${scoreColor(r.scoreMatrix[d.key][s])}">${esc(supName(s))} ${r.scoreMatrix[d.key][s]}</span>`).join('');
      const addressed = sids.some((s) => r.coverage[d.key][s].facts);
      return `<details class="tb-dom"><summary>${esc(d.label)} <span class="muted" style="font-weight:400;font-size:12px">(${L('poids', 'weight')} ${d.weight})</span> ${scores}${addressed ? '' : ` <span class="muted">${L('non traité', 'not addressed')}</span>`}</summary><div>
        ${dr.summary ? `<p style="white-space:pre-wrap">${esc(dr.summary)}</p>` : ''}
        ${(dr.differentiators || []).length ? `<strong>${L('Différenciateurs', 'Key differentiators')}</strong><ul class="tb-ul">${dr.differentiators.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${(dr.points || []).length ? `<div class="tb-scroll" style="max-height:none;margin:8px 0"><table class="tb"><tr><th>${L('Aspect', 'Aspect')}</th>${sids.map((s) => `<th>${esc(supName(s))}</th>`).join('')}</tr>
          ${dr.points.map((p) => `<tr><td><strong>${esc(p.aspect)}</strong></td>${sids.map((s) => {
            const pos = p.positions.find((x) => x.supplier_id === s);
            const best = (p.best_supplier_ids || []).includes(s);
            return `<td style="${best ? 'background:rgba(21,115,71,.07)' : ''}">${pos ? `${best ? '🏆 ' : ''}${esc(pos.position)} ${cites(pos.fact_ids)}` : '<span class="muted">–</span>'}</td>`;
          }).join('')}</tr>`).join('')}</table></div>` : ''}
        <div class="tb-grid" style="grid-template-columns:repeat(auto-fill,minmax(300px,1fr))">${sids.map((s) => {
          const a = dr.assessments[s];
          return `<div class="tb-card"><strong>${esc(supName(s))}</strong> – ${a.score}/5 <span class="muted">(${esc(a.coverage)})</span>${a.expertScore != null ? ` <span class="tb-pill sev-medium">${L('expert', 'expert')}</span>` : ''}
            <p style="font-size:12.5px">${esc(a.summary)}</p>
            ${a.strengths.length ? `<div style="font-size:12px;font-weight:600;color:var(--ok)">${L('Points forts', 'Strengths')}</div>${citedList(a.strengths)}` : ''}
            ${a.weaknesses.length ? `<div style="font-size:12px;font-weight:600;color:var(--nok)">${L('Points faibles', 'Weaknesses')}</div>${citedList(a.weaknesses)}` : ''}
            ${a.risks.length ? `<div style="font-size:12px;font-weight:600;color:var(--dev)">${L('Risques', 'Risks')}</div>${citedList(a.risks)}` : ''}</div>`;
        }).join('')}</div></div></details>`;
    }).join('');
  }

  function vParams() {
    const r = R(), sids = r.suppliers.map((s) => s.id), keys = Object.keys(r.parameters || {});
    if (!keys.length) return `<p class="muted">${L('Aucun paramètre clé détecté.', 'No key parameter detected.')}</p>`;
    return `<p class="muted" style="font-size:12.5px">${L('Valeurs déclarées par chaque fournisseur (IA + détection par motifs). Cliquez sur la page pour voir la source.', 'Values declared by each supplier (AI + pattern detection). Click the page to see the source.')}</p>
      <div class="tb-scroll"><table class="tb"><tr><th>${L('Paramètre', 'Parameter')}</th>${sids.map((s) => `<th>${esc(supName(s))}</th>`).join('')}</tr>
      ${keys.map((k) => {
        const lab = r.parameterLabels[k] || { label: k, unit: '' };
        return `<tr><td><strong>${esc(lab.label)}</strong>${lab.unit ? ` <span class="muted">[${esc(lab.unit)}]</span>` : ''}</td>${sids.map((s) => {
          const cells = (r.parameters[k] || {})[s] || [];
          return `<td>${cells.length ? cells.map((c) => `<div>${esc(c.value)}${c.variant ? ` <span class="muted">(${esc(c.variant)})</span>` : ''}
            <button class="tb-cite" data-doc="${esc(c.doc_id)}" data-page="${c.page}" data-quote="${esc(c.fact_id ? (factById()[c.fact_id] || {}).quote || '' : c.excerpt || '')}">p.${c.page}</button>${c.source === 'pattern' ? ' <span class="muted" title="pattern">≈</span>' : ''}</div>`).join('') : '<span class="muted">–</span>'}</td>`;
        }).join('')}</tr>`;
      }).join('')}</table></div>`;
  }

  function vProfiles() {
    const r = R();
    const sec = (title, items, sev) => items && items.length ? `<div style="font-size:12.5px;font-weight:600;margin-top:6px">${title}</div>${citedList(items, sev)}` : '';
    return `<div class="tb-grid" style="grid-template-columns:repeat(auto-fill,minmax(380px,1fr))">${[...r.suppliers].sort((a, b) => a.rank - b.rank).map((s) => {
      const p = r.profiles[s.id] || {};
      const v = (r.synthesis.verdicts || {})[s.id] || {};
      return `<div class="tb-card"><h3 style="margin:0">#${s.rank} ${esc(s.name)} <span class="muted" style="font-size:13px">${s.score.toFixed(2)}/5</span></h3>${verdictPill(v.verdict)}
        <p style="font-size:13px;white-space:pre-wrap">${esc(p.overview)}</p>${p.positioning ? `<p style="font-size:12.5px;font-style:italic">${esc(p.positioning)}</p>` : ''}
        ${sec('✅ ' + L('Points forts', 'Strengths'), p.strengths)}${sec('⚠️ ' + L('Points faibles', 'Weaknesses'), p.weaknesses)}
        ${sec('🔥 ' + L('Risques', 'Risks'), p.risks, true)}${sec('↔️ ' + L('Écarts déclarés', 'Declared deviations'), p.deviations)}
        ${sec('📌 ' + L('Hypothèses & dépendances', 'Assumptions & dependencies'), p.assumptions)}${sec('❓ ' + L('Points ouverts', 'Open points'), p.openPoints)}</div>`;
    }).join('')}</div>`;
  }

  function vQuestions() {
    const r = R();
    const rows = [];
    r.suppliers.forEach((s) => ((r.profiles[s.id] || {}).questions || []).forEach((q) => rows.push({ s, q })));
    if (!rows.length) return `<p class="muted">${L('Aucune question générée.', 'No question generated.')}</p>`;
    const order = { high: 0, medium: 1, low: 2 };
    rows.sort((a, b) => a.s.name.localeCompare(b.s.name) || (order[a.q.priority] ?? 3) - (order[b.q.priority] ?? 3));
    return `<p class="muted" style="font-size:12.5px">${L('Questions à envoyer à chaque fournisseur pour lever les ambiguïtés techniques.', 'Questions to send to each supplier to clear technical ambiguities.')}</p>
      <div class="tb-scroll"><table class="tb"><tr><th>${L('Fournisseur', 'Supplier')}</th><th>${L('Priorité', 'Priority')}</th><th>${L('Domaine', 'Domain')}</th><th>Question</th><th>${L('Justification', 'Rationale')}</th></tr>
      ${rows.map(({ s, q }) => `<tr><td>${esc(s.name)}</td><td><span class="tb-pill sev-${esc(q.priority)}">${esc(q.priority)}</span></td><td>${esc(domLabel(q.domain))}</td><td>${esc(q.question)}</td><td class="muted">${esc(q.rationale)}</td></tr>`).join('')}</table></div>`;
  }

  function vFacts() {
    const r = R(), F = S.factFilter;
    const opt = (v, l, cur) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(l)}</option>`;
    return `<div class="tb-flex" style="margin-bottom:8px">
      <select data-ff="supplier" style="width:auto">${opt('', L('Tous fournisseurs', 'All suppliers'), F.supplier)}${r.suppliers.map((s) => opt(s.id, s.name, F.supplier)).join('')}</select>
      <select data-ff="domain" style="width:auto">${opt('', L('Tous domaines', 'All domains'), F.domain)}${r.domains.map((d) => opt(d.key, d.label, F.domain)).join('')}</select>
      <select data-ff="kind" style="width:auto">${opt('', L('Tous types', 'All kinds'), F.kind)}${Object.entries(r.factKinds).map(([k, l]) => opt(k, l, F.kind)).join('')}</select>
      <select data-ff="grounding" style="width:auto">${opt('', L('Toute vérification', 'Any grounding'), F.grounding)}${['verified', 'approximate', 'unverified'].map((g) => opt(g, g, F.grounding)).join('')}</select>
      <input type="text" data-ff="q" placeholder="${L('Rechercher…', 'Search…')}" value="${esc(F.q)}" style="width:220px" />
      <span class="muted" id="tb-fcount" style="font-size:12px"></span></div>
      <div class="tb-scroll"><table class="tb"><thead><tr><th>ID</th><th>${L('Fournisseur', 'Supplier')}</th><th>${L('Domaine', 'Domain')}</th><th>${L('Type', 'Kind')}</th><th>${L('Énoncé', 'Statement')}</th><th>${L('Valeur', 'Value')}</th><th>Source</th></tr></thead><tbody id="tb-frows"></tbody></table></div>`;
  }

  function renderFactRows() {
    const r = R(), F = S.factFilter, q = F.q.trim().toLowerCase();
    const list = r.facts.filter((f) => (!F.supplier || f.supplier_id === F.supplier) && (!F.domain || f.domain === F.domain)
      && (!F.kind || f.kind === F.kind) && (!F.grounding || f.grounding === F.grounding)
      && (!q || (f.statement + ' ' + f.quote + ' ' + f.value + ' ' + f.topic).toLowerCase().includes(q)));
    const shown = list.slice(0, 600);
    $('#tb-fcount').textContent = `${list.length} / ${r.facts.length}${list.length > shown.length ? ' (600 ' + L('affichés', 'shown') + ')' : ''}`;
    $('#tb-frows').innerHTML = shown.map((f) => `<tr><td style="font-family:var(--mono);font-size:11px">${esc(f.id)}</td><td>${esc(supName(f.supplier_id))}</td>
      <td>${esc(domLabel(f.domain))}</td><td>${esc(r.factKinds[f.kind] || f.kind)}${f.importance === 'high' ? ' <span class="tb-pill sev-high">!</span>' : ''}</td>
      <td>${esc(f.statement)}${f.variant ? ` <span class="muted">(${esc(f.variant)})</span>` : ''}<div class="muted" style="font-size:11.5px;font-style:italic">« ${esc(f.quote)} »</div></td>
      <td>${esc(f.value)}</td><td><button class="tb-cite" data-fact="${esc(f.id)}">${esc(f.doc_id)} p.${f.page}</button> <span class="tb-pill g-${esc(f.grounding)}">${esc(f.grounding)}</span></td></tr>`).join('');
  }

  function vAsk() {
    const r = R();
    return `<p class="muted" style="font-size:12.5px">${L('Posez une question technique : l’outil cherche les pages pertinentes de chaque TDR et répond avec citations.', 'Ask a technical question: the tool retrieves the relevant pages of each TDR and answers with citations.')}</p>
      <div class="tb-flex"><input type="text" id="tb-q" maxlength="2000" style="flex:1;min-width:280px" placeholder="${L('ex. Quelle est la luminance et le contraste proposés ?', 'e.g. What luminance and contrast are proposed?')}" />
      <button class="btn btn-primary" data-act="ask">${L('Demander', 'Ask')}</button></div>
      <div class="tb-flex" style="margin-top:6px;font-size:12.5px">${r.suppliers.map((s) => `<label><input type="checkbox" data-asksup="${esc(s.id)}" checked /> ${esc(s.name)}</label>`).join('')}</div>
      <div id="tb-answer" style="margin-top:10px"></div>`;
  }

  async function doAsk() {
    const q = ($('#tb-q').value || '').trim();
    if (!q) return;
    const ids = Array.from(root().querySelectorAll('input[data-asksup]:checked')).map((i) => i.dataset.asksup);
    if (!ids.length) { alert(L('Sélectionnez au moins un fournisseur.', 'Select at least one supplier.')); return; }
    const box = $('#tb-answer');
    box.innerHTML = '<div class="spinner"></div>';
    try {
      const a = await api(`/jobs/${R().jobId}/ask`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q, supplierIds: ids }) });
      const pageBtn = (c) => `<button class="tb-cite" data-doc="${esc(c.docId)}" data-page="${c.page}">${esc(c.docId)} p.${c.page}</button>`;
      box.innerHTML = `${a.answer ? `<div class="tb-card"><strong>${L('Réponse', 'Answer')}</strong><p style="white-space:pre-wrap">${esc(a.answer)}</p></div>` : ''}
        ${a.mode === 'search' ? `<p class="muted" style="font-size:12px">${L('Mode recherche (sans IA) : extraits les plus pertinents.', 'Search mode (no AI): most relevant excerpts.')}</p>` : ''}
        <div class="tb-grid" style="grid-template-columns:repeat(auto-fill,minmax(320px,1fr))">${a.perSupplier.map((p) => `<div class="tb-card"><strong>${esc(supName(p.supplierId))}</strong>
          ${p.found ? '' : ` <span class="tb-pill sev-medium">${L('non trouvé', 'not found')}</span>`}<p style="font-size:12.5px;white-space:pre-wrap">${esc(p.answer)}</p>${p.citations.map(pageBtn).join('')}</div>`).join('')}</div>`;
    } catch (e) { box.innerHTML = `<div class="tb-warn">${esc(e.message)}</div>`; }
  }

  function vDocs() {
    const r = R(), st = r.stats;
    return `<div class="stats-grid">
      <div class="stat-card stat-total"><div class="value">${st.pages}</div><div class="label">pages</div></div>
      <div class="stat-card stat-ok"><div class="value">${st.verifiedFacts}</div><div class="label">${L('faits vérifiés', 'verified facts')}</div></div>
      <div class="stat-card stat-warn"><div class="value">${st.approximateFacts}</div><div class="label">${L('citations approx.', 'approx. quotes')}</div></div>
      <div class="stat-card stat-nok"><div class="value">${st.unverifiedFacts}</div><div class="label">${L('non vérifiés (exclus)', 'unverified (excluded)')}</div></div>
      <div class="stat-card"><div class="value">${st.visionPages}</div><div class="label">${L('pages lues en vision', 'vision pages')}</div></div>
      <div class="stat-card"><div class="value">${st.llmCalls != null ? st.llmCalls : '–'}</div><div class="label">${L('appels IA', 'AI calls')}</div></div></div>
      <div class="tb-scroll" style="margin-top:10px"><table class="tb"><tr><th>Doc</th><th>${L('Fichier', 'File')}</th><th>${L('Fournisseur', 'Supplier')}</th><th>Pages</th><th>${L('Vision', 'Vision')}</th><th>${L('Pages commerciales ignorées', 'Commercial pages skipped')}</th><th>${L('Avertissements', 'Warnings')}</th></tr>
      ${r.documents.map((d) => `<tr><td>${esc(d.id)}</td><td>${esc(d.fileName)}</td><td>${esc(supName(d.supplierId))}</td><td>${d.pages}${d.renderable ? ` <button class="tb-cite" data-doc="${esc(d.id)}" data-page="1">${L('voir', 'view')}</button>` : ''}</td>
        <td>${d.visionPages}</td><td>${d.commercialPagesSkipped}</td><td class="muted">${esc((d.warnings || []).join('; '))}</td></tr>`).join('')}</table></div>
      <p class="muted" style="font-size:12px;margin-top:8px">${L('Chaque fait est vérifié dans le code : la citation doit être retrouvée textuellement (ou quasi) dans la page indiquée. Les faits non vérifiés sont exclus des comparaisons et scores.', 'Every fact is checked in code: the quote must be found verbatim (or nearly) on the cited page. Unverified facts are excluded from comparisons and scores.')}</p>`;
  }

  // ── Page viewer ───────────────────────────────────────────────────
  const M = { doc: null, page: 1, quote: '', count: null };
  async function openPage(docId, page, quote) {
    if (!R()) return;
    M.doc = docId; M.page = Math.max(1, page | 0); M.quote = quote || '';
    document.getElementById('tb-modal').classList.add('show');
    await loadPage();
  }
  async function loadPage() {
    const r = R(), d = r.documents.find((x) => x.id === M.doc) || { fileName: M.doc, renderable: false, pages: 0 };
    M.count = d.pages;
    document.getElementById('tb-mtitle').textContent = `${supName(d.supplierId)} – ${d.fileName} – page ${M.page}/${d.pages}`;
    const qbox = document.getElementById('tb-mquote');
    qbox.style.display = M.quote ? 'block' : 'none';
    qbox.innerHTML = M.quote ? `<strong>${L('Citation', 'Quote')} :</strong> « ${esc(M.quote)} »` : '';
    const img = document.getElementById('tb-mimg');
    img.innerHTML = d.renderable ? `<img alt="page ${M.page}" src="${API}/jobs/${r.jobId}/docs/${encodeURIComponent(M.doc)}/pages/${M.page}.png" />` : `<p class="muted">${L('Aperçu image disponible uniquement pour les PDF.', 'Image preview only available for PDFs.')}</p>`;
    const image = img.querySelector('img');
    if (image) {
      image.onerror = () => {
        img.innerHTML = `<p class="muted">${L('Aperçu de la page indisponible.', 'Page preview unavailable.')}</p>`;
      };
    }
    const txt = document.getElementById('tb-mtext');
    txt.textContent = '…';
    try {
      const p = await apiRead(`/jobs/${r.jobId}/docs/${encodeURIComponent(M.doc)}/pages/${M.page}`);
      let html = esc(p.text || '') + (p.vision ? '\n\n── ' + L('Transcription vision', 'Vision transcript') + ' ──\n' + esc(p.vision) : '');
      const probe = (M.quote || '').trim().slice(0, 60);
      if (probe.length > 8) {
        const idx = html.toLowerCase().indexOf(esc(probe).toLowerCase());
        if (idx >= 0) html = html.slice(0, idx) + '<mark>' + html.slice(idx, idx + esc(probe).length) + '</mark>' + html.slice(idx + esc(probe).length);
      }
      txt.innerHTML = html || `<span class="muted">${L('(pas de texte)', '(no text)')}</span>`;
      const mk = txt.querySelector('mark'); if (mk) mk.scrollIntoView({ block: 'center' });
    } catch (e) { txt.textContent = e.message; }
  }
  function modalStep(delta) {
    const n = M.page + delta;
    if (!M.doc || n < 1 || (M.count && n > M.count)) return;
    M.page = n; M.quote = ''; loadPage();
  }
  function closeModal() { const m = document.getElementById('tb-modal'); if (m) m.classList.remove('show'); }

  // ── Event delegation ──────────────────────────────────────────────
  async function onRootClick(e) {
    const t = e.target.closest('button,[data-view]');
    if (!t || !root().contains(t)) return;
    if (t.dataset.view) { S.view = t.dataset.view; renderResults(); return; }
    if (t.dataset.fact) { const f = factById()[t.dataset.fact]; if (f) openPage(f.doc_id, f.page, f.quote); return; }
    if (t.dataset.doc) { openPage(t.dataset.doc, parseInt(t.dataset.page, 10), t.dataset.quote || ''); return; }
    if (t.dataset.open) { openResult(t.dataset.open); return; }
    if (t.dataset.follow) { S.jobId = t.dataset.follow; startPolling(); return; }
    if (t.dataset.del) {
      if (!confirm('Delete this benchmark and its files?')) return;
      try { await api('/jobs/' + t.dataset.del, { method: 'DELETE' }); } catch (err) { alert(err.message); }
      if (S.result && S.result.jobId === t.dataset.del) { S.result = null; $('#tb-results').innerHTML = ''; }
      loadJobs(); return;
    }
    const act = t.dataset.act;
    if (act === 'edit-ov') { S.editScores = true; S.draft = null; renderView(); }
    else if (act === 'cancel-ov') { S.editScores = false; S.draft = null; renderView(); }
    else if (act === 'save-ov') saveOverrides(false);
    else if (act === 'reset-ov') { if (confirm('Reset to AI scores and remove expert comments?')) saveOverrides(true); }
    else if (act === 'ask') doAsk();
  }

  function bindFactFilters() {
    root().addEventListener('input', (e) => {
      const el = e.target;
      if (el.dataset && el.dataset.ff) { S.factFilter[el.dataset.ff] = el.value; renderFactRows(); }
    });
    root().addEventListener('change', (e) => {
      const el = e.target;
      if (el.dataset && el.dataset.ff) { S.factFilter[el.dataset.ff] = el.value; renderFactRows(); }
    });
    root().addEventListener('keydown', (e) => { if (e.target.id === 'tb-q' && e.key === 'Enter') doAsk(); });
  }

  let initialised = false;
  function init() {
    if (initialised || !root()) return;
    initialised = true;
    injectCss();
    layout();
    bindFactFilters();
    loadConfig();
    loadJobs();
  }
  window.TdrBench = { init, openResult, reloadJobs: () => loadJobs() };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
