// ── detail.js — vista de detalle de manga ────────────────────────────────────

const g = id => document.getElementById(id);

function clearDetail() {
  if (isPC()) {
    if(g('det-pc-title'))    g('det-pc-title').innerHTML   = '<div class="sk-line sk-shine" style="width:60%;height:22px;border-radius:8px;"></div>';
    if(g('det-pc-syn'))      g('det-pc-syn').innerHTML     = '<div class="sk-line sk-shine" style="width:100%;height:11px;border-radius:5px;margin-bottom:6px;"></div>';
    if(g('det-pc-genres'))   g('det-pc-genres').innerHTML  = '';
    if(g('det-pc-meta'))     g('det-pc-meta').innerHTML    = '';
    if(g('det-chaplist-pc')) g('det-chaplist-pc').innerHTML = skChapRows(8);
    const pc = g('det-pc-cover-img');
    if(pc) { pc.src = BLANK; pc.style.opacity = '0'; }
  } else {
    const bg      = g('det-hero-bg');
    const cover   = g('det-hero-cover');
    const diamond = g('det-hero-diamond');
    if(bg)      bg.src = BLANK;
    if(cover)  { cover.src = BLANK; cover.style.opacity = '0'; cover.onload = null; }
    if(diamond) diamond.style.opacity = '1';
    if(g('det-title'))      g('det-title').innerHTML      = '<div class="sk-line sk-shine" style="width:55%;height:20px;border-radius:8px;margin:0 auto;"></div>';
    if(g('det-status-row')) g('det-status-row').innerHTML = '';
    if(g('det-genres-row')) g('det-genres-row').innerHTML = '';
    if(g('det-syn'))        g('det-syn').innerHTML        = '<div class="sk-line sk-shine" style="width:100%;height:10px;border-radius:5px;margin-bottom:6px;"></div>';
    if(g('det-chaplist'))   g('det-chaplist').innerHTML   = skChapRows(8);
    if(g('chap-count-num')) g('chap-count-num').textContent = '—';
  }
}

async function openDetail(encodedName) {
  const name = decodeURIComponent(encodedName);
  const navActive = document.querySelector('.ni.on[data-p]');
  fromPage = navActive ? navActive.dataset.p : 'inicio';
  history.pushState({page:'detail', manga:name}, '');
  clearDetail();
  showPage('detail');

  const data = await API.getManga(name);
  if (!data) return;
  currentManga = data;

  const pcLayout   = g('det-pc-layout');
  const mobileHero = g('det-mobile-hero');
  const mobileBody = g('det-mobile-body');
  const pc         = isPC();

  if(pcLayout)   pcLayout.style.display   = pc ? 'block' : 'none';
  if(mobileHero) mobileHero.style.display = pc ? 'none'  : '';
  if(mobileBody) mobileBody.style.display = pc ? 'none'  : '';

  const meta = data.metadata || {};
  const src  = imgSrc(data.cover);

  if (pc) {
    const pcImg = g('det-pc-cover-img');
    const pcPh  = g('det-pc-cover-ph');
    if (data.cover && pcImg) {
      pcImg.onload = () => { pcImg.style.opacity = '1'; };
      pcImg.src = src; pcImg.style.display = 'block';
      if(pcPh) pcPh.style.display = 'none';
    } else {
      if(pcImg) pcImg.style.display = 'none';
      if(pcPh)  pcPh.style.display  = 'flex';
    }
    if(g('det-pc-title'))  g('det-pc-title').textContent  = data.name;
    if(g('det-pc-meta'))   g('det-pc-meta').innerHTML     = badgeRow(meta);
    if(g('det-pc-genres')) g('det-pc-genres').innerHTML   = genreChips(meta, 'det-pc-genre-chip');
    if(g('det-pc-syn'))    g('det-pc-syn').innerHTML      = synopsisHTML(meta.synopsis, data.name+'_pc');
    const first = data.chapters[0]?.number;
    if(g('det-pc-read-btn')) g('det-pc-read-btn').onclick = () => first && openReader(encodeURIComponent(data.name), first);
    if(g('chap-count-num-pc')) g('chap-count-num-pc').textContent = data.chapterCount;
  } else {
    const heroBg      = g('det-hero-bg');
    const heroCover   = g('det-hero-cover');
    const heroDiamond = g('det-hero-diamond');
    if(heroBg) heroBg.src = data.cover ? src : BLANK;
    if(heroCover) {
      heroCover.alt = data.name;
      if(data.cover) {
        heroCover.onload = () => { heroCover.style.opacity='1'; if(heroDiamond) heroDiamond.style.opacity='0'; };
        heroCover.src = src;
      } else { heroCover.src = BLANK; heroCover.style.opacity='0'; if(heroDiamond) heroDiamond.style.opacity='0'; }
    }
    if(g('det-title'))      g('det-title').textContent      = data.name;
    if(g('det-status-row')) g('det-status-row').innerHTML   = badgeRow(meta);
    if(g('det-genres-row')) g('det-genres-row').innerHTML   = genreChips(meta, 'b genre-chip');
    if(g('det-syn'))        g('det-syn').innerHTML          = synopsisHTML(meta.synopsis, data.name);
    if(g('chap-count-num')) g('chap-count-num').textContent = data.chapterCount;
    const first = data.chapters[0]?.number;
    if(g('det-read-btn')) g('det-read-btn').onclick = () => first && openReader(encodeURIComponent(data.name), first);
  }

  renderDetChapList(data);

  const chapInput = g('chap-sinput');
  if(chapInput) {
    chapInput.oninput = e => {
      const q = e.target.value.toLowerCase().trim();
      const filtered = q ? data.chapters.filter(ch => chLabel(ch.number).toLowerCase().includes(q)||ch.number.toLowerCase().includes(q)) : data.chapters;
      renderDetChapList({...data, chapters: filtered});
    };
  }
}

