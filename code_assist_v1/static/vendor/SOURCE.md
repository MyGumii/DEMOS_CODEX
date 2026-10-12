# Locally served Markdown and syntax-highlight assets

These files were copied without source edits from the pinned npm package
archives below. The application loads them from its own `/static/vendor/`
route; no CDN is needed for rendering Markdown or highlighting code blocks.
The license notices travel with each package. The application must still pass
the company's third-party software and license review before deployment.

| Package | npm archive | Registry integrity | Local files |
|---|---|---|---|
| marked 18.0.14 | `https://registry.npmjs.org/marked/-/marked-18.0.14.tgz` | `sha512-mBHK6FBHuBAlhgRe88w9F0O1AbwwXJUcQibUbC/QcdTbVGAD7aWza+xt3N6oT/jCZx3/OMeS+8rnuiHZcQ9s7A==` | `marked.umd.js`, `LICENSE` |
| @highlightjs/cdn-assets 11.9.0 | `https://registry.npmjs.org/@highlightjs/cdn-assets/-/cdn-assets-11.9.0.tgz` | `sha512-F1vJKVAkLwj2Uz2ik1PDc+mDbkrecLI6gcBlAxSRUjyDpMPJjeDBanT9Y2B+xpNe1MT6zSG204Ohm/+nUMCApQ==` | `highlight.min.js`, `github.min.css`, `github-dark.min.css`, `LICENSE` |
| DOMPurify 3.4.16 | `https://registry.npmjs.org/dompurify/-/dompurify-3.4.16.tgz` | `sha512-sqo+pNp3qRhCIpbgRi1y8Tgk27Bo2Ry7w0dC1NBeNTdZChWjz9Xb/KOoZbRP/R6pQZ80Qw8YhXw13hWWBbMRnQ==` | `purify.min.js`, `LICENSE`, `LICENSE-MPL` |

SHA-256 of the served JavaScript/CSS bytes:

- `marked-18.0.14/marked.umd.js`: `21568877a938d2c4e7d74e27f18e60da96bb73a68809610ca39216e1efebae62`
- `highlightjs-11.9.0/highlight.min.js`: `837a6fa5b0c736b52bbde2b2b6190f305da3fc9ed41681db5321507057b5c846`
- `highlightjs-11.9.0/github.min.css`: `3a9a5def8b9c311e5ae43abde85c63133185eed4f0d9f67fea4b00a8308cf066`
- `highlightjs-11.9.0/github-dark.min.css`: `9f208d022102b1d0c7aebfecd8e42ca7997d5de636649d2b31ea63093d809019`
- `dompurify-3.4.16/purify.min.js`: `2c90a9b46d6463f26038a29b686e82bc91de01fdac9d5229e7cfe3b360134ea2`

Marked parses Markdown but does not sanitize its generated HTML. The app
sanitizes rendered HTML with DOMPurify's HTML profile before using it in the
DOM; if DOMPurify is unavailable, it shows escaped plain text instead.
