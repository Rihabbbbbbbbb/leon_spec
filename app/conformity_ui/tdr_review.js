// Local review extension to the existing Matrix/TDR page.
const tdrScopeFields = ['project', 'component', 'product', 'variant', 'supplier', 'rfq',
  'document_reference', 'version', 'date', 'language', 'confidentiality'];
const tdrProposalStatuses = [
  'CONFORME_AVEC_PREUVE', 'NON_CONFORME_CONFIRME', 'DEVIATION_DECLAREE',
  'DEVIATION_NON_DECLAREE', 'STATUT_CONTRADICTOIRE', 'REPONSE_SANS_PREUVE',
  'AUCUNE_REPONSE_TROUVEE', 'ANALYSE_EN_COURS_TBD', 'NON_APPLICABLE_A_JUSTIFIER',
  'MAUVAIS_PERIMETRE', 'ANALYSE_MANUELLE_REQUISE', 'ERREUR_EXTRACTION'
];
const tdrScopes = new Map();
let tdrCaseBusy = false;
let tdrCasesOffset = 0;
let tdrCasesHasMore = true;

function tdrHeaders(json = false) {
  const headers = {'Accept': 'application/json'};
  if (json) headers['Content-Type'] = 'application/json';
  const key = document.getElementById('tdr-access-key').value;
  if (key) headers['X-TDR-Review-Key'] = key;
  return headers;
}

function renderTdrScopeInputs() {
  const box = document.getElementById('tdr-scope-inputs');
  if (!box) return;
  box.replaceChildren();
  const inputs = aerisMatrix ? [['MATRIX:' + aerisMatrix.name, aerisMatrix.name + ' (matrix)']] : [];
  aerisEvidence.forEach(file => inputs.push(['TDR:' + file.name, file.name + ' (TDR)']));
  inputs.forEach(([key, label]) => {
    const section = document.createElement('details');
    const title = document.createElement('summary');
    title.textContent = label;
    section.append(title);
    const values = tdrScopes.get(key) || {};
    tdrScopes.set(key, values);
    tdrScopeFields.forEach(field => {
      const wrapper = document.createElement('label');
      wrapper.style.cssText = 'display:inline-block;margin:8px';
      wrapper.append(field + ' ');
      const input = document.createElement('input');
      input.value = values[field] || '';
      input.maxLength = field === 'document_reference' ? 160 :
        ['version', 'date', 'language'].includes(field) ? 80 : 120;
      input.setAttribute('aria-label', label + ': ' + field);
      input.addEventListener('input', () => {
        values[field] = input.value.trim() || null;
        aerisRevision++;
        aerisReport = null;
        aerisExcel = null;
        document.getElementById('results-aeris').classList.remove('show');
        document.getElementById('aeris-excel-btn').disabled = true;
        document.getElementById('tdr-json-btn').disabled = true;
      });
      wrapper.append(input);
      section.append(wrapper);
    });
    box.append(section);
  });
}

function tdrReviewContext() {
  const evidenceScopes = {};
  aerisEvidence.forEach(f => { evidenceScopes[f.name] = tdrScopes.get('TDR:' + f.name) || {}; });
  return {matrix_scope: aerisMatrix ? tdrScopes.get('MATRIX:' + aerisMatrix.name) || {} : {},
    evidence_scopes: evidenceScopes};
}

async function tdrRequest(path, options = {}) {
  const response = await fetch(apiUrl('/tdr-review' + path), {
    ...options, headers: {...tdrHeaders(options.body !== undefined), ...options.headers},
  });
  const data = await safeJson(response);
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data;
}

async function loadTdrCases(append = false) {
  try {
    if (append && !tdrCasesHasMore) { toast('All saved cases are listed'); return; }
    const offset = append ? tdrCasesOffset : 0;
    const result = await tdrRequest('/cases?limit=25&offset=' + offset);
    const select = document.getElementById('tdr-saved-cases');
    if (!append) select.replaceChildren();
    result.cases.forEach(c => {
      const option = document.createElement('option');
      option.value = c.caseId;
      option.textContent = c.matrixFile + ' - ' + c.createdAt;
      select.append(option);
    });
    tdrCasesOffset = offset + result.cases.length;
    tdrCasesHasMore = result.hasMore;
    if (!result.cases.length) toast('No saved TDR cases');
  } catch (error) { toast(error.message, 'error'); }
}

function useTdrCase(data) {
  aerisReport = data;
  aerisExcel = data.reportExcel || null;
  document.getElementById('aeris-excel-btn').disabled = !aerisExcel;
  renderAeris(data);
  renderTdrReview(data);
  document.getElementById('results-aeris').classList.add('show');
}

async function resumeTdrCase() {
  const id = document.getElementById('tdr-saved-cases').value;
  if (!id || tdrCaseBusy || aerisBusy) return;
  tdrCaseBusy = true;
  invalidateAeris();
  const revision = aerisRevision;
  try {
    const data = await tdrRequest('/cases/' + encodeURIComponent(id));
    if (revision === aerisRevision) useTdrCase(data);
    else toast('Inputs changed; saved case was not displayed', 'error');
  } catch (error) { toast(error.message, 'error'); }
  finally { tdrCaseBusy = false; }
}

