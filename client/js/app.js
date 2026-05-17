// app.js
let allMangas = [];
let latestChaps = [];
let currentManga = null;
let fromPage = 'inicio';
let chapSortAsc = false;
let activeFilters = { types: [], genres: [], status: [] };
let chapSearchVisible = false;

// ── HELPERS ──────────────────────────────────────────────────────────────────
function imgSrc(src) { return API.imgSrc(src); }
function isAdultEnabled() { return localStorage.getItem('adult_content') === 'true'; }
function filterAdult(list) { return isAdultEnabled() ? list : list.filter(m => !m.metadata?.adult && !m.adult); }

function chLabel(str) {
  const m = String(str).match(/(\d+(?:\.\d+)?)/);
  if (!m) return 'Capítulo ' + str;
  const n = parseFloat(m[1]);
  return 'Capítulo ' + (Number.isInteger(n) ? n : n);
}
function typeClass(tp) { if(tp==='Manhwa')return'bt'; if(tp==='Manhua')return'bpu'; return'bb'; }
function statusBadge(s) {
  if(!s)return'';
  const sl = s.toLowerCase();
  if(sl==='activo'||sl==='en emisión') return`<span class="b s-activo">Activo</span>`;
  if(sl==='hiatus') return`<span class="b s-hiatus">Hiatus</span>`;
  if(sl==='finalizado') return`<span class="b s-finalizado">Finalizado</span>`;
  return`<span class="b bx">${s}</span>`;
}
function showPage(name) {
  document.querySelectorAll('.pg').forEach(p=>{p.style.display='none';p.classList.remove('on');});
  const pg = document.getElementById('p-'+name);
  if(pg){pg.style.display='block';pg.classList.add('on');}
  document.getElementById('cnt').scrollTop=0;
}
function logout() { localStorage.clear(); window.location.href='/login.html'; }
function toggleAdult(el) {
  el.classList.toggle('on');
  localStorage.setItem('adult_content', el.classList.contains('on')?'true':'false');
  renderHome(); renderSeries(); renderRankings(); renderCapitulos();
  renderSearch(document.getElementById('sinput').value);
}

function synopsisHTML(text, id) {
  const lim=130;
  if(!text||text.length<=lim)return`<span>${text||'Sin sinopsis disponible.'}</span>`;
  const uid='syn_'+String(id).replace(/[^a-z0-9]/gi,'_');
  return`<span id="${uid}_s">${text.slice(0,lim)}<span class="det-mas" onclick="expandSyn('${uid}')"> ... más</span></span>
    <span id="${uid}_f" style="display:none;">${text}<span class="det-mas" onclick="collapseSyn('${uid}')"> — menos</span></span>`;
}
function expandSyn(uid){document.getElementById(uid+'_s').style.display='none';document.getElementById(uid+'_f').style.display='inline';}
function collapseSyn(uid){document.getElementById(uid+'_s').style.display='inline';document.getElementById(uid+'_f').style.display='none';}

function isPC(){ return window.innerWidth >= 768; }

// ── CARDS ─────────────────────────────────────────────────────────────────────
function seriesCard(m) {
  const meta = m.metadata || {};
  return`<div class="sc" onclick="openDetail('${encodeURIComponent(m.name)}')">
    <div class="scov">
      ${m.cover?`<img src="${imgSrc(m.cover)}" alt="${m.name}" loading="lazy">`:'<div class="scov-placeholder">M</div>'}
      <div class="scov-top">${meta.adult?'<span class="b b18">+18</span>':''}</div>
      <div class="scov-foot"><div class="scov-title">${m.name}</div></div>
    </div>
    <div class="smeta">
      ${meta.type?`<span class="b ${typeClass(meta.type)}">${meta.type}</span>`:''}
      ${meta.status?statusBadge(meta.status):''}
    </div>
    <div class="scaps">${m.chapterCount} Caps</div>
  </div>`;
}

