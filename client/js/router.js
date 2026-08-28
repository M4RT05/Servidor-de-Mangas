// ── router.js — tabla de rutas para las URLs limpias ─────────────────────────
// Única fuente de verdad de cómo se arma y se interpreta cada URL. El resto
// del cliente (app.js, detail.js, reader.js) siempre pasa por
// Router.buildPath() / Router.parseRoute() en vez de tocar location/history
// a mano — así el esquema de URLs vive en un solo lugar y no se desparrama.
//
// Esquema:
//   /                                     -> inicio
//   /series                               -> listado de series
//   /series/:mangaSlug                    -> detalle de un manga
//   /capitulo/:mangaSlug/:chapterSlug      -> lector
//   /rankings                              -> rankings
//   /capitulos                 (+?page=N) -> últimos capítulos
//   /buscar                    (+?q=...)  -> búsqueda (state interno: "busqueda")
//
// IMPORTANTE: esto se carga ANTES que app.js en index.html porque app.js lo
// usa en código de nivel superior (al parsear la URL con la que se cargó
// la página), no solo dentro de funciones.
const Router = {
  buildPath(page, params = {}) {
    switch (page) {
      case 'inicio':    return '/';
      case 'series':    return '/series';
      case 'rankings':  return '/rankings';
      case 'capitulos': return (params.page && params.page > 1) ? `/capitulos?page=${params.page}` : '/capitulos';
      case 'busqueda':
      case 'buscar':    return params.q ? `/buscar?q=${encodeURIComponent(params.q)}` : '/buscar';
      case 'detail':    return `/series/${encodeURIComponent(params.mangaSlug)}`;
      case 'reader':    return `/capitulo/${encodeURIComponent(params.mangaSlug)}/${encodeURIComponent(params.chapterSlug)}`;
      default:          return '/';
    }
  },

  // Interpreta location.pathname + location.search. Cualquier ruta que no
  // matchee ningún patrón cae a 'inicio' — nunca deja a la SPA sin página
  // que mostrar (ej. links viejos, typos, rutas de una versión futura).
  parseRoute(pathname, search) {
    const qs    = new URLSearchParams(search || '');
    const parts = pathname.split('/').filter(Boolean).map(p => {
      try { return decodeURIComponent(p); } catch { return p; }
    });

    if (parts.length === 0) return { page: 'inicio' };
    if (parts[0] === 'series' && parts.length === 1) return { page: 'series' };
    if (parts[0] === 'series' && parts.length === 2) return { page: 'detail', mangaSlug: parts[1] };
    if (parts[0] === 'capitulo' && parts.length === 3) return { page: 'reader', mangaSlug: parts[1], chapterSlug: parts[2] };
    if (parts[0] === 'rankings' && parts.length === 1) return { page: 'rankings' };
    if (parts[0] === 'capitulos' && parts.length === 1) return { page: 'capitulos', capPage: parseInt(qs.get('page')) || 1 };
    if (parts[0] === 'buscar' && parts.length === 1) return { page: 'busqueda', q: qs.get('q') || '' };

    return { page: 'inicio' };
  }
};
