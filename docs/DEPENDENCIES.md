# Runtime dependencies

Python dependency source and wheels are not vendored in this repository. Each dependency retains its own license; consult its installed distribution for full notices.

| Package | Version | Declared license |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| fastapi | 0.141.1 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic_core | 2.46.4 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.47.0 | BSD-3-Clause |

## Browser Markdown parser

The browser uses the unmodified, self-contained ESM browser distribution of
**markdown-it 15.0.3** (MIT), checked against the npm tarball's SHA-512 integrity.
It is served locally; no npm installation or CDN is required to run Gongxi.

- [Upstream project](https://github.com/markdown-it/markdown-it)
- [Pinned source tarball](https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.3.tgz)
- [License](../web/vendor/markdown-it.LICENSE)
- [Bundled notices](../web/vendor/THIRD-PARTY-LICENSES.txt) for mdurl 2.1.0 (MIT),
  entities 8.1.0 (BSD-2-Clause) and OXC runtime helpers 0.154.0 (MIT).
- Vendored ESM SHA-256:
  `d5432491790141da046b1197353705db03264f40b5727f3b4b591f5aba193e30`.

An isolated npm lockfile audit of markdown-it 15.0.3 and its dependency graph on
2026-10-27 reported zero known vulnerabilities. This is a point-in-time result,
not a guarantee. The browser build does not enable raw HTML or external plugins.
