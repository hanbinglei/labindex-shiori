/* ============================================================
   LabIndex Shiori — Frontend Application (Batch 2)
   ============================================================ */

// --- State ---
const state = {
  lang: document.documentElement.lang || 'ja',
  config: null,
  i18n: {},
  currentPage: 1,
  currentQuery: '',
  filters: { year: '', subtopic: '', type: '' },
  selectedDocId: null,
};

// --- i18n ---
async function loadI18n(lang) {
  const r = await fetch(`/api/i18n/${lang}`);
  state.i18n = await r.json();
  state.lang = lang;
  document.documentElement.lang = lang;
  document.documentElement.dataset.lang = lang;
  translateUI();
  document.querySelectorAll('.lang-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.lang === lang);
  });
}

function t(...keys) {
  let val = state.i18n;
  const path = keys.length === 1 && keys[0].includes('.') ? keys[0].split('.') : keys;
  for (const k of path) {
    if (val && typeof val === 'object') val = val[k];
    else return keys.join('.');
  }
  return typeof val === 'string' ? val : keys.join('.');
}

function translateUI() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const keys = el.dataset.i18n.split('.');
    el.textContent = t(...keys);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const keys = el.dataset.i18nPlaceholder.split('.');
    el.placeholder = t(...keys);
  });
  // Update year select labels (data-i18n-title)
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const keys = el.dataset.i18nTitle.split('.');
    el.title = t(...keys);
  });
}

// --- Config ---
async function loadConfig() {
  const r = await fetch('/api/config');
  state.config = await r.json();
  populateFilters();
  populateLineage();
}

function populateFilters() {
  const yearSel = document.getElementById('filter-year');
  const subSel = document.getElementById('filter-subtopic');
  const typeSel = document.getElementById('filter-type');

  if (state.config && state.config.topics) {
    for (const topic of state.config.topics) {
      for (const st of topic.subtopics) {
        const opt = document.createElement('option');
        opt.value = st.name;
        opt.textContent = st[`display_name_${state.lang}`] || st.name;
        opt.dataset.nameJa = st.display_name_ja;
        opt.dataset.nameZh = st.display_name_zh;
        opt.dataset.nameEn = st.display_name_en;
        subSel.appendChild(opt);
      }
    }
  }

  if (state.config && state.config.file_types) {
    for (const ft of state.config.file_types) {
      const opt = document.createElement('option');
      opt.value = ft;
      opt.textContent = ft;
      typeSel.appendChild(opt);
    }
  }

  yearSel.addEventListener('change', onFilterChange);
  subSel.addEventListener('change', onFilterChange);
  typeSel.addEventListener('change', onFilterChange);
}

function updateFilterLabels() {
  const subSel = document.getElementById('filter-subtopic');
  for (const opt of subSel.options) {
    if (!opt.value) continue;
    const name = opt.dataset[`name${state.lang.charAt(0).toUpperCase() + state.lang.slice(1)}`];
    if (name) opt.textContent = name;
  }
  translateUI();
}

// --- Search ---
let searchTimeout = null;
document.getElementById('search-input').addEventListener('input', () => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(doSearch, 300);
});
document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});
document.getElementById('search-btn').addEventListener('click', doSearch);

function onFilterChange() {
  state.filters.year = document.getElementById('filter-year').value;
  state.filters.subtopic = document.getElementById('filter-subtopic').value;
  state.filters.type = document.getElementById('filter-type').value;
  state.currentPage = 1;
  doSearch();
}

async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  state.currentQuery = q;
  state.currentPage = parseInt(document.getElementById('pagination').dataset.page || '1');

  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (state.filters.year) params.set('year', state.filters.year);
  if (state.filters.subtopic) params.set('subtopic', state.filters.subtopic);
  if (state.filters.type) params.set('type', state.filters.type);
  params.set('page', state.currentPage);

  document.getElementById('results-list').innerHTML = '<div class="loading">' + t('app.loading') + '</div>';

  try {
    const r = await fetch(`/api/search?${params}`);
    const data = await r.json();
    if (data.error) {
      if (data.error_i18n_key) {
        document.getElementById('results-list').innerHTML = `<div class="loading" style="color:var(--text-secondary)">⚠️ ${t(...data.error_i18n_key.split('.')) || data.error}</div>`;
      } else {
        document.getElementById('results-list').innerHTML = `<div class="loading">${data.error}</div>`;
      }
      return;
    }
    renderResults(data);
    renderPagination(data);
  } catch (e) {
    document.getElementById('results-list').innerHTML = `<div class="loading">${t('app.error')}</div>`;
  }
}

