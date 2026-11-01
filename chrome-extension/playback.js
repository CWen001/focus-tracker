/* Shared by the isolated content script and synthetic runtime tests. */
globalThis.createPlaybackReader = function () {
  const previous = new WeakMap();
  return function sample(root) {
    let playing = false, uncertain = false;
    function visit(node) {
      for (const element of node.querySelectorAll('*')) {
        if (element.tagName === 'VIDEO') {
          const before = previous.get(element);
          previous.set(element, element.currentTime);
          if (!element.paused && !element.ended) {
            if (element.readyState >= 2 && before !== undefined &&
                element.currentTime > before && element.currentTime-before < 3) playing = true;
            else uncertain = true;
          }
        }
        if (element.shadowRoot) visit(element.shadowRoot);
        if (element.tagName === 'IFRAME') {
          try {
            if (element.contentDocument) visit(element.contentDocument);
            else uncertain = true;
          } catch { uncertain = true; }
        }
        if (['CANVAS','OBJECT','EMBED'].includes(element.tagName)) uncertain = true;
      }
    }
    visit(root);
    return playing ? 'playing' : uncertain ? 'unknown' : 'not-playing';
  };
};