function badgeRow(meta) {
  return [
    meta.type    ? `<span class="${typeClass(meta.type)}">${meta.type}</span>`  : '',
    meta.status  ? statusBadge(meta.status)                                      : '',
    meta.adult   ? '<span class="b b18">+18</span>'                             : '',
    meta.ranking ? `<span class="b by">Rank #${meta.ranking}</span>`            : ''
  ].filter(Boolean).join('');
}
function sortGenres(genres) {
  return [...genres].sort((a, b) => {
    const priority = s => {
      const c = (s || '').trim()[0];
      if (!c) return 3;
      if (/[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]/.test(c)) return 2; // letras al final
      if (/[0-9]/.test(c)) return 1;                    // números en el medio
      return 0;                                           // símbolos primero
    };
    const pa = priority(a), pb = priority(b);
    if (pa !== pb) return pa - pb;
    return a.localeCompare(b, 'es', { sensitivity: 'base' });
  });
}
function genreChips(meta, cls) {
  return sortGenres(meta.genres || []).map(g => `<span class="${cls}">${g}</span>`).join('');
}

function renderDetChapList(manga) {
  const chapters = chapSortAsc ? [...manga.chapters] : [...manga.chapters].reverse();
  const coverSrc = manga.cover ? imgSrc(manga.cover) : null;
  const chRowHTML = ch => `
    <div class="ch-row" onclick="openReader('${encodeURIComponent(manga.name)}','${ch.number}')">
      ${coverSrc?`<img class="ch-row-bg" src="${coverSrc}" alt="">`:''}
      <div class="dot ${ch.read?'r':'u'}" onclick="toggleReadChapter('${ch.number}',event)" title="${ch.read?'Marcar no leído':'Marcar leído'}" style="cursor:pointer;"></div>
      <div class="ch-info">
        <div class="ch-num">${chLabel(ch.number)}</div>
        <div class="ch-tr">${ch.imageCount} páginas${ch.dateLabel?' · '+ch.dateLabel:''}</div>
      </div>
      <div class="ch-date"><i class="ti ti-chevron-right" style="font-size:16px;color:var(--mut);"></i></div>
    </div>`;
  if (g('det-chaplist'))   g('det-chaplist').innerHTML   = chapters.map(chRowHTML).join('');
  if (g('det-chaplist-pc')) g('det-chaplist-pc').innerHTML = chapters.map(chRowHTML).join('');
}

function toggleSort() { chapSortAsc = !chapSortAsc; if (currentManga) renderDetChapList(currentManga); }

