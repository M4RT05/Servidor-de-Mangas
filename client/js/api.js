const API = {
  getToken()   { return localStorage.getItem('manga_token'); },
  getUsername(){ return localStorage.getItem('manga_username') || 'Usuario'; },
  getRole()    { return localStorage.getItem('manga_role') || 'reader'; },
  isAdmin()    { return this.getRole() === 'admin'; },

  headers() { return { 'Content-Type':'application/json', 'Authorization':'Bearer '+this.getToken() }; },
  imgSrc(src) { if(!src)return''; return src+(src.includes('?')?'&':'?')+'token='+this.getToken(); },

  async fetchRaw(url, options={}) {
    try {
      const res = await fetch(url, {...options, headers:{...this.headers(),...(options.headers||{})}});
      if (res.status===401||res.status===403) { localStorage.clear(); window.location.href='/login.html'; return null; }
      return res;
    } catch(e) { console.error('API:', e); return null; }
  },
  async getMangas()            { const r=await this.fetchRaw('/api/mangas'); return r?r.json():[]},
  async getManga(n)            { const r=await this.fetchRaw(`/api/mangas/${encodeURIComponent(n)}`); return r?r.json():null; },
  async getChapterImages(m,c)  { const r=await this.fetchRaw(`/api/mangas/${encodeURIComponent(m)}/${encodeURIComponent(c)}/images`); return r?r.json():null; },
  async saveProgress(m,c,p)    { await this.fetchRaw('/api/mangas/progress',{method:'POST',body:JSON.stringify({manga:m,chapter:c,page:p})}); },

  // Usuarios (admin)
  async getUsers()             { const r=await this.fetchRaw('/api/users'); return r?r.json():[]; },
  async createUser(u,p,role)   { const r=await this.fetchRaw('/api/users',{method:'POST',body:JSON.stringify({username:u,password:p,role})}); return r?r.json():null; },
  async updateUser(id,data)    { const r=await this.fetchRaw(`/api/users/${id}`,{method:'PUT',body:JSON.stringify(data)}); return r?r.json():null; },
  async deleteUser(id)         { const r=await this.fetchRaw(`/api/users/${id}`,{method:'DELETE'}); return r?r.json():null; },
};

// Auto-verificar al cargar (excepto en login y admin pages)
(async () => {
  const token = API.getToken();
  const path = window.location.pathname;
  if (path.includes('login.html')) return;
  if (!token) { window.location.href='/login.html'; return; }
  try {
    const res = await fetch('/api/verify', { headers: { Authorization: 'Bearer '+token } });
    if (!res.ok) { localStorage.clear(); window.location.href='/login.html'; return; }
    const data = await res.json();
    if (data.username) localStorage.setItem('manga_username', data.username);
    if (data.role)     localStorage.setItem('manga_role', data.role);
  } catch {}
})();