// ── INICIO ────────────────────────────────────────────────────────────────────
function renderHome() {
  const visible = filterAdult(allMangas);
  const inProgress = visible.filter(m=>{
    const rc = m.progress?.readChapters?.length || 0;
    return rc>0 && rc<m.chapterCount;
  }).slice(0,4);
  document.getElementById('cont-reading').innerHTML = inProgress.length===0
    ?'<p class="loading">Aún no has leído ningún manga.</p>'
    :inProgress.map(m=>{
        const pct=Math.round((m.progress.readChapters.length/m.chapterCount)*100);
        return`<div class="ri" onclick="openDetail('${encodeURIComponent(m.name)}')">
          <div class="rcov">${m.cover?`<img src="${imgSrc(m.cover)}" alt="">`:''}</div>
          <div style="flex:1;min-width:0;overflow:hidden;">
            <div class="ri-title">${m.name}</div>
            <div style="font-size:12px;color:var(--mut);">Cap. ${chLabel(m.progress.lastChapter)} · ${pct}%</div>
            <div class="prbar"><div class="prfill" style="width:${pct}%;"></div></div>
          </div>
        </div>`;
      }).join('');
  const recent=[...visible].filter(m=>m.addedDate).sort((a,b)=>new Date(b.addedDate)-new Date(a.addedDate)).slice(0,4);
  document.getElementById('recent-grid').innerHTML = recent.length?recent.map(seriesCard).join(''):'<p class="empty">No hay mangas recientes.</p>';
}

// ── SERIES ────────────────────────────────────────────────────────────────────
function renderSeries(){ applySeriesFilter(); }
function applySeriesFilter(){
  let list=filterAdult(allMangas);
  const f=activeFilters;
  if(f.types.length) list=list.filter(m=>f.types.includes(m.metadata?.type));
  if(f.genres.length)list=list.filter(m=>(m.metadata?.genres||[]).some(g=>f.genres.includes(g)));
  if(f.status.length)list=list.filter(m=>f.status.includes(m.metadata?.status));
  document.getElementById('sgrid').innerHTML=list.length?list.map(seriesCard).join(''):'<p class="empty">Sin resultados.</p>';
  document.getElementById('scnt').textContent=`Mostrando ${list.length} series`;
  const total=f.types.length+f.genres.length+f.status.length;
  const btn=document.getElementById('filter-btn');
  btn.innerHTML=total>0
    ?`<i class="ti ti-adjustments-horizontal" style="font-size:16px;"></i> Filtrar <span style="background:var(--acc);color:var(--acc-text);border-radius:50%;width:18px;height:18px;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;">${total}</span>`
    :`<i class="ti ti-adjustments-horizontal" style="font-size:16px;"></i> Filtrar`;
}

// ── FILTROS ───────────────────────────────────────────────────────────────────
let pendingFilters = {};
function openFilter(){
  pendingFilters=JSON.parse(JSON.stringify(activeFilters));
  const genreSet=new Set(); allMangas.forEach(m=>(m.metadata?.genres||[]).forEach(g=>genreSet.add(g)));
  document.getElementById('filter-types').innerHTML=['Manga','Manhwa','Manhua'].map(t=>`<button class="filter-chip${pendingFilters.types.includes(t)?' on':''}" onclick="toggleFilterChip(this,'types','${t}')">${t}</button>`).join('');
  document.getElementById('filter-genres').innerHTML=[...genreSet].sort().map(g=>`<button class="filter-chip${pendingFilters.genres.includes(g)?' on':''}" onclick="toggleFilterChip(this,'genres','${g}')">${g}</button>`).join('');
  document.getElementById('filter-status').innerHTML=['Activo','Hiatus','Finalizado'].map(s=>`<button class="filter-chip${pendingFilters.status.includes(s)?' on':''}" onclick="toggleFilterChip(this,'status','${s}')">${s}</button>`).join('');
  document.getElementById('filter-overlay').style.display='block';
  document.getElementById('filter-panel').style.display='block';
}
function toggleFilterChip(el,cat,val){el.classList.toggle('on');const arr=pendingFilters[cat];const i=arr.indexOf(val);i>=0?arr.splice(i,1):arr.push(val);}
function applyFilter(){activeFilters=pendingFilters;closeFilter();applySeriesFilter();}
function clearFilter(){pendingFilters={types:[],genres:[],status:[]};document.querySelectorAll('#filter-types .filter-chip,#filter-genres .filter-chip,#filter-status .filter-chip').forEach(e=>e.classList.remove('on'));}
function closeFilter(){document.getElementById('filter-overlay').style.display='none';document.getElementById('filter-panel').style.display='none';}

