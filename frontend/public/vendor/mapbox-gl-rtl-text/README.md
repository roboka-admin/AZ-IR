# Mapbox GL RTL Text

- Package: `@mapbox/mapbox-gl-rtl-text`
- Version: `0.2.3`
- Source: https://github.com/mapbox/mapbox-gl-rtl-text
- npm release: https://www.npmjs.com/package/@mapbox/mapbox-gl-rtl-text/v/0.2.3
- Vendored artifact: `mapbox-gl-rtl-text.js`
- SHA-256: `94329f07e455c5faa27d333cef7763f79cb0fc6ea9f8d8cfd80a06813c12f8ca`

The official compiled release artifact is vendored unchanged because MapLibre Web Workers require a
URL that can be loaded with `importScripts()`. Serving it from the application origin avoids making
Persian label shaping depend on access to a third-party CDN at runtime.

Version 0.2.3 is intentional for MapLibre GL JS 4.7.1: it calls `self.registerRTLTextPlugin()`
synchronously before `importScripts()` returns, as that MapLibre worker requires. Version 0.3.0
registers after asynchronous WASM initialization, so MapLibre 4.7.1 reports an import failure before
registration completes even when the script was served successfully.

The package is licensed under the BSD 2-Clause license and includes ICU-derived code. See `LICENSE`
for the complete notices.
