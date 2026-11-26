# Architecture & Technical Reference — FMMT 27 Pro (FM27 27.0.1)

This document summarizes the memory layout, scoring models, and direct Unity IL2CPP navigation architecture used in this adaptation of [fmoreoo/FMMT-27](https://github.com/fmoreoo/FMMT-27).

## 1. Engine Build & Memory Offsets (`reader/fm27.py`)

Verified live against **FM27 27.0.1+2494687 (Steam)** (`game_plugin.dll` PE build ID `6AE8B5A4-17CB9000`, compiled `2026-11-02 16:09:08 UTC`):

| Structure | Constant | Offset | Type / Meaning |
| --- | --- | --- | --- |
| `ACTUAL_PLAYER` (sub-object `+0x288`) | `P_ID` | `+0x08` | `u32` — DB record index (`_dbid`, used by `FM.UI.PersonReference`) |
| `ACTUAL_PLAYER` (sub-object `+0x288`) | `P_UID` / `P_UID2` | `+0x0C` / `+0x10` | `u32` — Unique ID & secondary ID (newgens may have non-zero `uid2`, validated via `stats_object + 0x40 == P_ID`) |
| `ACTUAL_PLAYER` (sub-object `+0x288`) | `P_REP_CUR` / `P_REP_HOME` / `P_REP_WORLD` | `-0x2A` / `-0x28` / `-0x26` | `3 × u16` (0–10000) — Current, Home, World Reputation |
| `ACTUAL_PLAYER` (sub-object `+0x288`) | `P_CA` / `P_PA` | `-0x24` / `-0x22` | `2 × u16` (1–200) — Used silently for underlying upside only (never displayed) |
| `TEAM` | `TEAM_REP` | `+0xA8` | `u16` (0–10000) — Team / Club reputation (senior team preferred) |
| `CLUB` | `CLUB_NATION` | `+0xD8` | `ptr` → `NATION` (`+0x28` 3-letter ISO code, e.g. `IDN`, `ENG`) |
| `CONTRACT` | `CONTRACT_START` / `CONTRACT_END` | `+0x44` / `+0x48` | `u32` packed date (`doy \| (year << 16)`) |
| `CONTRACT` | `CONTRACT_CLAUSES` | `+0x68` | Dynamic list of clauses; entry type `0` = Minimum Fee Release Clause |

### Steam RTTI Scanning (`reader/rtti.py`)
Steam `game_plugin.dll` includes a large `.ecode` section (~287 MB). `module_image()` uses `VirtualQueryEx` (`p.regions(lo=base, hi=base + size)`) to read only committed readable non-guard regions in 16 MB chunks, skipping unreadable or `PAGE_GUARD` pages cleanly without falling back to tens of thousands of page-by-page reads.

---

## 2. Strict "Join Chance" Engine (`FMMT 27.html`)

Designed for **high precision** (conservative `Likely` label to avoid false positives when making offers in-game):

- **Hard Caps (cannot be `Likely`):**
  - Contract signed `< 6` months ago → capped at `Unlikely` (`< 9` months → capped at `Possible`).
  - Reputation significantly above your club's `Club Rep` (`> +900` current or `> +700` world for contracted players; `> +1400` / `+1100` for free agents/Bosman) → capped at `Unlikely`.
  - Current wage `> 2.5×` your club's max wage → capped at `Unlikely`; `> 1.25×` → capped at `Possible`.
  - Underlying personality / relocation flags (high Loyalty at comparable/bigger club, high Ambition above club rep, low Adaptability for foreign moves) → capped at `Possible`.
- **Positive Signals:**
  - Free Agent / expired contract (`+28`), Bosman `≤ 6` months (`+18`), expiring `≤ 12` months (`+10`).
  - Reputation within club reach, step-up in club reputation, affordable wage, same nationality (`Local player`), or already playing in your club's country.
- **Tiers:** `Likely` (`score ≥ 72` and no cap), `Possible` (`46–71`), `Unlikely` (`< 46`).

---

## 3. Moneyball Score (0–99) & Pure Moneyball Policy

Raw CA, PA, and hidden attributes are **never displayed** in the UI (and `_`-prefixed fields are excluded from the `All players` table). Instead, `reader/export.py` computes compact underlying indices (`_qual`, `_upside`, `_pers`) used only in scoring:

- **32% Performance** — On-pitch stat percentile (supports 13 tactical roles and optional League-Adjusted scaling by league median club reputation, `×0.55` to `×1.25`).
- **26% Value / Undervalued Ratio** — Log-scaled `Fair Value / Market Value` (Free Agents = 95, Bosman `≤ 6 mo` floor = 84).
- **20% Ability & Age Curve** — Weighted position Ability percentile + `vs Age` boost for players `≤ 23`.
- **12% Join Chance** — `Likely` (100), `Possible` (62), `Unlikely` (18).
- **10% Underlying Quality & Upside** — Consistency, big-match temperament, durability, professionalism, and youth development headroom.

---

## 4. Direct "Open in FM" Architecture (`reader/fm_nav.c` → `fmmt_nav.dll`)

When the user clicks **Open in FM** (or `FM` on a Moneyball row):

1. `FMMT 27.html` sends `POST /api/open-in-fm` with `{ name, uid, db_id }` (Origin-checked to `127.0.0.1:8627` only).
2. `reader/server.py` connects to named pipe `\\.\pipe\FMMT27Nav2_<pid>`. On first use only, it injects the bundled `fmmt_nav.dll` into `fm.exe` via `LoadLibraryW` (the reader itself remains strictly read-only during normal loading).
3. Inside `fm.exe`, `fmmt_nav.dll` subclasses `UnityWndClass`'s `WndProc` and posts a custom `WM_APP + 0x270` message so all Unity IL2CPP calls run **exclusively on Unity's main thread**, wrapped in Win32 SEH (`__try / __except`):
   - Resolves official IL2CPP C API exports from `GameAssembly.dll` (zero code patching, zero Denuvo interaction, zero database writes).
   - Looks up live singletons `FM.UI.EmbeddedDataHandler` (`m_personReferenceClickedHandler` → `m_playerPanelRef`, `m_playerParamName`) and `SI.Bindable.PanelManager`.
   - Instantiates `new FM.UI.PersonReference(db_id)`, wraps it in `SI.Core.TypedValue` (resolving `SetValue` via `il2cpp_object_get_virtual_method`), and invokes `PanelManager.Open(panelRef, paramName, tv, null, null, 0)`.
4. If direct navigation is ever unavailable, `server.py` automatically falls back to focusing `fm.exe` and driving Global Search (`Ctrl+F` → `Ctrl+A` → `Ctrl+V` → `Enter`) via 64-bit Win32 `SendInput`.