// ── RANKINGS ──────────────────────────────────────────────────────────────────
function renderRankings(){
  const visible=filterAdult(allMangas);
  const sorted=[...visible].sort((a,b)=>{const ra=a.metadata?.ranking??9999,rb=b.metadata?.ranking??9999;return ra!==rb?ra-rb:b.chapterCount-a.chapterCount;});
  document.getElementById('rlist').innerHTML=sorted.map((m,i)=>`
    <div class="ri" onclick="openDetail('${encodeURIComponent(m.name)}')">
      <div class="rn${i===0?' t1':''}">${i+1}</div>
      <div class="rcov">${m.cover?`<img src="${imgSrc(m.cover)}" alt="">`:''}</div>
      <div style="flex:1;min-width:0;overflow:hidden;">
        <div class="ri-title">${m.name}</div>
        <div style="font-size:12px;color:var(--mut);margin-top:2px;">${m.chapterCount} caps</div>
        <div style="margin-top:4px;display:flex;gap:5px;">
          ${m.metadata?.type?`<span class="b ${typeClass(m.metadata.type)}">${m.metadata.type}</span>`:''}
          ${m.metadata?.status?statusBadge(m.metadata.status):''}
        </div>
      </div>
    </div>`).join('');
}

// ── CAPÍTULOS ─────────────────────────────────────────────────────────────────
async function renderCapitulos(){
  try{
    const res=await API.fetchRaw('/api/mangas/latest?limit=40');
    if(!res)return;
    latestChaps=await res.json();
    const visible=isAdultEnabled()?latestChaps:latestChaps.filter(c=>!c.adult);
    const grouped={};
    for(const c of visible){
      if(!grouped[c.manga])grouped[c.manga]={manga:c.manga,cover:c.cover,chapters:[],status:c.status};
      grouped[c.manga].chapters.push(c);
    }
    document.getElementById('clist').innerHTML=Object.values(grouped).map(g=>`
      <div class="lci">
        ${g.cover?`<img class="lci-bg" src="${imgSrc(g.cover)}" alt="">`:''}
        <div class="lci-head" onclick="openDetail('${encodeURIComponent(g.manga)}')">
          <div class="lci-cov">${g.cover?`<img src="${imgSrc(g.cover)}" alt="">`:''}</div>
          <div class="lci-title">${g.manga}</div>
          ${statusBadge(g.status)}
        </div>
        ${g.chapters.map(ch=>`
        <div class="lci-ch" onclick="openReader('${encodeURIComponent(g.manga)}','${ch.chapter}')">
          <div class="dot ${ch.read?'r':'u'}" style="margin-right:10px;"></div>
          <div style="flex:1;"><div style="font-size:13px;font-weight:600;color:var(--text);">${chLabel(ch.chapter)}</div></div>
          <div style="font-size:12px;color:var(--mut);display:flex;align-items:center;gap:3px;"><i class="ti ti-calendar" style="font-size:12px;"></i>${ch.dateLabel}</div>
        </div>`).join('')}
      </div>`).join('');
  }catch{document.getElementById('clist').innerHTML='<p class="empty">Error cargando capítulos.</p>';}
}