function renderTdrReview(data) {
  document.getElementById('tdr-json-btn').disabled = !data.caseId;
  document.getElementById('tdr-delete-confirm').checked = false;
  document.getElementById('tdr-review-detail').replaceChildren();
  const scope = document.getElementById('tdr-scope-banner');
  scope.replaceChildren();
  const heading = document.createElement('strong');
  heading.textContent = 'Scope: ' + data.scope.status + ' - No automatic acceptance';
  scope.append(heading);
  Object.entries(data.scope.comparisons).forEach(([name, c]) => {
    const p = document.createElement('p');
    p.textContent = `${name}: ${c.status}. ${c.explanation}` +
      (c.mismatches.length ? ' ' + JSON.stringify(c.mismatches) : '') +
      (c.missingFields.length ? ' Missing: ' + c.missingFields.join(', ') : '') +
      (c.warnings.length ? ' ' + c.warnings.join('; ') : '');
    scope.append(p);
  });
  document.getElementById('tdr-manifest').textContent = JSON.stringify({
    caseId: data.caseId, documents: data.documents, scope: data.scope,
    mapping: data.schemaMapping, limitations: data.limitations,
  }, null, 2);
  document.getElementById('tdr-review-progress').textContent =
    `${data.reviewedCount || 0}/${data.items.length} rows have a human review event. ` +
    'This is review progress, not an acceptance rate. Case: ' + data.caseId;
  const chart = document.getElementById('tdr-status-chart');
  chart.replaceChildren();
  const stacked = document.createElement('div');
  stacked.style.cssText = 'display:flex;height:18px;width:100%;margin-bottom:8px';
  stacked.setAttribute('aria-label', 'AI proposal status distribution');
  const colors = ['#28764a', '#aa3333', '#ac650e', '#8d2727', '#754a8c', '#53718a', '#777777'];
  Object.entries(data.reviewSummary).forEach(([status, count], index) => {
    const segment = document.createElement('div');
    segment.style.cssText = `height:18px;background:${colors[index % colors.length]};width:${count / data.items.length * 100}%`;
    segment.title = `${status}: ${count}`;
    stacked.append(segment);
  });
  chart.append(stacked);
  Object.entries(data.reviewSummary).forEach(([status, count]) => {
    const label = document.createElement('div');
    label.textContent = `${status}: ${count}`;
    chart.append(label);
  });
  const priority = {MAUVAIS_PERIMETRE: 0, ERREUR_EXTRACTION: 1, STATUT_CONTRADICTOIRE: 2,
    NON_CONFORME_CONFIRME: 3, DEVIATION_NON_DECLAREE: 3, DEVIATION_DECLAREE: 4,
    ANALYSE_EN_COURS_TBD: 5};
  const alerts = data.items.filter(i => i.proposal_status !== 'CONFORME_AVEC_PREUVE')
    .sort((a,b) => (priority[a.proposal_status] ?? 6) - (priority[b.proposal_status] ?? 6))
    .slice(0, 5);
  document.getElementById('tdr-top-alerts').innerHTML = '<strong>Top five prioritized review alerts</strong>' +
    alerts.map(i => `<p>${_esc(i.req_id)}: ${_esc(i.proposal_status)} - ${_esc(i.escalation_reasons.join('; '))}</p>`).join('');
  const statusFilter = document.getElementById('tdr-status-filter');
  statusFilter.innerHTML = '<option value="ALL">All AI proposal statuses</option>' +
    tdrProposalStatuses.map(s => `<option>${s}</option>`).join('');
  filterTdrReviews();
}

function filterTdrReviews() {
  if (!aerisReport || !aerisReport.caseId) return;
  const status = document.getElementById('tdr-status-filter').value;
  const reviewed = document.getElementById('tdr-review-filter').value;
  const confidence = document.getElementById('tdr-confidence-filter').value;
  const search = document.getElementById('tdr-review-search').value.trim().toLowerCase();
  const rows = aerisReport.items.filter(i =>
    (status === 'ALL' || i.proposal_status === status) &&
    (reviewed === 'ALL' || Boolean(i.human_decision) === (reviewed === 'REVIEWED')) &&
    (confidence === 'ALL' || i.confidence === confidence) &&
    (!search || [i.req_id, i.description, i.domain, i.variant, i.evidence_file].join(' ').toLowerCase().includes(search)));
  const box = document.getElementById('tdr-review-rows');
  box.replaceChildren();
  const count = document.createElement('p');
  count.textContent = `${rows.length} matching rows; showing first 100. Narrow filters for large cases.`;
  box.append(count);
  rows.slice(0, 100).forEach(i => {
    const button = document.createElement('button');
    button.className = 'btn btn-secondary';
    button.style.cssText = 'display:block;margin:8px 0;max-width:100%;white-space:normal;text-align:left';
    button.textContent = `${i.req_id || '(no ID)'} [row ${i.matrix_source.row}] - AI: ${i.proposal_status}` +
      ` - Human: ${i.human_decision ? i.human_decision.result_status : 'NOT_REVIEWED'}`;
    button.addEventListener('click', () => showTdrRequirement(i.row_key));
    box.append(button);
  });
}

