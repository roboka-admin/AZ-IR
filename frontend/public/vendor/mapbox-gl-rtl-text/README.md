# Mapbox GL RTL Text

- Package: `@mapbox/mapbox-gl-rtl-text`
- Version: `0.3.0`
- Source: https://github.com/mapbox/mapbox-gl-rtl-text
- npm release: https://www.npmjs.com/package/@mapbox/mapbox-gl-rtl-text/v/0.3.0
- Vendored artifact: `dist/mapbox-gl-rtl-text.js`
- SHA-256: `d1c69035295613baaf83fe23fd9266b0eaed7e5e472e9632b0b5438afc3f589e`

The compiled release artifact is vendored unchanged because MapLibre Web Workers require a URL that
can be loaded with `importScripts()`. Serving it from the application origin avoids making Persian
label shaping depend on access to a third-party CDN at runtime.

The package is licensed under the BSD 2-Clause license and includes ICU-derived code. See `LICENSE`
for the complete notices.
