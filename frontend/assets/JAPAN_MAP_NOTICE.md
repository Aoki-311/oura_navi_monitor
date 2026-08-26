# Japan map asset notice

`japan-regions.svg` is based on `map-full.svg` from
[geolonia/japanese-prefectures](https://github.com/geolonia/japanese-prefectures)
at commit `90c5b4b8260de058d3db61b3cb8bfb6f67a81f9a`.

- Creator: Geolonia
- Original source: Wikipedia `日本地図.svg`
- License: GNU Free Documentation License (GFDL)
- Local modifications: the remote-island inset and guide lines are omitted so
  the four principal islands remain legible at dashboard size. Reporting-area
  projection, interaction, color and the `本社・虎ノ門` marker are applied at
  runtime by `frontend/components/japanMap.js`.

The map is used for dashboard navigation and relative visualization, not for
surveying, legal boundaries or administrative decisions. The visual omission
does not remove any roster user or regional metric from ranking and API totals.