function showTdrRequirement(rowKey) {
  const item = aerisReport.items.find(i => i.row_key === rowKey);
  if (!item) return;
  const box = document.getElementById('tdr-review-detail');
  box.innerHTML = `<h2>${_esc(item.req_id)} - requirement review</h2>
    <p><strong>Original requirement:</strong> ${_esc(item.description)}</p>
    <p><strong>Matrix comment:</strong> ${_esc(item.comment)}</p>
    <p>AI proposal: <strong>${_esc(item.proposal_status)}</strong>; technical result: ${_esc(item.final_status)};
      match confidence: ${_esc(item.confidence)} (heuristic, not a probability).</p>
    <p>${_esc(item.escalation_reasons.join('; '))}</p>
    <details open><summary>Evidence and comparisons</summary><pre id="tdr-detail-evidence" style="white-space:pre-wrap"></pre></details>
    <p>Self-declared reviewer <input id="tdr-reviewer" maxlength="120" required /></p>
    <p>Action <select id="tdr-decision-action" onchange="document.getElementById('tdr-corrected-status').disabled = this.value !== 'CORRECT'">
      <option>REQUEST_EVIDENCE</option><option>VALIDATE</option><option>CORRECT</option><option>REJECT</option><option>MARK_NOT_APPLICABLE</option>
    </select> Corrected proposal status <select id="tdr-corrected-status" disabled>
      ${tdrProposalStatuses.map(s => `<option>${s}</option>`).join('')}</select></p>
    <p>Justification (required) <textarea id="tdr-decision-comment" maxlength="4000" rows="3"></textarea></p>
    <button class="btn btn-primary" id="tdr-decision-save">Record review (not technical acceptance)</button>
    <details open><summary>Append-only review history</summary><pre id="tdr-detail-history" style="white-space:pre-wrap"></pre></details>`;
  document.getElementById('tdr-detail-evidence').textContent = JSON.stringify({
    matrixSource: item.matrix_source, expected: item.expected_value,
    supplier: item.supplier_value, allSources: item.evidence_sources,
    candidates: item.evidence_candidates, comparisons: item.condition_verdicts,
    rationale: item.rationale, scope: item.scope_status,
  }, null, 2);
  document.getElementById('tdr-detail-history').textContent = JSON.stringify(item.review_history || [], null, 2);
  document.getElementById('tdr-decision-save').addEventListener('click', () => saveTdrDecision(rowKey));
  box.scrollIntoView({behavior: 'smooth', block: 'start'});
}

async function saveTdrDecision(rowKey) {
  if (tdrCaseBusy || !aerisReport || !aerisReport.caseId) return;
  const caseId = aerisReport.caseId;
  const item = aerisReport.items.find(i => i.row_key === rowKey);
  const action = document.getElementById('tdr-decision-action').value;
  const decision = {
    reviewer: document.getElementById('tdr-reviewer').value.trim(), action,
    comment: document.getElementById('tdr-decision-comment').value.trim(),
    corrected_status: action === 'CORRECT' ? document.getElementById('tdr-corrected-status').value : null,
    expected_revision: item.review_revision,
  };
  if (!decision.reviewer || !decision.comment) { toast('Reviewer and justification are required', 'error'); return; }
  const revision = aerisRevision;
  const button = document.getElementById('tdr-decision-save');
  button.disabled = true;
  tdrCaseBusy = true;
  try {
    const data = await tdrRequest(`/cases/${encodeURIComponent(caseId)}/requirements/${rowKey}/decisions`,
      {method: 'POST', body: JSON.stringify(decision)});
    if (revision === aerisRevision && aerisReport && aerisReport.caseId === caseId) {
      useTdrCase(data);
      showTdrRequirement(rowKey);
      toast('Review event persisted; original AI proposal unchanged');
    } else toast('Review saved in the original case; inputs changed, so reload that case to view it');
  } catch (error) { toast(error.message, 'error'); }
  finally { tdrCaseBusy = false; if (button.isConnected) button.disabled = false; }
}

function downloadTdrJson() {
  if (!aerisReport || !aerisReport.caseId) return;
  const {reportExcel, reportFileName, ...data} = aerisReport;
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'tdr-review-' + data.caseId + '.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function deleteTdrCase() {
  if (tdrCaseBusy || !aerisReport || !aerisReport.caseId) return;
  if (!document.getElementById('tdr-delete-confirm').checked) {
    toast('Explicit deletion confirmation required', 'error'); return;
  }
  const caseId = aerisReport.caseId;
  tdrCaseBusy = true;
  try {
    await tdrRequest('/cases/' + encodeURIComponent(caseId) + '?confirm=true', {method: 'DELETE'});
    if (aerisReport && aerisReport.caseId === caseId) invalidateAeris();
    await loadTdrCases();
    toast('Case and review events deleted from local application storage');
  } catch (error) { toast(error.message, 'error'); }
  finally { tdrCaseBusy = false; }
}