// ── BÚSQUEDA ──────────────────────────────────────────────────────────────────
function renderSearch(q){
  const query=(q||'').toLowerCase().trim();
  document.getElementById('sclear').style.display=query?'block':'none';
  const base=filterAdult(allMangas);
  if(!query){document.getElementById('sresults').innerHTML='<p style="color:var(--mut);font-size:13px;text-align:center;padding:20px 0;">Escribe para buscar...</p>';return;}
  const results=base.filter(m=>m.name.toLowerCase().includes(query));
  document.getElementById('sresults').innerHTML=results.length
    ?results.map(m=>{const meta=m.metadata||{};return`<div class="sri" onclick="openDetail('${encodeURIComponent(m.name)}')">
        <div class="sri-cov">${m.cover?`<img src="${imgSrc(m.cover)}" alt="" loading="lazy">`:''}</div>
        <div class="sri-info"><div class="sri-name">${m.name}</div><div class="sri-meta">
          ${meta.type?`<span class="b ${typeClass(meta.type)}">${meta.type}</span>`:''}
          ${meta.status?statusBadge(meta.status):''}
          ${(meta.genres||[]).slice(0,2).map(g=>`<span class="b bx">${g}</span>`).join('')}
          ${meta.adult?'<span class="b b18">+18</span>':''}
        </div></div>
        <i class="ti ti-chevron-right" style="color:var(--mut);font-size:16px;flex-shrink:0;"></i>
      </div>`;}).join('')
    :`<p class="empty">Sin resultados para "${q}"</p>`;
}
function clearSearch(){document.getElementById('sinput').value='';renderSearch('');document.getElementById('sinput').focus();}
document.getElementById('sinput').addEventListener('input',e=>renderSearch(e.target.value));

// ── DETALLE ───────────────────────────────────────────────────────────────────
async function openDetail(encodedName){
  const name=decodeURIComponent(encodedName);
  const navActive=document.querySelector('.ni.on[data-p]');
  fromPage=navActive?navActive.dataset.p:'inicio';
  showPage('detail');

  const data=await API.getManga(name);
  if(!data)return;
  currentManga=data;

  const meta=data.metadata||{};
  const src=imgSrc(data.cover);

  if(isPC()){
    // PC layout
    const pcWrap=document.getElementById('det-pc-wrap');
    const pcCover=document.getElementById('det-pc-cover-img');
    const pcCoverPh=document.getElementById('det-pc-cover-ph');
    if(data.cover){if(pcCover){pcCover.src=src;pcCover.style.display='block';}if(pcCoverPh)pcCoverPh.style.display='none';}
    else{if(pcCover)pcCover.style.display='none';if(pcCoverPh)pcCoverPh.style.display='flex';}
    const el=id=>document.getElementById(id);
    if(el('det-pc-title'))el('det-pc-title').textContent=data.name;
    if(el('det-pc-meta'))el('det-pc-meta').innerHTML=[
      meta.type?`<span class="b ${typeClass(meta.type)}">${meta.type}</span>`:'',
      meta.status?statusBadge(meta.status):'',
      meta.adult?'<span class="b b18">+18</span>':'',
      meta.ranking?`<span class="b by">Rank #${meta.ranking}</span>`:''
    ].filter(Boolean).join('');
    if(el('det-pc-genres'))el('det-pc-genres').innerHTML=(meta.genres||[]).map(g=>`<span class="det-pc-genre-chip">${g}</span>`).join('');
    if(el('det-pc-syn'))el('det-pc-syn').innerHTML=synopsisHTML(meta.synopsis,data.name+'_pc');
    const first=data.chapters[0]?.number;
    if(el('det-pc-read-btn'))el('det-pc-read-btn').onclick=()=>first&&openReader(encodeURIComponent(data.name),first);
  } else {
    // Mobile layout
    document.getElementById('det-hero-bg').src=data.cover?src:'';
    document.getElementById('det-hero-cover').src=data.cover?src:'';
    document.getElementById('det-hero-cover').alt=data.name;
    document.getElementById('det-title').textContent=data.name;
    document.getElementById('det-status-row').innerHTML=[
      meta.type?`<span class="b ${typeClass(meta.type)}">${meta.type}</span>`:'',
      meta.status?statusBadge(meta.status):'',
      meta.adult?'<span class="b b18">+18</span>':'',
      meta.ranking?`<span class="b by">Rank #${meta.ranking}</span>`:''
    ].filter(Boolean).join('');
    document.getElementById('det-genres-row').innerHTML=(meta.genres||[]).map(g=>`<span class="b genre-chip">${g}</span>`).join('');
    document.getElementById('det-syn').innerHTML=synopsisHTML(meta.synopsis,data.name);
    const first=data.chapters[0]?.number;
    document.getElementById('det-read-btn').onclick=()=>first&&openReader(encodeURIComponent(data.name),first);
  }

  document.getElementById('chap-count-num').textContent=data.chapterCount;
  renderDetChapList(data);
  syncPCChapSection(data);

  const chapInput=document.getElementById('chap-sinput');
  if(chapInput){
    chapInput.oninput=e=>{
      const q=e.target.value.toLowerCase().trim();
      const filtered=q?data.chapters.filter(ch=>chLabel(ch.number).toLowerCase().includes(q)||ch.number.toLowerCase().includes(q)):data.chapters;
      renderDetChapList({...data,chapters:filtered});
    };
  }
}

