# Preserved endpoint reference

Source: the installed `bilibili-api-python==17.4.1` distribution, maintained upstream
at https://github.com/Nemo2011/bilibili-api. Preserved during Ascendant's HTTPX
migration on 2026-10-19.

- `endpoints.json`: selected, unchanged endpoint definitions from the distribution's
  `bilibili_api/data/api/{user,video,search,rank,credential,login}.json` files. Keys
  identify the original file and nested path. Only Ascendant's used endpoints are kept.
- `LICENSE`: the distribution's original GPL-3.0 license, copied verbatim.

The replacement's WBI key permutation and request contracts were informed by
`utils/network.py`, `utils/user_render_data.py`, `user.py`, `video.py`, `search.py`,
`hot.py`, and `login_v2.py` in that version. Ascendant's own implementation lives in
`ascendant/api/` and `ascendant/client.py`, under the project's GPL-3.0 license.
No SDK executable modules are vendored. These files are documentation only and
are not loaded at runtime. No account cookies, tokens or response bodies are
included in this reference.

These are Bilibili web endpoints, not an official supported API contract. The
reference records what the old SDK used; it does not guarantee current access.
