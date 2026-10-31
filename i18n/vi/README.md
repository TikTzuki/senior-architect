# Vietnamese lessons

Vietnamese translations of the `production-review` lessons, published at
`www.tiktuzki.com/vi/docs/production-patterns/…`. They live here, outside `plugins/`, so the plugin
never ships them; TikTzuki's `sync-knowledge.mjs` copies them into the site's `vi` locale on every
build, the same way it copies the English lessons.

```
i18n/vi/production-patterns/<lesson>.md   ←  translates  plugins/…/production-review/references/<lesson>.md
```

## Translating a lesson

1. **Translate the whole lesson, keeping its structure**: every heading, in the same order and at
   the same level. The site gives each translated heading the id of the English heading in the same
   position, which is what keeps `other-lesson.md#section` links working in the vi build.
2. **Keep** code blocks, identifiers, config keys, links and anchors (`#the-anti-pattern`) as they
   are. Technical terms that Vietnamese engineers use in English — idempotency, retry, replica,
   quorum, failover, commit — stay in English.
3. **Translate the attribution line too**, keeping the TechCraft credit and links.
4. **Stamp it** — records the hash of the English lesson it was made from, and refuses a
   translation whose heading structure differs:

   ```bash
   python3 scripts/i18n-stamp.py stamp <lesson>
   ```

5. **Run the originality gate against the lesson's Vietnamese source posts.** The translations are
   in the same language as the paid Dev Insider posts, so a translation must not drift into the
   posts' own wording:

   ```bash
   python3 scripts/originality-check.py i18n/vi/production-patterns/<lesson>.md --source dev-insider-docs/<series>/<post>.md
   ```

## When an English lesson changes

`python3 scripts/i18n-stamp.py status` lists every lesson as translated, stale or missing. A stale
translation stays on the site with a "bản dịch có thể chưa cập nhật" notice linking to the English
page; if its headings no longer match the English (a section was added or removed), the site stops
publishing it and shows the English page until it is re-translated and re-stamped. Neither case
fails a deploy — the build log lists them.

Front matter here holds only `source_sha256`. Title, tags and position come from
`knowledge-map.yaml`, exactly as for the English lesson.
