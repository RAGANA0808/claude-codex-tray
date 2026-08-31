"""Quick sanity test for the parsers against the live data dirs."""
import config
import parsers

cfg = config.load()
snap = parsers.collect_all(cfg)

print("=== Codex ===")
c = snap.codex
print(f"  available    = {c.available}")
print(f"  primary 5h % = {c.primary_pct}")
print(f"  weekly  %    = {c.secondary_pct}")
print(f"  plan         = {c.plan_type}")
print(f"  resets 5h    = {c.primary_resets_at}")
print(f"  resets week  = {c.secondary_resets_at}")
print(f"  last event   = {c.last_event_at}")
print(f"  note         = {c.note}")

print("\n=== Claude Code ===")
cc = snap.claude
print(f"  available    = {cc.available}")
print(f"  tokens 5h    = {cc.tokens_5h:,}")
print(f"  cost 5h $    = {cc.cost_5h_usd:.2f}")
print(f"  pct 5h       = {cc.pct_5h:.1f}%")
print(f"  tokens 7d    = {cc.tokens_7d:,}")
print(f"  cost 7d $    = {cc.cost_7d_usd:.2f}")
print(f"  pct 7d       = {cc.pct_7d:.1f}%")
print(f"  block start  = {cc.block_started_at}")
print(f"  block reset  = {cc.block_resets_at}")
print(f"  models 5h    = {cc.models_5h}")
print(f"  note         = {cc.note}")

print(f"\noverall max % = {snap.overall_pct():.1f}")
