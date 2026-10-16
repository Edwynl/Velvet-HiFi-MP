"""Inline production classic-script modules into JSDOM fixtures.

jsdom does not download relative /static URLs by default. Embedding exactly
the allowlisted scripts in their browser execution order keeps old behavioural
tests representative without introducing a network test server.
"""
import { readFileSync } from 'node:fs';

const ALLOWED = new Set([
  'library-views', 'settings-view', 'player-ui',
  'library-cards', 'dsp-controls', 'remote-settings',
]);

export function inlineFrontendModules(html) {
  return html.replace(
    /<script src="\/static\/js\/([a-z0-9-]+)\.js"><\/script>/g,
    (original, name) => {
      if (!ALLOWED.has(name)) throw new Error(`Unexpected frontend module: ${name}`);
      const source = readFileSync(`static/js/${name}.js`, 'utf8');
      if (/<\/script\s*>/i.test(source)) {
        throw new Error(`Cannot safely inline ${name}`);
      }
      return `<script>\n${source}\n</script>`;
    },
  );
}