function renderDetChapList(manga){
  const chapters=chapSortAsc?[...manga.chapters]:[...manga.chapters].reverse();
  const coverSrc=manga.cover?imgSrc(manga.cover):null;
  document.getElementById('det-chaplist').innerHTML=chapters.map(ch=>`
    <div class="ch-row" onclick="openReader('${encodeURIComponent(manga.name)}','${ch.number}')">
      ${coverSrc?`<img class="ch-row-bg" src="${coverSrc}" alt="">`:''}
      <div class="dot ${ch.read?'r':'u'}"></div>
      <div class="ch-info">
        <div class="ch-num">${chLabel(ch.number)}</div>
        <div class="ch-tr">${ch.imageCount} páginas${ch.dateLabel?' · '+ch.dateLabel:''}</div>
      </div>
      <div class="ch-date"><i class="ti ti-chevron-right" style="font-size:16px;color:var(--mut);"></i></div>
    </div>`).join('');
}

function toggleSort(){chapSortAsc=!chapSortAsc;if(currentManga)renderDetChapList(currentManga);}
function toggleChapSearch(){
  chapSearchVisible=!chapSearchVisible;
  document.getElementById('chap-search-bar').style.display=chapSearchVisible?'block':'none';
  if(chapSearchVisible)document.getElementById('chap-sinput').focus();
}
function closeDetail(){showPage(fromPage);}
function openReader(encodedManga,chapter){window.location.href=`/reader.html?manga=${encodedManga}&chapter=${encodeURIComponent(chapter)}`;}

// ── NAVEGACIÓN ────────────────────────────────────────────────────────────────
document.querySelectorAll('.ni[data-p]').forEach(b=>{
  b.addEventListener('click',()=>{
    document.querySelectorAll('.ni').forEach(x=>x.classList.remove('on'));
    b.classList.add('on');
    showPage(b.dataset.p);
  });
});
document.getElementById('ubtn').addEventListener('click',()=>{document.getElementById('upanel').classList.add('on');document.getElementById('ov').classList.add('on');});
['cpanel','ov'].forEach(id=>document.getElementById(id).addEventListener('click',()=>{document.getElementById('upanel').classList.remove('on');document.getElementById('ov').classList.remove('on');}));

// ── TEMAS ─────────────────────────────────────────────────────────────────────
function setTheme(theme){
  const themes=['theme-gilded-forest','theme-ember-dark','theme-arctic-ink','theme-lunar-tide','theme-void-matrix'];
  themes.forEach(t=>document.body.classList.remove(t));
  if(theme)document.body.classList.add(theme);
  localStorage.setItem('manga_theme',theme);
  document.querySelectorAll('.theme-btn').forEach(btn=>btn.classList.toggle('active',btn.dataset.theme===theme));
}
function loadTheme(){setTheme(localStorage.getItem('manga_theme')||'');}
loadTheme();

