// ── API CLIENT CON CACHÉ EN MEMORIA ──────────────────────────────────────────
const API = {
  getToken()    { return localStorage.getItem('manga_token'); },
  getUsername() { return localStorage.getItem('manga_username') || 'Usuario'; },
  getRole()     { return localStorage.getItem('manga_role') || 'reader'; },
  isAdmin()     { return this.getRole() === 'admin'; },

  headers() {
    return { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + this.getToken() };
  },
  imgSrc(src) {
    if (!src) return '';
    return src + (src.includes('?') ? '&' : '?') + 'token=' + this.getToken();
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

  // ── LISTA DE MANGAS (con caché en memoria + ETag) ─────────────────────────
  async getMangas() {
    const cached = this._cacheGet('mangas', this.MANGA_LIST_TTL);
    if (cached) return cached;
    const headers = { ...this.headers() };
    const etag = localStorage.getItem('manga_list_etag');
    if (etag) headers['If-None-Match'] = etag;
    try {
      const res = await fetch('/api/mangas', { headers });
      if (res.status === 304) {
        // Servidor dice que no cambió: usar lo que hay en localStorage
        const stored = localStorage.getItem('manga_list_cache');
        if (stored) {
          const data = JSON.parse(stored);
          this._cacheSet('mangas', data);
          return data;
        }
      }
      if (!res.ok) return [];
      const newEtag = res.headers.get('ETag');
      if (newEtag) localStorage.setItem('manga_list_etag', newEtag);
      const data = await res.json();
      this._cacheSet('mangas', data);
      // Persistir en localStorage para el próximo arranque (máx ~2MB)
      try { localStorage.setItem('manga_list_cache', JSON.stringify(data)); } catch {}
      return data;
    } catch(e) {
      console.error('getMangas error:', e);
      // Fallback a localStorage si falla la red
      const stored = localStorage.getItem('manga_list_cache');
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
    localStorage.removeItem('manga_list_etag');
  },


  // Usuarios (admin)
  async getUsers()           { const r = await this.fetchRaw('/api/users'); return r ? r.json() : []; },
  async createUser(u, p, role) { const r = await this.fetchRaw('/api/users', { method: 'POST', body: JSON.stringify({ username: u, password: p, role }) }); return r ? r.json() : null; },
  async updateUser(id, data)   { const r = await this.fetchRaw(`/api/users/${id}`, { method: 'PUT', body: JSON.stringify(data) }); return r ? r.json() : null; },
  async deleteUser(id)         { const r = await this.fetchRaw(`/api/users/${id}`, { method: 'DELETE' }); return r ? r.json() : null; },
};

// ── AUTO-VERIFICACIÓN AL CARGAR ───────────────────────────────────────────────
(async () => {
  const token = API.getToken();
  if (window.location.pathname.includes('login.html')) return;
  if (!token) { window.location.href = '/login.html'; return; }
  try {
    const res = await fetch('/api/verify', { headers: { Authorization: 'Bearer ' + token } });
    if (!res.ok) { localStorage.clear(); window.location.href = '/login.html'; return; }
    const data = await res.json();
    if (data.username) localStorage.setItem('manga_username', data.username);
    if (data.role)     localStorage.setItem('manga_role', data.role);
    // Establecer cookie HttpOnly para imágenes (mejora #2)
    fetch('/api/set-img-cookie', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify({ token })
    }).catch(() => {});
  } catch {}
})();
