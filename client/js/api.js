// ── NORMALIZACIÓN (acentos + mayúsculas) ──────────────────────────────────────
// Antes vivía solo en app.js, así que la búsqueda principal, el buscador de
// géneros y el buscador de capítulos ya ignoraban acentos y mayúsculas al
// comparar ("busqueda" encuentra "Búsqueda"), pero el buscador de manga a
// vetar (users.html), el editor de metadata y el editor de seguimiento — que
// no cargan app.js, solo api.js — no tenían esta función y comparaban texto
// tal cual, exigiendo la tilde exacta. Vive acá porque api.js es el único
// archivo que cargan las 7 páginas del cliente.
function norm(str) { return String(str).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim(); }

// ── API CLIENT CON CACHÉ EN MEMORIA ──────────────────────────────────────────
const API = {
  getToken()    { return localStorage.getItem('manga_token'); },
  // Antes esto dependía 100% de que la verificación asíncrona (la IIFE al
  // final de este archivo) ya hubiera guardado manga_username en
  // localStorage. Si algo llamaba a getUsername() ANTES de que esa
  // verificación contestara — más probable cuanto más lenta la red (ej.
  // Tailscale vs LAN) — caía al valor genérico 'Usuario', y con eso la
  // caché/ETag de la lista de mangas quedaba guardada bajo una clave
  // compartida entre cualquier cuenta que pisara esa carrera. Ahora, si
  // todavía no está en localStorage, se decodifica directo del JWT (que ya
  // está disponible al instante, sin red) — el username viaja en el propio
  // token desde el login (ver server/routes/auth.js, jwt.sign).
  getUsername() {
    const stored = localStorage.getItem('manga_username');
    if (stored) return stored;
    return this._usernameFromToken() || 'Usuario';
  },
  _usernameFromToken() {
    try {
      const token = this.getToken();
      if (!token) return null;
      const b64  = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      // atob() por sí solo da bytes crudos, no texto UTF-8 — sin este paso,
      // un username con tildes/ñ (algo bien común acá) queda ilegible.
      const json = decodeURIComponent(atob(b64).split('').map(c =>
        '%' + c.charCodeAt(0).toString(16).padStart(2, '0')
      ).join(''));
      return JSON.parse(json).username || null;
    } catch { return null; }
  },
  getRole()     { return localStorage.getItem('manga_role') || 'reader'; },
  isAdmin()     { return this.getRole() === 'admin'; },

  headers() {
    return { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + this.getToken() };
  },
  // El token YA NO viaja en la URL de la imagen. Desde que existe la
  // cookie httpOnly 'img_token' (se establece al iniciar sesión — ver
  // login.html y la auto-verificación más abajo, y server/middleware/
  // cookieOrHeaderAuth.js del lado del servidor, que revisa esa cookie
  // ANTES que un ?token= en la URL), agregarlo acá era redundante y
  // dejaba el JWT pegado en el historial del navegador y en cualquier
  // log de acceso que se agregue en el futuro. Si algún día hace falta
  // un fallback explícito (ej. la cookie fue bloqueada), agregarlo acá
  // de nuevo — pero como fallback condicional, no siempre.
  imgSrc(src) {
    return src || '';
  },

  // Igual que imgSrc, pero pide la versión miniatura (WebP, ~400px) en vez
  // de la portada completa — para tarjetas de grilla, nunca para el lector.
  // Si el server no puede generar la miniatura (sharp no instalado, etc.)
  // devuelve la portada completa igual, así que no hay riesgo de romper nada
  // usando esto de más.
  thumbSrc(src) {
    if (!src) return '';
    return src + (src.includes('?') ? '&' : '?') + 'thumb=1';
  },

  // ── CACHÉ EN MEMORIA ───────────────────────────────────────────────────────
  _cache: new Map(),
  _cacheTime: new Map(),
  MANGA_LIST_TTL:   5 * 60 * 1000,  // 5 min para la lista
  MANGA_DETAIL_TTL: 3 * 60 * 1000,  // 3 min para detalle individual

  _cacheGet(key, ttl) {
    const t = this._cacheTime.get(key);
    if (t && (Date.now() - t) < ttl) return this._cache.get(key);
    return null;
  },
  _cacheSet(key, value) {
    this._cache.set(key, value);
    this._cacheTime.set(key, Date.now());
  },
  invalidate(key) {
    this._cache.delete(key);
    this._cacheTime.delete(key);
  },
  invalidateAll() {
    this._cache.clear();
    this._cacheTime.clear();
  },

  // ── FETCH BASE ─────────────────────────────────────────────────────────────
  async fetchRaw(url, options = {}) {
    try {
      const res = await fetch(url, { ...options, headers: { ...this.headers(), ...(options.headers || {}) } });
      if (res.status === 401 || res.status === 403) {
        localStorage.clear(); window.location.href = '/login.html'; return null;
      }
      return res;
    } catch(e) { console.error('API fetch error:', e); return null; }
  },

  // Claves de localStorage por usuario: si en el mismo navegador se cambia
  // de cuenta sin pasar por logout() (ej. dos pestañas), cada usuario tiene
  // su propio slot y nunca reusa el listado de otro.
  _listCacheKey() { return 'manga_list_cache_' + this.getUsername(); },
  _listEtagKey()  { return 'manga_list_etag_'  + this.getUsername(); },

  // ── LISTA DE MANGAS (con caché en memoria + ETag) ─────────────────────────
  async getMangas() {
    const cached = this._cacheGet('mangas', this.MANGA_LIST_TTL);
    if (cached) return cached;
    const headers = { ...this.headers() };
    const etag = localStorage.getItem(this._listEtagKey());
    if (etag) headers['If-None-Match'] = etag;
    try {
      const res = await fetch('/api/mangas', { headers });
      if (res.status === 304) {
        // Servidor dice que no cambió: usar lo que hay en localStorage
        const stored = localStorage.getItem(this._listCacheKey());
        if (stored) {
          const data = JSON.parse(stored);
          this._cacheSet('mangas', data);
          return data;
        }
      }
      if (!res.ok) return [];
      const newEtag = res.headers.get('ETag');
      if (newEtag) localStorage.setItem(this._listEtagKey(), newEtag);
      const data = await res.json();
      this._cacheSet('mangas', data);
      // Persistir en localStorage para el próximo arranque (máx ~2MB)
      try { localStorage.setItem(this._listCacheKey(), JSON.stringify(data)); } catch {}
      return data;
    } catch(e) {
      console.error('getMangas error:', e);
      // Fallback a localStorage si falla la red
      const stored = localStorage.getItem(this._listCacheKey());
      return stored ? JSON.parse(stored) : [];
    }
  },

  // ── DETALLE DE MANGA (con caché en memoria) ────────────────────────────────
  async getManga(name) {
    const key = 'manga_' + name;
    const cached = this._cacheGet(key, this.MANGA_DETAIL_TTL);
    if (cached) return cached;
    const headers = { ...this.headers() };
    const etag = sessionStorage.getItem('etag_' + name);
    if (etag) headers['If-None-Match'] = etag;
    try {
      const res = await fetch(`/api/mangas/${encodeURIComponent(name)}`, { headers });
      if (res.status === 304) {
        const stored = sessionStorage.getItem('detail_' + name);
        if (stored) {
          const data = JSON.parse(stored);
          this._cacheSet(key, data);
          return data;
        }
      }
      if (!res.ok) return null;
      const newEtag = res.headers.get('ETag');
      if (newEtag) sessionStorage.setItem('etag_' + name, newEtag);
      const data = await res.json();
      this._cacheSet(key, data);
      try { sessionStorage.setItem('detail_' + name, JSON.stringify(data)); } catch {}
      // Si "name" era en realidad un slug, la entry real vive en data.name —
      // cachearla ahí también evita pedirla de nuevo la próxima vez que se
      // acceda por su nombre real (ej. desde una tarjeta normal del listado).
      if (data.name && data.name !== name) this._cacheSet('manga_' + data.name, data);
      return data;
    } catch(e) {
      console.error('getManga error:', e);
      const stored = sessionStorage.getItem('detail_' + name);
      return stored ? JSON.parse(stored) : null;
    }
  },

  // Invalidar caché de un manga específico (tras marcar leído, etc.)
  invalidateManga(name) {
    this.invalidate('manga_' + name);
    this.invalidate('mangas');
    sessionStorage.removeItem('etag_' + name);
    sessionStorage.removeItem('detail_' + name);
    localStorage.removeItem(this._listEtagKey());
  },


  // Usuarios (admin)
  async getUsers()           { const r = await this.fetchRaw('/api/users'); return r ? r.json() : []; },
  async createUser(u, p, role, restrictions = {}) { const r = await this.fetchRaw('/api/users', { method: 'POST', body: JSON.stringify({ username: u, password: p, role, ...restrictions }) }); return r ? r.json() : null; },
  async updateUser(id, data)   { const r = await this.fetchRaw(`/api/users/${id}`, { method: 'PUT', body: JSON.stringify(data) }); return r ? r.json() : null; },
  async deleteUser(id)         { const r = await this.fetchRaw(`/api/users/${id}`, { method: 'DELETE' }); return r ? r.json() : null; },

  // Scraper (admin) — implementado del lado server en la Fase 2
  async getScraperStatus()     { const r = await this.fetchRaw('/api/admin/scraper/status'); return r ? r.json() : null; },
  async startScraper(mode)     { const r = await this.fetchRaw('/api/admin/scraper/start', { method: 'POST', body: JSON.stringify({ mode }) }); return r ? r.json() : null; },
  async stopScraper(force = false) { const r = await this.fetchRaw('/api/admin/scraper/stop', { method: 'POST', body: JSON.stringify({ force }) }); return r ? r.json() : null; },
  async getFuentesActivas()        { const r = await this.fetchRaw('/api/admin/scraper/fuentes'); return r ? r.json() : null; },
  async setFuentesActivas(cambios) { const r = await this.fetchRaw('/api/admin/scraper/fuentes', { method: 'POST', body: JSON.stringify({ fuentes: cambios }) }); return r ? r.json() : null; },
  async getOrdenFuentes()          { const r = await this.fetchRaw('/api/admin/scraper/orden-fuentes'); return r ? r.json() : null; },
  async setOrdenFuentes(orden)     { const r = await this.fetchRaw('/api/admin/scraper/orden-fuentes', { method: 'POST', body: JSON.stringify({ orden }) }); return r ? r.json() : null; },
  async resetOrdenFuentes()        { const r = await this.fetchRaw('/api/admin/scraper/orden-fuentes', { method: 'POST', body: JSON.stringify({ resetear: true }) }); return r ? r.json() : null; },
  async getSeguimientoMangas()     { const r = await this.fetchRaw('/api/admin/scraper/mangas'); return r ? r.json() : null; },
  async addSeguimientoManga(manga) { const r = await this.fetchRaw('/api/admin/scraper/mangas', { method: 'POST', body: JSON.stringify(manga) }); return r ? r.json() : null; },
  async editSeguimientoManga(originalNombreCarpeta, manga, forzar = false) { const body = { original_nombre_carpeta: originalNombreCarpeta, manga }; if (forzar) body.forzar = true; const r = await this.fetchRaw('/api/admin/scraper/mangas', { method: 'PUT', body: JSON.stringify(body) }); return r ? r.json() : null; },
  async deleteSeguimientoManga(nombreCarpeta) { const r = await this.fetchRaw('/api/admin/scraper/mangas', { method: 'DELETE', body: JSON.stringify({ nombre_carpeta: nombreCarpeta }) }); return r ? r.json() : null; },
  async getDominiosFuente()        { const r = await this.fetchRaw('/api/admin/scraper/dominios'); return r ? r.json() : null; },

  // ── EXPORT / IMPORT DE PROGRESO ─────────────────────────────────────────
  // Compartido entre el panel principal (app.js) y stats.html — antes cada
  // uno tenía su propia copia casi idéntica (con una diferencia real: la de
  // stats.html no tenía try/catch alrededor del export). Acá vive el fetch,
  // el armado del archivo y el parseo, una sola vez; cada página se queda
  // con su propio toast y con decidir qué refrescar después de importar.

  // Dispara la descarga del archivo .json de progreso. Tira una excepción
  // si la respuesta no fue exitosa — el llamador decide cómo avisar el error.
  async exportProgress() {
    const r = await this.fetchRaw('/api/mangas/progress/export');
    if (!r || !r.ok) throw new Error('No se pudo exportar el progreso.');
    const blob = await r.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `progreso-manga-${new Date().toISOString().slice(0,10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },

  // file: el File del <input type="file">. merge: true = combinar con lo
  // existente, false/undefined = reemplazar. Devuelve { ok, imported, error }
  // tal cual lo manda el servidor — nunca tira por una respuesta de error,
  // solo si el archivo no es JSON válido (el llamador ya lo espera en su
  // propio try/catch, igual que antes).
  async importProgress(file, merge) {
    const text = await file.text();
    const json = JSON.parse(text);
    const progress = json.progress || json; // soporta el formato viejo (sin envolver) y el nuevo
    const r = await this.fetchRaw('/api/mangas/progress/import', {
      method: 'POST',
      body: JSON.stringify({ progress, merge: !!merge })
    });
    if (!r) return { ok: false, error: 'Error de conexión con el servidor.' };
    return r.json();
  },
};

// ── API.ready: se resuelve cuando termina la auto-verificación de abajo ──────
// Antes, cada página que necesitaba saber el rol del usuario ANTES de decidir
// qué mostrar (los 4 paneles admin: users/scraper/seguimiento/metadata) tenía
// que adivinar cuánto iba a tardar la verificación con un setTimeout fijo de
// 400ms. En una red más lenta que de costumbre (Tailscale sobre WAN, celular
// con mala señal) esos 400ms no siempre alcanzaban, y un admin real terminaba
// expulsado del panel porque getRole() todavía no tenía la respuesta guardada.
// API.ready es una promesa real: se resuelve recién cuando la verificación de
// abajo terminó (haya salido bien, mal, o directo no haya corrido porque no
// había token) — nunca antes, sin importar cuánto tarde la red. Se resuelve
// siempre en el `finally`, así ningún `await API.ready` se queda colgado para
// siempre pase lo que pase en el try de adentro.
let _resolveApiReady;
API.ready = new Promise(resolve => { _resolveApiReady = resolve; });

// ── AUTO-VERIFICACIÓN AL CARGAR ───────────────────────────────────────────────
(async () => {
  try {
    const token = API.getToken();
    if (window.location.pathname.includes('login.html')) return;
    if (!token) { window.location.href = '/login.html'; return; }
    try {
      const res = await fetch('/api/verify', { headers: { Authorization: 'Bearer ' + token } });
      if (!res.ok) { localStorage.clear(); window.location.href = '/login.html'; return; }
      const data = await res.json();
      if (data.username) localStorage.setItem('manga_username', data.username);
      if (data.role)     localStorage.setItem('manga_role', data.role);
      // Establecer cookie HttpOnly para imágenes. Se espera (antes era
      // fire-and-forget) para que cualquier página que haga
      // "await API.ready" tenga la garantía real de que la cookie ya está
      // puesta antes de pedir la primera imagen — las URLs de imagen ya
      // no llevan el token como respaldo (ver imgSrc más abajo).
      await fetch('/api/set-img-cookie', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
        body: JSON.stringify({ token })
      }).catch(() => {});
    } catch {}
  } finally {
    _resolveApiReady();
  }
})();