function renderResults(data) {
  const list = document.getElementById('results-list');
  const info = document.getElementById('results-info');
  if (data.hits.length === 0) {
    info.textContent = t('search.no_results');
    list.innerHTML = `<div class="loading">${t('search.no_results')}</div>`;
    return;
  }
  info.textContent = t('search.results').replace('{count}', data.total);
  list.innerHTML = data.hits.map(doc => {
    const title = doc.title_hl || doc.title || doc.filename;
    const kw = doc.keywords ? doc.keywords.slice(0, 80) : '';
    const year = doc.year || '--';
    const ext = doc.extension || '';
    const sub = doc.subtopic || '';
    const kwSource = doc.keywords_source;
    const titleSource = doc.title_source;
    const kwTag = kwSource ? `<span class="source-tag ${kwSource}">${kwSource}</span>` : '';
    const titleTag = titleSource ? `<span class="source-tag ${titleSource}">${titleSource}</span>` : '';
    return `<div class="result-item" onclick="openDetail(${doc.id})">
      <div class="title">${title} ${titleTag}</div>
      <div class="meta">
        <span>${ext}</span>
        <span>${year}</span>
        ${sub ? `<span>${sub}</span>` : ''}
        ${kw ? `<span class="kw">${kw}</span> ${kwTag}` : ''}
      </div>
    </div>`;
  }).join('');
}

function renderPagination(data) {
  const pg = document.getElementById('pagination');
  if (data.pages <= 1) { pg.innerHTML = ''; return; }
  let html = '';
  for (let i = 1; i <= data.pages; i++) {
    if (i > 1 && i < data.pages - 1 && Math.abs(i - data.page) > 3) {
      if (Math.abs(i - 1 - data.page) > 3) continue;
      html += '<span>...</span>';
      continue;
    }
    html += `<button class="${i === data.page ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`;
  }
  pg.innerHTML = html;
}

function goToPage(page) {
  state.currentPage = page;
  document.getElementById('pagination').dataset.page = page;
  doSearch();
}

// --- Detail Panel ---
async function openDetail(fileId) {
  state.selectedDocId = fileId;
  const panel = document.getElementById('detail-panel');
  panel.classList.remove('hidden');
  document.getElementById('detail-body').innerHTML = '<div class="loading">' + t('app.loading') + '</div>';
  try {
    const [docR, overlayR, relR] = await Promise.all([
      fetch(`/api/document/${fileId}`).then(r => r.json()),
      fetch(`/api/overlay/${fileId}`).then(r => r.json()),
      fetch(`/api/relations/${fileId}`).then(r => r.json()),
    ]);
    if (docR.error) {
      document.getElementById('detail-body').innerHTML = `<div class="loading">${docR.error}</div>`;
      return;
    }
    document.getElementById('detail-title').textContent = docR.title || docR.filename;
    renderDetail(docR, overlayR, relR);
  } catch (e) {
    document.getElementById('detail-body').innerHTML = `<div class="loading">${t('app.error')}</div>`;
  }
}

document.getElementById('detail-close').addEventListener('click', () => {
  document.getElementById('detail-panel').classList.add('hidden');
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.getElementById('detail-panel').classList.add('hidden');
});

function renderDetail(doc, overlay, relations) {
  const body = document.getElementById('detail-body');
  const kwSource = doc.keywords_source;
  const titleSource = doc.title_source;
  const kwTag = kwSource ? `<span class="source-tag ${kwSource}">${kwSource}</span>` : '';
  const titleTag = titleSource ? `<span class="source-tag ${titleSource}">${titleSource}</span>` : '';
  body.innerHTML = `
    <div class="detail-section">
      <label>${t('detail.title')}</label>
      <div class="value inline-edit" data-field="title" onclick="editField(${doc.id},'title',this)">${doc.title || '--'} ${titleTag}</div>
    </div>
    <div class="detail-section">
      <label>${t('detail.keywords')}</label>
      <div class="value inline-edit" data-field="keywords" onclick="editField(${doc.id},'keywords',this)">${(doc.keywords||[]).join(', ') || '--'} ${kwTag}</div>
    </div>
    <div class="detail-section">
      <label>${t('detail.year')}</label>
      <div class="value inline-edit" data-field="year" onclick="editField(${doc.id},'year',this)">${doc.year || '--'}</div>
    </div>
    <div class="detail-section">
      <label>${t('detail.subtopic')}</label>
      <div class="value inline-edit" data-field="subtopic" onclick="editField(${doc.id},'subtopic',this)">${doc.subtopic || '--'}</div>
    </div>
    ${doc.authors && doc.authors.length ? `<div class="detail-section"><label>${t('detail.authors')}</label><div class="value">${doc.authors.join(', ')}</div></div>` : ''}
    <div class="detail-section"><label>${t('detail.file_type')}</label><div class="value">${doc.extension || '--'}</div></div>
    <div class="detail-section"><label>${t('detail.file_path')}</label><div class="value" style="font-size:0.75rem;color:var(--text-muted);font-family:var(--font-mono)">${doc.path || '--'}</div></div>
    <div class="detail-section"><label>${t('detail.file_size')}</label><div class="value">${doc.file_size || '--'}</div></div>
    ${doc.note ? `<div class="detail-section"><label>${t('app.note')||'Note'}</label><div class="value" style="color:var(--warning)">${doc.note}</div></div>` : ''}
    ${doc.abstract ? `<div class="detail-section"><label>${t('detail.abstract')}</label><div class="value" style="font-size:0.85rem">${doc.abstract.slice(0,500)}</div></div>` : ''}
    <div class="detail-section">
      <label>Actions</label>
      <div>${doc.excluded ? `<button class="action-btn" onclick="includeFile(${doc.id})">${t('overlay.include_file')}</button>` : `<button class="action-btn danger" onclick="excludeFile(${doc.id})">${t('overlay.exclude_file')}</button>`}</div>
    </div>
    <div class="detail-section" id="relations-section">
      <label>${t('relation.title')}</label>
      ${relations.length ? relations.map(r => `
        <div class="relation-item" onclick="openDetail(${r.id})">
          <div class="rel-type">${r.type === 'keyword_shared' ? '🔗 ' + t('relation.keyword_shared') : r.type} ${r.note ? '· ' + r.note : ''}</div>
          <div>${r.title || r.filename || 'ID:'+r.id}</div>
          ${r.shared_keywords && r.shared_keywords.length ? `<div style="color:var(--text-muted);font-size:0.75rem">${r.shared_keywords.join(', ')}</div>` : ''}
        </div>
      `).join('') : `<div style="color:var(--text-muted);font-size:0.85rem">${t('relation.no_relation')}</div>`}
    </div>
    <div class="detail-section">
      <label>${t('overlay.add_relation')}</label>
      <div style="display:flex;gap:6px">
        <input type="number" id="rel-target" placeholder="File ID" style="width:80px;background:var(--bg-primary);border:1px solid var(--border);color:var(--text-primary);padding:4px 8px;border-radius:var(--radius)">
        <input type="text" id="rel-note" placeholder="Note" style="flex:1;background:var(--bg-primary);border:1px solid var(--border);color:var(--text-primary);padding:4px 8px;border-radius:var(--radius)">
        <button class="action-btn save" onclick="addRelation(${doc.id})">${t('common.add')||'Add'}</button>
      </div>
    </div>
  `;
}