async function toggleReadChapter(chapterNum, e) {
  e.stopPropagation();
  if (!currentManga) return;
  const mangaName = currentManga.name;
  const prog      = currentManga.progress || {};
  const readChaps = prog.readChapters || [];
  const isRead    = readChaps.includes(chapterNum);
  try {
    if (isRead) {
      await fetch('/api/mangas/unread', { method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+API.getToken()}, body: JSON.stringify({ manga: mangaName, chapter: chapterNum }) });
      prog.readChapters = readChaps.filter(c => c !== chapterNum);
    } else {
      await fetch('/api/mangas/progress', { method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+API.getToken()}, body: JSON.stringify({ manga: mangaName, chapter: chapterNum, page: 0 }) });
      prog.readChapters = [...readChaps, chapterNum];
    }
    currentManga.progress = prog;
    API.invalidateManga(mangaName);
    renderDetChapList(currentManga);
  } catch(err) { console.error('Error toggling read:', err); }
}

function toggleChapSearch() {
  chapSearchVisible = !chapSearchVisible;
  document.getElementById('chap-search-bar').style.display = chapSearchVisible ? 'block' : 'none';
  if (chapSearchVisible) document.getElementById('chap-sinput').focus();
}
function toggleChapSearchPC() {
  const bar = document.getElementById('chap-search-bar-pc');
  if (!bar) return;
  const visible = bar.style.display === 'block';
  bar.style.display = visible ? 'none' : 'block';
  if (!visible) document.getElementById('chap-sinput-pc')?.focus();
}

const pcChapInput = document.getElementById('chap-sinput-pc');
if (pcChapInput) {
  pcChapInput.addEventListener('input', e => {
    if (!currentManga) return;
    const q = e.target.value.toLowerCase().trim();
    const filtered = q ? currentManga.chapters.filter(ch => chLabel(ch.number).toLowerCase().includes(q)||ch.number.toLowerCase().includes(q)) : currentManga.chapters;
    if (g('det-chaplist-pc')) {
      const chapters = chapSortAsc ? [...filtered] : [...filtered].reverse();
      const coverSrc = currentManga.cover ? imgSrc(currentManga.cover) : null;
      g('det-chaplist-pc').innerHTML = chapters.map(ch => `
        <div class="ch-row" onclick="openReader('${encodeURIComponent(currentManga.name)}','${ch.number}')">
          ${coverSrc?`<img class="ch-row-bg" src="${coverSrc}" alt="">`:''}
          <div class="dot ${ch.read?'r':'u'}" onclick="toggleReadChapter('${ch.number}',event)" style="cursor:pointer;"></div>
          <div class="ch-info"><div class="ch-num">${chLabel(ch.number)}</div><div class="ch-tr">${ch.imageCount} páginas${ch.dateLabel?' · '+ch.dateLabel:''}</div></div>
          <div class="ch-date"><i class="ti ti-chevron-right" style="font-size:16px;color:var(--mut);"></i></div>
        </div>`).join('');
    }
  });
}

function closeDetail() {
  // history.back() sincroniza con el historial del browser
  // popstate se encargará de llamar showPage(fromPage)
  history.back();
}
function openReader(encodedManga, chapter) { window.location.href = `/reader.html?manga=${encodedManga}&chapter=${encodeURIComponent(chapter)}`; }

function openChapConfig()  { document.getElementById('chap-cfg-overlay').style.display='block'; document.getElementById('chap-cfg-panel').style.display='block'; }
function closeChapConfig() { document.getElementById('chap-cfg-overlay').style.display='none'; document.getElementById('chap-cfg-panel').style.display='none'; }

async function markAllChaptersRead() {
  if (!currentManga) return;
  try {
    await fetch('/api/mangas/mark-all-read', { method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+API.getToken()}, body: JSON.stringify({manga: currentManga.name}) });
    const allNums = currentManga.chapters.map(c => c.number);
    currentManga.chapters = currentManga.chapters.map(ch => ({...ch, read:true}));
    if (!currentManga.progress) currentManga.progress = {};
    currentManga.progress.readChapters = allNums;
    API.invalidateManga(currentManga.name);
    renderDetChapList(currentManga);
  } catch(e) { console.error('Error marcando como leídos:', e); }
  closeChapConfig();
}
async function unmarkAllChaptersRead() {
  if (!currentManga) return;
  try {
    await fetch('/api/mangas/unread-all', { method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+API.getToken()}, body: JSON.stringify({manga: currentManga.name}) });
    currentManga.chapters = currentManga.chapters.map(ch => ({...ch, read:false}));
    if (!currentManga.progress) currentManga.progress = {};
    currentManga.progress.readChapters = [];
    API.invalidateManga(currentManga.name);
    renderDetChapList(currentManga);
  } catch(e) { console.error('Error desmarcando capítulos:', e); }
  closeChapConfig();
}
