<div align="center">

# senior-architect

**A marketplace of Claude Code plugins.**

Project scaffolding, terminal automation, and day-to-day developer tooling — each one an independently installable
plugin, all served from a single marketplace.

[![Release](https://img.shields.io/github/v/release/TikTzuki/senior-architect?style=flat-square&color=blue)](https://github.com/TikTzuki/senior-architect/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square)](LICENSE-APACHE)
[![Stars](https://img.shields.io/github/stars/TikTzuki/senior-architect?style=flat-square)](https://github.com/TikTzuki/senior-architect/stargazers)

[Install](#install) &bull; [Plugins](#plugins) &bull; [vibe-generate](#vibe-generate) &bull;
[Add a Plugin](#add-a-plugin) &bull; [Structure](#repository-structure)

</div>

---

## Install

Add the marketplace once, then install whichever plugins you want:

```
/plugin marketplace add TikTzuki/senior-architect
/plugin install vibe-generate@senior-architect
```

Each plugin is independent — install one, install all six, uninstall any of them without touching the rest.

## Plugins

| Plugin                    | What it does                                                                                           | Requires            |
|---------------------------|--------------------------------------------------------------------------------------------------------|---------------------|
| **`vibe-generate`**       | Scaffold production-ready projects from curated templates (Next.js 16, Rust 2024, Spring Boot 4.0)     | `vibe-generate` CLI |
| **`tmux`**                | Drive interactive CLIs by sending keystrokes to tmux panes and scraping their output                   | `tmux`              |
| **`gog`**                 | Gmail, Calendar, Drive, Contacts, Sheets, and Docs from the terminal                                   | `gog`               |
| **`docflu`**              | Sync Docusaurus markdown to Confluence, Google Docs, or Notion — diagrams included                     | `docflu`, `node`    |
| **`skill-creator`**       | Create, structure, validate, and package Agent Skills                                                  | —                   |
| **`production-patterns`** | Review code against production failure modes that only appear under concurrency, retries, and timeouts | —                   |

Install any of them the same way:

```
/plugin install tmux@senior-architect
/plugin install gog@senior-architect
/plugin install docflu@senior-architect
```

## vibe-generate

The `vibe-generate` plugin wraps a self-contained Rust binary that scaffolds projects from opinionated,
battle-tested templates. Every generated project ships with a `CLAUDE.md` so Claude Code understands its stack
conventions from the first prompt.

### Templates

| Template       | Stack                                                               | What you get                                                                                       |
|----------------|---------------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| **`nextjs`**   | Next.js 16 &bull; tRPC v11 &bull; Tailwind CSS 4 &bull; Drizzle ORM | Full-stack TypeScript app with type-safe API layer, App Router, Turbopack, and Zod validation      |
| **`rust-1.9`** | Rust 2024 &bull; clap 4 &bull; tracing &bull; tokio &bull; axum     | Multi-crate workspace with centralized deps, workspace lints, and optional web/crypto/CLI features |
| **`java-25`**  | Spring Boot 4.0 &bull; Gradle 9.3 &bull; Java 25                    | Multi-module project with version catalog, Spotless formatting, and dynamic module discovery       |

### Using it from Claude Code

```
/vibe-generate:new nextjs my-app
/vibe-generate:new rust-1.9 my-cli
/vibe-generate:new java-25 my-service ~/projects
```

### Installing the CLI

<details>
<summary><b>macOS</b></summary>

```bash
# Apple Silicon (M1/M2/M3/M4)
curl -fsSL https://github.com/TikTzuki/senior-architect/releases/latest/download/vibe-generate-aarch64-apple-darwin.tar.gz | tar xz
sudo mv vibe-generate /usr/local/bin/

# Intel
curl -fsSL https://github.com/TikTzuki/senior-architect/releases/latest/download/vibe-generate-x86_64-apple-darwin.tar.gz | tar xz
sudo mv vibe-generate /usr/local/bin/
```

</details>

<details>
<summary><b>Linux</b></summary>

```bash
# x86_64
curl -fsSL https://github.com/TikTzuki/senior-architect/releases/latest/download/vibe-generate-x86_64-unknown-linux-gnu.tar.gz | tar xz
sudo mv vibe-generate /usr/local/bin/

# aarch64
curl -fsSL https://github.com/TikTzuki/senior-architect/releases/latest/download/vibe-generate-aarch64-unknown-linux-gnu.tar.gz | tar xz
sudo mv vibe-generate /usr/local/bin/
```

</details>

<details>
<summary><b>Windows</b></summary>

Download `vibe-generate-x86_64-pc-windows-msvc.zip` from
the [releases page](https://github.com/TikTzuki/senior-architect/releases/latest), extract it, and add the directory
to your `PATH`.

</details>

<details>
<summary><b>Build from source</b></summary>

```bash
git clone https://github.com/TikTzuki/senior-architect.git
cd senior-architect
cargo install --path crates/vibe-generate
```

Requires Rust 1.70+.

</details>

### Direct CLI usage

```bash
# Interactive — pick a template from a menu
vibe-generate --name my-project

# Direct — skip the menu
vibe-generate --template nextjs --name my-app

# Custom output directory
vibe-generate --template rust-1.9 --name my-cli --output-dir ~/projects
```

Templates are embedded into the binary at compile time via `include_dir`, so the CLI is fully self-contained — no
internet connection, no package registry, no external dependencies. At runtime the selected template is extracted,
`{{project-name}}` placeholders are replaced, and every skill in `plugins/*/skills/` is copied into the new project's
`.claude/skills/`.

## Add a Plugin

1. Create `plugins/<name>/` with a `.claude-plugin/plugin.json` manifest
2. Put the skill under `plugins/<name>/skills/<skill-name>/SKILL.md` (add `commands/`, `agents/`, or `hooks/` as needed)
3. Register the plugin in `.claude-plugin/marketplace.json`

```
plugins/my-plugin/
├── .claude-plugin/
│   └── plugin.json
└── skills/
    └── my-skill/
        └── SKILL.md
```

## Add a Template

1. Create a directory under `templates/` (e.g., `templates/python-fastapi/`)
2. Build out the project structure using `{{project-name}}` wherever the project name should appear
3. Include a `CLAUDE.md` with stack conventions for AI-assisted development
4. Rebuild: `cargo build -p vibe-generate`

Templates are just directories of real project files — no config format, no template language, one placeholder.

## The production-patterns knowledge base

`production-patterns` is not only a review skill — it carries the largest body of writing in this
repo. `plugins/production-patterns/skills/production-review/references/` holds **41 lessons** on
production failure modes, each one distilled from a studied source series and tracked in
[ROADMAP.md](ROADMAP.md).

Those files are the source of truth and stay inside the plugin, because `SKILL.md` links them by
relative path and `${CLAUDE_PLUGIN_ROOT}` has to resolve for anyone who installs from the
marketplace. They are **also** published as readable documentation at
[tiktuzki.com/docs/production-patterns](https://www.tiktuzki.com/docs/production-patterns), copied
in at build time by the `TikTzuki` repo. Edit them here; the site follows.

Site metadata for all 41 — title, which of the seven sets it belongs to, and its tags —
lives in [`knowledge-map.yaml`](knowledge-map.yaml) at the repo root, **not** in the lesson
files. The site's sync injects it into its own copy, so these stay plain markdown: 347
relative links run between them and the plugin resolves them through
`${CLAUDE_PLUGIN_ROOT}`, so both their content and their location are load-bearing.

Adding a lesson means adding a `knowledge-map.yaml` entry too — see step 6 of the
`lesson-add` skill. Without it the lesson still works here but reaches the site untagged.

To publish an edit immediately rather than waiting for the nightly rebuild:

```bash
gh api repos/TikTzuki/TikTzuki/dispatches -f event_type=knowledge-updated
```

Source material for those lessons lives in `dev-insider-docs/`, which is **gitignored on
purpose** — it is paid subscriber content and is not redistributable. The lessons are original
writing derived from it; the sources are not published, mirrored, or committed.

## Repository Structure

```
senior-architect/
├── .claude-plugin/
│   └── marketplace.json     # marketplace manifest — lists every plugin
├── plugins/                 # one directory per installable plugin
│   ├── vibe-generate/
│   │   ├── .claude-plugin/plugin.json
│   │   └── skills/new/SKILL.md
│   ├── tmux/
│   ├── gog/
│   ├── docflu/
│   ├── skill-creator/
│   └── production-patterns/
│       ├── skills/production-review/
│       │   └── references/  # 41 lessons — source of truth, mirrored to the site
│       ├── skills/deploy-checklist/
│       │   └── references/  # pre-deploy checklist — plugin-local, NOT mirrored
│       └── skills/roadmap/
├── crates/
│   └── vibe-generate/       # Rust CLI behind the vibe-generate plugin
│       └── src/
│           ├── main.rs      # entry point + template discovery
│           ├── cli.rs       # argument parsing (clap)
│           └── scaffold.rs  # template copying + placeholder replacement
├── templates/               # project boilerplates, embedded into the CLI
│   ├── nextjs/
│   ├── rust-1.9/
│   └── java-25/
├── knowledge-map.yaml       # site metadata for the 41 lessons (titles, sets, tags)
├── dev-insider-docs/        # GITIGNORED source material — subscriber content
└── .github/workflows/       # cross-platform release CI
```

## Contributing

Contributions are welcome — especially new plugins and new templates. If you have a skill or an opinionated project
setup that you keep reusing, it probably belongs here.

1. Fork the repo
2. Add your plugin under `plugins/` or your template under `templates/`
3. Register plugins in `.claude-plugin/marketplace.json`; verify templates handle `{{project-name}}` correctly
4. Open a PR

## License

Apache-2.0