// --- Overlay Editing ---
function editField(fileId, field, el) {
  const current = el.textContent.trim().replace(/\s+\w+$/, '');
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'edit-input';
  input.value = current === '--' ? '' : current;
  input.dataset.field = field;
  const save = () => { const v = input.value.trim(); if (v) saveCorrection(fileId, field, v, el); };
  input.addEventListener('keydown', e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') cancelEdit(el, current); });
  input.addEventListener('blur', () => save());
  el.innerHTML = '';
  el.appendChild(input);
  input.focus();
}
function cancelEdit(el, oldValue) { el.textContent = oldValue; el.classList.remove('editing'); }
async function saveCorrection(fileId, field, value, el) {
  const payload = { file_id: fileId, field, value };
  if (field === 'keywords') payload.value = JSON.stringify(value.split(',').map(k => k.trim()).filter(k => k));
  try {
    const r = await fetch('/api/overlay/correct', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const result = await r.json();
    if (result.status === 'ok') { showToast(t('overlay.save_success')); openDetail(fileId); }
    else showToast(result.message || t('overlay.save_fail'), true);
  } catch (e) { showToast(t('overlay.save_fail'), true); }
}
async function excludeFile(fileId) {
  try {
    const r = await fetch('/api/overlay/exclude', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_id: fileId }) });
    const result = await r.json();
    if (result.status === 'ok') { showToast(t('overlay.exclude_file') + ' ✅'); openDetail(fileId); doSearch(); }
  } catch (e) { showToast(t('app.error'), true); }
}
async function includeFile(fileId) {
  try {
    const r = await fetch('/api/overlay/include', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_id: fileId }) });
    const result = await r.json();
    if (result.status === 'ok') { showToast(t('overlay.include_file') + ' ✅'); openDetail(fileId); doSearch(); }
  } catch (e) { showToast(t('app.error'), true); }
}
async function addRelation(fileId) {
  const targetId = parseInt(document.getElementById('rel-target').value);
  const note = document.getElementById('rel-note').value;
  if (!targetId) return;
  try {
    const r = await fetch('/api/overlay/relation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_id_a: fileId, file_id_b: targetId, note }) });
    const result = await r.json();
    if (result.status === 'ok') { showToast(t('overlay.add_relation') + ' ✅'); openDetail(fileId); }
    else showToast(result.message || t('app.error'), true);
  } catch (e) { showToast(t('app.error'), true); }
}

// ============================================================
// M11: 研究系譜図（Batch 2 — 缩放/筛选/高亮/防重叠/分支染色）
// ============================================================
let lineageEditMode = false;
let lineageEditSource = null;
const LINEAGE_RULE_TYPES = ['citation', 'bibliographic_coupling', 'title_succession', 'manual', 'intro_similarity', 'keyword'];
let lineageToggles = {};
let lineageHighlightNode = null; // 当前高亮的节点key
let lineageData = null;          // 缓存的完整数据（用于年范围筛选）
let lineageFullYears = [];       // 完整年份列表
let lineageNaturalViewBox = null; // 初始viewBox

// === 分支染色调色板（高区分度, 20色）===
const BRANCH_PALETTE = [
  '#c0392b', '#2980b9', '#27ae60', '#8e44ad', '#f39c12',
  '#16a085', '#d35400', '#2c3e50', '#7f8c8d', '#e67e22',
  '#1abc9c', '#3498db', '#9b59b6', '#e74c3c', '#34495e',
  '#f1c40f', '#2ecc71', '#95a5a6', '#c0392b', '#2980b9'
];
function getBranchColor(idx) { return BRANCH_PALETTE[idx % BRANCH_PALETTE.length]; }

function initLineageToggles() {
  const tg = document.getElementById('lineage-toggles');
  if (!tg) return;
  tg.querySelectorAll('.layer-toggle input[type=checkbox]').forEach(cb => {
    const type = cb.closest('.layer-toggle').dataset.type;
    if (type) lineageToggles[type] = cb.checked;
  });
}

async function populateLineage() {
  const container = document.getElementById('lineage-chart');
  const tooltip = document.getElementById('lineage-tooltip');
  lineageEditSource = null;
  try {
    const r = await fetch('/api/lineage');
    const data = await r.json();
    if (data.error || (!data.researcher_nodes || data.researcher_nodes.length === 0)) {
      container.innerHTML = `<div class="loading">${t('lineage.no_data')}</div>`;
      return;
    }
    lineageData = data;
    lineageFullYears = data.years || [];
    // 年筛选下拉
    populateYearRangeSelectors();
    renderLineage();
  } catch (e) {
    container.innerHTML = `<div class="loading">${t('lineage.no_data')}</div>`;
  }
}

function populateYearRangeSelectors() {
  const selStart = document.getElementById('lineage-year-start');
  const selEnd = document.getElementById('lineage-year-end');
  if (!selStart || !selEnd || !lineageFullYears.length) return;
  const years = lineageFullYears;
  // 只做一次
  if (selStart.options.length > 1) return;
  years.forEach(y => {
    const o1 = document.createElement('option');
    o1.value = y; o1.textContent = y;
    selStart.appendChild(o1);
    const o2 = document.createElement('option');
    o2.value = y; o2.textContent = y;
    selEnd.appendChild(o2);
  });
  selStart.value = years[0];
  selEnd.value = years[years.length - 1];
}

function getFilteredLineageData() {
  const selStart = document.getElementById('lineage-year-start');
  const selEnd = document.getElementById('lineage-year-end');
  const yearStart = selStart ? parseInt(selStart.value) || lineageFullYears[0] : (lineageFullYears[0] || 0);
  const yearEnd = selEnd ? parseInt(selEnd.value) || lineageFullYears[lineageFullYears.length - 1] : (lineageFullYears[lineageFullYears.length - 1] || 0);
  if (!lineageData) return null;
  const years = lineageFullYears.filter(y => y >= yearStart && y <= yearEnd);
  const nodes = lineageData.researcher_nodes.filter(n => n.academic_year >= yearStart && n.academic_year <= yearEnd);
  return { researcher_nodes: nodes, researcher_edges: lineageData.researcher_edges, years };
}

function renderLineage() {
  const container = document.getElementById('lineage-chart');
  const tooltip = document.getElementById('lineage-tooltip');
  const data = getFilteredLineageData();
  if (!data || !data.years.length || !data.researcher_nodes.length) {
    container.innerHTML = `<div class="loading">${t('lineage.no_data')}</div>`;
    return;
  }
  const { researcher_nodes, researcher_edges, years } = data;

  // ---- レイアウト定数 ----
  const TOP_MARGIN = 68;
  const LEFT_MARGIN = 36;
  const RIGHT_MARGIN = 36;
  const COL_WIDTH = 210;
  const ROW_HEIGHT = 64;
  const BAND_GAP = 28;
  const NODE_R = 11;
  const DEGREE_ORDER = ["D", "M", "B"];

  // ---- 学位×年度 で研究者を分類 ----
  const degreeYears = {};
  DEGREE_ORDER.forEach(d => { degreeYears[d] = {}; });
  years.forEach(y => { DEGREE_ORDER.forEach(d => { degreeYears[d][y] = []; }); });
  researcher_nodes.forEach(n => {
    const deg = n.degree || "B";
    if (degreeYears[deg] && degreeYears[deg][n.academic_year]) degreeYears[deg][n.academic_year].push(n);
  });

  // 各行の高さ
  const bandHeights = {};
  DEGREE_ORDER.forEach(d => {
    let max = 1;
    years.forEach(y => { const cnt = (degreeYears[d][y] || []).length; if (cnt > max) max = cnt; });
    bandHeights[d] = max * ROW_HEIGHT;
  });

  // ---- ノード位置計算（防重叠：每个格子里纵向均匀排列）----
  let yOffset = TOP_MARGIN;
  const bandY = {};
  DEGREE_ORDER.forEach(d => {
    const h = bandHeights[d];
    bandY[d] = { top: yOffset, bottom: yOffset + h, mid: yOffset + h / 2 };
    yOffset += h + BAND_GAP;
  });
  const svgH = yOffset;
  const svgW = LEFT_MARGIN + years.length * COL_WIDTH + RIGHT_MARGIN;

  // 节点位置映射
  const nodePos = {};    // key: "researcher_year" → {x, y, node, yi, deg}
  const posToKey = {};   // key: "x_y" → researcher_year (用于高亮查找)
  DEGREE_ORDER.forEach(d => {
    const bandTop = bandY[d].top;
    years.forEach((year, yi) => {
      const list = degreeYears[d][year] || [];
      // 本格内纵向均匀排列（防重叠关键）
      const cellH = bandHeights[d];
      const nodeCount = list.length;
      // 如果本格节点较少，居中；如果较多，均匀分布
      const spacing = Math.max(ROW_HEIGHT, (nodeCount > 0) ? Math.min(cellH / nodeCount, ROW_HEIGHT * 1.5) : ROW_HEIGHT);
      const totalHeight = nodeCount * spacing;
      const startY = bandTop + (cellH - totalHeight) / 2;
      list.forEach((n, ri) => {
        const x = LEFT_MARGIN + yi * COL_WIDTH + COL_WIDTH / 2;
        const y = startY + ri * spacing + spacing / 2;
        const key = `${n.researcher}_${n.academic_year}`;
        nodePos[key] = { x, y, node: n, yi, deg: d };
        posToKey[`${Math.round(x)}_${Math.round(y)}`] = key;
      });
    });
  });

  // ---- エッジの表示判定バッファ ----
  const visibleEdges = [];
  researcher_edges.forEach(e => {
    const relType = e.relation_type;
    if (lineageToggles[relType] === false) return;
    const srcKey = `${e.source_researcher}_${e.source_year}`;
    const tgtKey = `${e.target_researcher}_${e.target_year}`;
    const src = nodePos[srcKey];
    const tgt = nodePos[tgtKey];
    if (!src || !tgt) return;
    // 高亮过滤：如果某个节点被高亮，只保留与此节点相连的边
    if (lineageHighlightNode) {
      const hlKey = lineageHighlightNode;
      if (srcKey !== hlKey && tgtKey !== hlKey) return; // 不相关边跳过
    }
    visibleEdges.push({ e, src, tgt, srcKey, tgtKey });
  });

  // ---- 分支染色：基于当前显示的实线边（citation/bibliographic_coupling/title_succession/manual）----
  const solidTypes = new Set(['citation', 'bibliographic_coupling', 'title_succession', 'manual']);
  // Union-Find
  const ufParent = {};
  const allResearcherKeys = Object.keys(nodePos);
  allResearcherKeys.forEach(k => { ufParent[k] = k; });
  function ufFind(x) { while (ufParent[x] !== x) { ufParent[x] = ufParent[ufParent[x]]; x = ufParent[x]; } return x; }
  function ufUnion(a, b) { ufParent[ufFind(a)] = ufFind(b); }
  visibleEdges.forEach(({ e, srcKey, tgtKey }) => {
    if (solidTypes.has(e.relation_type) && e.confidence && (e.confidence === '确定' || e.confidence === '確定')) {
      ufUnion(srcKey, tgtKey);
    }
  });
  // 计算颜色映射
  const compColors = {};
  const compIds = {};
  allResearcherKeys.forEach(k => {
    const root = ufFind(k);
    if (!(root in compIds)) compIds[root] = Object.keys(compIds).length;
  });
  allResearcherKeys.forEach(k => {
    const root = ufFind(k);
    compColors[k] = getBranchColor(compIds[root]);
  });

  // ---- SVG 構築 ----
  let svg = `<svg class="lineage-svg" width="100%" viewBox="0 0 ${svgW} ${svgH}" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">`;
  svg += `<rect class="svg-bg" x="0" y="0" width="${svgW}" height="${svgH}" fill="transparent"/>`;

  // 学位バンドの背景
  DEGREE_ORDER.forEach(d => {
    const { top, bottom } = bandY[d];
    svg += `<rect class="degree-band" x="${LEFT_MARGIN}" y="${top}" width="${years.length * COL_WIDTH}" height="${bottom - top}" rx="3"/>`;
  });

  // 年区切り縦線
  years.forEach((year, yi) => {
    const x = LEFT_MARGIN + yi * COL_WIDTH + COL_WIDTH / 2;
    svg += `<line class="year-tick" x1="${x}" y1="${TOP_MARGIN - 8}" x2="${x}" y2="${svgH}" />`;
  });

  // 年ラベル
  years.forEach((year, yi) => {
    const x = LEFT_MARGIN + yi * COL_WIDTH + COL_WIDTH / 2;
    svg += `<text class="year-label" x="${x}" y="${TOP_MARGIN - 18}">${year}</text>`;
  });

  // 学位ラベル
  const DEGREE_NAMES = { "D": "D", "M": "M", "B": "B" };
  DEGREE_ORDER.forEach(d => {
    const y = bandY[d].mid;
    svg += `<text class="deg-label" x="${LEFT_MARGIN - 8}" y="${y + 5}" text-anchor="end">${DEGREE_NAMES[d]}</text>`;
  });

  // ---- エッジ ----
  visibleEdges.forEach(({ e, src, tgt, srcKey, tgtKey }) => {
    const isDefinite = e.confidence === '确定' || e.confidence === '確定';
    const isSolid = solidTypes.has(e.relation_type) && isDefinite;
    const lineClass = isSolid ? 'solid-edge' : 'dashed-edge';
    const gap = Math.abs(e.target_year - e.source_year);
    const midX = (src.x + tgt.x) / 2;
    const midY = Math.min(src.y, tgt.y) - (gap > 0 ? 20 : 0);
    const d = `M${src.x},${src.y} Q${midX},${midY} ${tgt.x},${tgt.y}`;
    const topicClass = e.shared_topic ? `edge-topic-${e.shared_topic}` : '';
    // 分支色
    const branchColor = (isSolid && srcKey in compColors) ? compColors[srcKey] : null;

    // 高亮模式CSS类
    let extraClass = '';
    if (lineageHighlightNode) {
      const hlKey = lineageHighlightNode;
      if (srcKey === hlKey || tgtKey === hlKey) extraClass = ' edge-highlighted-chain';
      else extraClass = ' edge-dimmed-all';
    }

    svg += `<path class="edge-line ${lineClass} ${topicClass}${extraClass}" d="${d}" `;
    if (branchColor) {
      svg += `stroke="${escapeXml(branchColor)}" `;
    }
    svg += `data-src-res="${escapeXml(e.source_researcher)}" data-src-year="${e.source_year}" `;
    svg += `data-tgt-res="${escapeXml(e.target_researcher)}" data-tgt-year="${e.target_year}" `;
    svg += `data-type="${e.relation_type}" data-confidence="${e.confidence}" data-strength="${e.strength || 1}" data-topic="${e.shared_topic || ''}" data-gap="${gap}"/>`;
  });

  // ---- ノード ----
  Object.values(nodePos).forEach(({ x, y, node }) => {
    const key = `${node.researcher}_${node.academic_year}`;
    const branchColor = (key in compColors && compColors[key]) || null;
    const nodeFill = branchColor || 'var(--accent)';
    // 高亮模式CSS类
    let extraClass = '';
    if (lineageHighlightNode) {
      if (key === lineageHighlightNode) extraClass = ' node-highlighted-chain';
      else {
        // 检查是否与高亮节点直接相连
        const connected = visibleEdges.some(({ srcKey, tgtKey }) =>
          (srcKey === key && tgtKey === lineageHighlightNode) ||
          (tgtKey === key && srcKey === lineageHighlightNode)
        );
        extraClass = connected ? ' node-highlighted-chain' : ' node-dimmed-all';
      }
    }
    svg += `<g class="node-group${extraClass}" data-researcher="${escapeXml(node.researcher)}" data-year="${node.academic_year}" data-filecount="${node.file_count}">`;
    svg += `  <circle class="node-circle" cx="${x}" cy="${y}" r="${NODE_R}" fill="${escapeXml(nodeFill)}" stroke="${escapeXml(branchColor || 'var(--bg-card)')}" stroke-width="2"/>`;
    svg += `  <text class="node-name" x="${x}" y="${y + NODE_R + 20}" text-anchor="middle">${escapeXml(node.researcher)}</text>`;
    svg += `</g>`;
  });

  svg += '</svg>';
  container.innerHTML = svg;

  // 保存自然viewBox
  const svgEl = container.querySelector('svg');
  if (svgEl) {
    lineageNaturalViewBox = `0 0 ${svgW} ${svgH}`;
    if (!svgEl.dataset.zoomInit) {
      svgEl.dataset.zoomInit = '1';
      svgEl.setAttribute('viewBox', lineageNaturalViewBox);
    }
  }

  // ---- インタラクション ----
  attachLineageInteractions(container, researcher_nodes, tooltip, NODE_R);

  // 取消高亮（点击空白处）
  const bg = container.querySelector('.svg-bg');
  if (bg) {
    bg.addEventListener('click', () => {
      if (lineageHighlightNode) {
        lineageHighlightNode = null;
        renderLineage();
      }
    });
  }
}

// ---- SVG インタラクション（イベントアタッチ）----
function attachLineageInteractions(container, researcher_nodes, tooltip, NODE_R) {
  const svgEl = container.querySelector('svg');
  if (!svgEl) return;

  // === 缩放 + 平移（viewBox操作）===
  let isPanning = false;
  let panStart = null;
  let vbStart = null;

  function getViewBox() {
    const vb = svgEl.getAttribute('viewBox');
    if (!vb) return { x: 0, y: 0, w: 100, h: 100 };
    const parts = vb.split(/[,\s]+/).map(Number);
    return { x: parts[0] || 0, y: parts[1] || 0, w: parts[2] || 100, h: parts[3] || 100 };
  }
  function setViewBox(vb) {
    svgEl.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
  }

  // 滚轮缩放
  svgEl.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = svgEl.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const vb = getViewBox();
    // 鼠标在viewBox中的位置
    const vx = vb.x + (mx / rect.width) * vb.w;
    const vy = vb.y + (my / rect.height) * vb.h;
    const factor = e.deltaY > 0 ? 1.12 : 0.88; // 滚轮下=缩小，上=放大
    const newW = Math.max(vb.w * factor, 100);  // 最小宽度限制
    const newH = Math.max(vb.h * factor, 100);
    // 保持鼠标位置不变
    const newX = vx - (mx / rect.width) * newW;
    const newY = vy - (my / rect.height) * newH;
    setViewBox({ x: newX, y: newY, w: newW, h: newH });
  }, { passive: false });

  // 拖拽平移
  svgEl.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return; // 只接受左键
    // 如果点在节点上则不启动平移（节点点击由节点处理）
    if (e.target.closest('.node-group')) return;
    isPanning = true;
    panStart = { x: e.clientX, y: e.clientY };
    vbStart = getViewBox();
    svgEl.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', (e) => {
    if (!isPanning) return;
    const rect = svgEl.getBoundingClientRect();
    const dx = e.clientX - panStart.x;
    const dy = e.clientY - panStart.y;
    const vb = vbStart;
    vb.x -= (dx / rect.width) * vb.w;
    vb.y -= (dy / rect.height) * vb.h;
    setViewBox(vb);
    panStart = { x: e.clientX, y: e.clientY };
    vbStart = getViewBox();
  });
  window.addEventListener('mouseup', () => {
    if (isPanning) {
      isPanning = false;
      if (svgEl) svgEl.style.cursor = '';
    }
  });

  // 重置视图
  const resetBtn = document.getElementById('lineage-reset-view');
  if (resetBtn) {
    const newBtn = resetBtn.cloneNode(true);
    resetBtn.parentNode.replaceChild(newBtn, resetBtn);
    newBtn.addEventListener('click', () => {
      if (lineageNaturalViewBox) {
        svgEl.setAttribute('viewBox', lineageNaturalViewBox);
      }
    });
  }

  // === ノードインタラクション ===
  const allNodes = container.querySelectorAll('.node-group');
  allNodes.forEach(g => {
    g.addEventListener('click', (e) => {
      const researcher = g.dataset.researcher;
      const year = g.dataset.year;
      if (!researcher) return;

      // 编辑模式
      if (lineageEditMode) {
        e.stopPropagation();
        const yearInt = parseInt(year);
        if (!lineageEditSource) {
          lineageEditSource = { researcher, year: yearInt, element: g };
          g.classList.add('node-edit-source');
        } else {
          const src = lineageEditSource;
          g.classList.remove('node-edit-source');
          lineageEditSource = null;
          toggleManualEdge(src.researcher, src.year, researcher, yearInt);
        }
        return;
      }

      // 高亮模式：点击节点切换关联链高亮
      const key = `${researcher}_${year}`;
      if (lineageHighlightNode === key) {
        lineageHighlightNode = null; // 再次点击取消高亮
      } else {
        lineageHighlightNode = key;
      }
      renderLineage();
    });

    g.addEventListener('mouseenter', (e) => {
      const researcher = g.dataset.researcher;
      const year = parseInt(g.dataset.year);
      const node = researcher_nodes.find(n => n.researcher === researcher && n.academic_year === year);
      if (!node) return;
      const circle = g.querySelector('.node-circle');
      if (circle) {
        circle.setAttribute('r', NODE_R * 1.6);
        circle.classList.add('highlighted');
      }
      let html = `<div class="tip-name">${escapeXml(node.researcher)} (${year})</div>`;
      html += `<div class="tip-count">${node.file_count} ${t('lineage.documents')}</div>`;
      if (node.subtopics && node.subtopics.length > 0) html += `<div class="tip-subtopics">${node.subtopics.join(', ')}</div>`;
      if (node.titles && node.titles.length > 0) {
        const shown = node.titles.slice(0, 4);
        html += `<div class="tip-titles">${shown.map(s => escapeXml(s.substring(0, 50))).join('<br/>')}</div>`;
        if (node.titles.length > 4) html += '<div style="color:#999;font-size:0.75rem">...</div>';
      }
      tooltip.innerHTML = html;
      tooltip.classList.remove('hidden');
    });
    g.addEventListener('mousemove', (e) => {
      tooltip.style.left = (e.clientX + 16) + 'px';
      tooltip.style.top = (e.clientY - 12) + 'px';
    });
    g.addEventListener('mouseleave', () => {
      const circle = g.querySelector('.node-circle');
      if (circle) {
        circle.setAttribute('r', NODE_R);
        circle.classList.remove('highlighted');
      }
      tooltip.classList.add('hidden');
    });
  });

  // エッジ hover
  const allPaths = container.querySelectorAll('.edge-line');
  function clearHighlights() {
    allPaths.forEach(ep => ep.classList.remove('edge-dimmed', 'edge-highlighted'));
    allNodes.forEach(en => en.classList.remove('node-dimmed', 'node-highlighted'));
  }
  allPaths.forEach(p => {
    p.addEventListener('mouseenter', () => {
      const srcRes = p.dataset.srcRes;
      const tgtRes = p.dataset.tgtRes;
      const type = p.dataset.type;
      const confidence = p.dataset.confidence;
      const strength = parseInt(p.dataset.strength || '1');
      const typeLabel = t(`lineage.relation_type_${type}`) || type;
      const confLabel = confidence === '确定' || confidence === '確定'
        ? t('lineage.confidence_certain') : t('lineage.confidence_candidate');
      tooltip.innerHTML = `
        <div class="tip-edge">${escapeXml(srcRes)} → ${escapeXml(tgtRes)}</div>
        <div class="tip-type">${typeLabel}</div>
        <div class="tip-confidence">${confLabel}${strength > 1 ? ' (x' + strength + ')' : ''}</div>`;
      tooltip.classList.remove('hidden');
      clearHighlights();
      p.classList.add('edge-highlighted');
      allNodes.forEach(en => {
        const res = en.dataset.researcher;
        if (res === srcRes || res === tgtRes) en.classList.add('node-highlighted');
        else en.classList.add('node-dimmed');
      });
      allPaths.forEach(ep => { if (ep !== p) ep.classList.add('edge-dimmed'); });
    });
    p.addEventListener('mousemove', (e) => {
      tooltip.style.left = (e.clientX + 16) + 'px';
      tooltip.style.top = (e.clientY - 12) + 'px';
    });
    p.addEventListener('mouseleave', () => {
      tooltip.classList.add('hidden');
      clearHighlights();
    });
  });
}