// Inicializar UI de usuario
function initUserUI(){
  const username=API.getUsername();
  const role=API.getRole();
  document.querySelectorAll('.user-name-display').forEach(el=>el.textContent=username);
  document.querySelectorAll('.user-role-display').forEach(el=>el.textContent=role==='admin'?'Administrador':'Lector');
  document.querySelectorAll('.avatar').forEach(el=>el.textContent=username.slice(0,2).toUpperCase());
  const adminLinks=document.querySelectorAll('.admin-only');
  adminLinks.forEach(el=>el.style.display=API.isAdmin()?'':'none');
}

if(isAdultEnabled())document.getElementById('toggle-adult')?.classList.add('on');

// ── INIT ──────────────────────────────────────────────────────────────────────
async function init(){
  try{
    initUserUI();
    allMangas=await API.getMangas();
    if(!allMangas||allMangas.length===0){
      ['sgrid','rlist','cont-reading','recent-grid'].forEach(id=>{const el=document.getElementById(id);if(el)el.innerHTML='<p class="empty">No se encontraron mangas. Configura MANGA_PATH en el .env</p>';});
      return;
    }
    renderHome(); renderSeries(); renderRankings(); renderCapitulos();
    const urlParams=new URLSearchParams(window.location.search);
    const gotoManga=urlParams.get('goto');
    if(gotoManga){window.history.replaceState({},'','/');await openDetail(encodeURIComponent(gotoManga));}
  }catch(err){console.error('Error iniciando app:',err);}
}
window.addEventListener('load',()=>setTimeout(init,300));

// ── Sync PC chapter section ───────────────────────────────────────────────────
function syncPCChapSection(data){
  const el = id => document.getElementById(id);
  if(el('chap-count-num-pc')) el('chap-count-num-pc').textContent = data.chapterCount;
  // Render chapters in PC list too
  if(el('det-chaplist-pc')) {
    const chapters = chapSortAsc ? [...data.chapters] : [...data.chapters].reverse();
    const coverSrc = data.cover ? imgSrc(data.cover) : null;
    el('det-chaplist-pc').innerHTML = chapters.map(ch=>`
      <div class="ch-row" onclick="openReader('${encodeURIComponent(data.name)}','${ch.number}')">
        ${coverSrc?`<img class="ch-row-bg" src="${coverSrc}" alt="">`:''}
        <div class="dot ${ch.read?'r':'u'}"></div>
        <div class="ch-info">
          <div class="ch-num">${chLabel(ch.number)}</div>
          <div class="ch-tr">${ch.imageCount} páginas${ch.dateLabel?' · '+ch.dateLabel:''}</div>
        </div>
        <div class="ch-date"><i class="ti ti-chevron-right" style="font-size:16px;color:var(--mut);"></i></div>
      </div>`).join('');
    // Chapter search in PC
    const pcInput = el('chap-sinput-pc');
    if(pcInput) {
      pcInput.oninput = e => {
        const q = e.target.value.toLowerCase().trim();
        const filtered = q ? data.chapters.filter(ch=>chLabel(ch.number).toLowerCase().includes(q)||ch.number.toLowerCase().includes(q)) : data.chapters;
        const chapters2 = chapSortAsc ? [...filtered] : [...filtered].reverse();
        el('det-chaplist-pc').innerHTML = chapters2.map(ch=>`
          <div class="ch-row" onclick="openReader('${encodeURIComponent(data.name)}','${ch.number}')">
            ${coverSrc?`<img class="ch-row-bg" src="${coverSrc}" alt="">`:''}
            <div class="dot ${ch.read?'r':'u'}"></div>
            <div class="ch-info"><div class="ch-num">${chLabel(ch.number)}</div><div class="ch-tr">${ch.imageCount} páginas${ch.dateLabel?' · '+ch.dateLabel:''}</div></div>
            <div class="ch-date"><i class="ti ti-chevron-right" style="font-size:16px;color:var(--mut);"></i></div>
          </div>`).join('');
      };
    }
  }
}

function toggleChapSearchPC(){
  const bar = document.getElementById('chap-search-bar-pc');
  if(!bar) return;
  const visible = bar.style.display === 'block';
  bar.style.display = visible ? 'none' : 'block';
  if(!visible) document.getElementById('chap-sinput-pc')?.focus();
}
