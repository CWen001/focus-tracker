export function metadata(raw, title = '') {
  const empty = {domain: null, video_id: null, title: null};
  try {
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol)) return empty;
    const domain = url.hostname.toLowerCase();
    if (!/^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$/.test(domain)) return empty;
    const youtube = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'].includes(domain);
    const candidate = domain === 'youtu.be' ? url.pathname.slice(1) :
      url.searchParams.get('v') ?? url.pathname.match(/^\/(?:shorts|embed)\/([^/]+)$/)?.[1];
    return {domain, video_id: youtube && /^[\w-]{11}$/.test(candidate ?? '') ? candidate : null,
      title: youtube ? String(title).replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, 300) : null};
  } catch { return empty; }
}

export function snapshot(tab, media, focused, generation, seq) {
  const active = focused && Number.isSafeInteger(tab?.id) && tab.id >= 0;
  const page = active ? metadata(tab.url, tab.title) : metadata(null);
  return {v: 1, generation, seq, focused: !!active, tab: active ? tab.id : null,
    document: active && media ? media.document : null, ...page,
    playback: active && media && page.domain ? media.playback : 'unknown'};
}
