# WeightMovement

Paper 1.20.1 (Java 17) plugin that adjusts walking speed from the equipment a player is wearing or holding. Empty hands and no armor use the configured base speed multiplier (default 1.25× vanilla). Weight reduces speed proportionally, down to the unchanged minimum multiplier (default 0.35×). Inventory contents do not add weight.

## Build

From the repository root:

```powershell
.\plugin_template\gradlew.bat -p plugin_template/weight build
```

The JAR is `plugin_template/weight/build/libs/WeightMovement.jar`.

## Install with CraftMCP

Call `build_plugin` with `plugin_name=WeightMovement`, `main_class=local.craftmcp.weight.WeightMovementPlugin`, and the Java source at `src/main/java/local/craftmcp/weight/WeightMovementPlugin.java`. Pass the contents of this project's `config.yml` as `resource_files` and the matching plugin metadata as `plugin_manifest`, then deploy its `build_id` to the chosen runtime.

## Weight model

Each armor slot and each hand contributes the configured weight multiplied by the stack amount. By default only the main hand item contributes; enable `count-offhand` to count the offhand too. The computed multiplier is `max(min-speed-multiplier, base-speed-multiplier - total-weight * speed-loss-per-weight)`. This keeps the minimum at 0.35 by default while making lighter loadouts faster. Tune `base-speed-multiplier` (default 1.25) to change the unencumbered speed. All weights and values can be tuned in `config.yml`.
