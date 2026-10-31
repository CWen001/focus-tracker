(() => {
  const documentId = crypto.randomUUID();
  const sample = createPlaybackReader();
  let playback = 'unknown', observedAt = 0;
  function update() {
    playback = document.visibilityState === 'visible' ? sample(document) : 'unknown';
    observedAt = performance.now();
  }
  document.addEventListener('visibilitychange', update);
  setInterval(update, 1000);
  update();
  chrome.runtime.onMessage.addListener((request, sender, reply) => {
    if (request?.kind !== 'local-playback') return;
    reply({document:documentId, url:location.href,
      playback:performance.now()-observedAt <= 2500 ? playback : 'unknown'});
  });
})();