function escapeXml(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');
}

// ---- Lineage manual edge editing ----
async function toggleManualEdge(srcRes, srcYear, tgtRes, tgtYear) {
  try {
    const r = await fetch('/api/lineage/manual-edges');
    const manualEdges = await r.json();
    const existing = manualEdges.find(e =>
      e.source === srcRes && e.source_year === srcYear &&
      e.target === tgtRes && e.target_year === tgtYear
    );
    if (existing) {
      await fetch('/api/lineage/edge', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: srcRes, source_year: srcYear, target: tgtRes, target_year: tgtYear }),
      });
      showToast('手动连线已删除');
    } else {
      await fetch('/api/lineage/edge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: srcRes, source_year: srcYear, target: tgtRes, target_year: tgtYear }),
      });
      showToast('手动连线已添加');
    }
    populateLineage();
  } catch (e) {
    console.error('Toggle edge failed:', e);
    showToast('操作失败', true);
  }
}

// ---- 年筛选变更 ----
function onLineageYearRangeChange() {
  lineageHighlightNode = null;
  renderLineage();
}

// ---- DOMContentLoaded ----
document.addEventListener('DOMContentLoaded', () => {
  initLineageToggles();

  // 规则开关
  const toggleGroup = document.getElementById('lineage-toggles');
  if (toggleGroup) {
    toggleGroup.querySelectorAll('.layer-toggle input[type=checkbox]').forEach(cb => {
      cb.addEventListener('change', () => {
        const type = cb.closest('.layer-toggle').dataset.type;
        if (type) {
          lineageToggles[type] = cb.checked;
          lineageHighlightNode = null;
          renderLineage();
        }
      });
    });
  }

  // Edit按钮
  const btn = document.getElementById('lineage-edit-btn');
  if (btn) {
    btn.addEventListener('click', () => {
      lineageEditMode = !lineageEditMode;
      btn.textContent = lineageEditMode ? 'Done' : 'Edit';
      btn.classList.toggle('active', lineageEditMode);
      document.querySelectorAll('.node-edit-source').forEach(el => el.classList.remove('node-edit-source'));
      lineageEditSource = null;
      if (!lineageEditMode) {
        lineageHighlightNode = null;
        renderLineage();
      }
    });
  }

  // 年范围筛选
  const yearStart = document.getElementById('lineage-year-start');
  const yearEnd = document.getElementById('lineage-year-end');
  if (yearStart) yearStart.addEventListener('change', onLineageYearRangeChange);
  if (yearEnd) yearEnd.addEventListener('change', onLineageYearRangeChange);
});

// --- Language Switching ---
document.querySelectorAll('.lang-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    loadI18n(btn.dataset.lang);
    updateFilterLabels();
    doSearch();
    setTimeout(populateLineage, 100);
  });
});

// --- Toast ---
let toastTimeout = null;
function showToast(msg, isError = false) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.remove('hidden', 'error');
  if (isError) toast.classList.add('error');
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.add('hidden'), 3000);
}

// --- Init ---
async function init() {
  await loadI18n(state.lang);
  await loadConfig();
  document.getElementById('status-msg').textContent = '';
  document.getElementById('search-input').focus();
}

init();
