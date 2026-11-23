package local.craftmcp.marvel.ironman;

import local.craftmcp.marvel.MarvelHeroesPlugin;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.Color;
import org.bukkit.GameMode;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.Particle;
import org.bukkit.Sound;
import org.bukkit.World;
import org.bukkit.entity.Player;
import org.bukkit.event.Event;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.Action;
import org.bukkit.event.entity.EntityDamageEvent;
import org.bukkit.event.entity.EntityToggleGlideEvent;
import org.bukkit.event.player.PlayerInteractEntityEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerToggleFlightEvent;
import org.bukkit.inventory.EquipmentSlot;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.meta.FireworkMeta;
import org.bukkit.scheduler.BukkitTask;
import org.bukkit.util.Vector;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Iron boots = flight boots.
 * <ul>
 *   <li>Wearing them in survival/adventure lets the client "double-tap jump" (allowFlight). The flight
 *       toggle is intercepted: instead of creative flight the player blasts upward and then glides
 *       like with an elytra, without needing one.</li>
 *   <li>Double-tapping again mid-air boosts toward where the player looks. Sneaking drops out of the glide.</li>
 *   <li>Blue thruster/aura particles surround the player for the whole flight.</li>
 * </ul>
 */
public final class FlightBoots implements Listener {

    private static final Particle.DustOptions BLUE = new Particle.DustOptions(Color.fromRGB(40, 130, 255), 1.1f);
    private static final Particle.DustOptions LIGHT_BLUE = new Particle.DustOptions(Color.fromRGB(120, 200, 255), 0.8f);

    private static final class Flight {
        long launchTick;
        boolean glide = true;
        long lastBoostMs;
        long lastThrusterMs;
        /** Position on the previous tick, to measure how fast the player is really moving. */
        Location lastLoc;
    }

    private final MarvelHeroesPlugin plugin;
    /** Players whose allowFlight we switched on (so we only switch off what we turned on). */
    private final Set<UUID> managed = new HashSet<>();
    private final Map<UUID, Flight> flights = new HashMap<>();
    /** Fall-damage grace after landing, as a server tick deadline. */
    private final Map<UUID, Long> graceUntil = new HashMap<>();
    private long tick;
    private BukkitTask task;

    public FlightBoots(MarvelHeroesPlugin plugin) {
        this.plugin = plugin;
    }

    public void start() {
        task = Bukkit.getScheduler().runTaskTimer(plugin, () -> {
            tick++;
            for (Player p : Bukkit.getOnlinePlayers()) update(p);
        }, 1L, 1L);
    }

    public void shutdown() {
        if (task != null) task.cancel();
        for (Player p : Bukkit.getOnlinePlayers()) release(p);
        flights.clear();
        managed.clear();
        graceUntil.clear();
    }

    private void debug(Player p, String what) {
        if (plugin.getConfig().getBoolean("debug", false)) {
            Location l = p.getLocation();
            plugin.getLogger().info(String.format("[flight] %s %s tick=%d y=%.2f vel=%s gliding=%s onGround=%s",
                    p.getName(), what, tick, l.getY(), p.getVelocity(), p.isGliding(), p.isOnGround()));
        }
    }

    private static boolean survivalLike(Player p) {
        return p.getGameMode() == GameMode.SURVIVAL || p.getGameMode() == GameMode.ADVENTURE;
    }

    private boolean active(Player p) {
        return IronManSuit.wearsBoots(p) && survivalLike(p);
    }

    // ------------------------------------------------------------------ per-tick update

    private void update(Player p) {
        UUID id = p.getUniqueId();
        if (!active(p)) {
            release(p);
            return;
        }
        if (!p.getAllowFlight()) {
            p.setAllowFlight(true);
            managed.add(id);
        }

        Flight f = flights.get(id);
        if (f == null) return;

        if (tick - f.launchTick > 3 && (p.isOnGround() || p.isInWater() || p.isInLava() || p.isInsideVehicle())) {
            land(p);
            return;
        }
        if (f.glide && !p.isGliding() && tick - f.launchTick >= plugin.getConfig().getInt("flight.glide-delay-ticks", 7)) {
            p.setGliding(true);
            debug(p, "glide-start");
        }
        p.setFallDistance(0);
        // Particles spawn at the server-side position, which trails the client by a tick or more. At
        // glide speed that is several blocks, so lead the effects by the player's per-tick motion.
        Location now = p.getLocation();
        Vector motion = new Vector();
        if (f.lastLoc != null && f.lastLoc.getWorld() == now.getWorld()) {
            motion = now.toVector().subtract(f.lastLoc.toVector());
            if (motion.lengthSquared() > 25) motion = new Vector(); // teleport, not flight
        }
        f.lastLoc = now;
        thrusterParticles(p, motion);
    }

    private void land(Player p) {
        UUID id = p.getUniqueId();
        debug(p, "land");
        flights.remove(id);
        graceUntil.put(id, tick + 20);
        if (p.isGliding()) p.setGliding(false);
        World w = p.getWorld();
        w.spawnParticle(Particle.REDSTONE, p.getLocation().add(0, 0.1, 0), 20, 0.5, 0.05, 0.5, 0, BLUE);
        w.playSound(p.getLocation(), Sound.BLOCK_BEACON_DEACTIVATE, 0.5f, 1.8f);
    }

    /** Ends everything this feature did for the player (boots removed, game mode change, quit, disable). */
    private void release(Player p) {
        UUID id = p.getUniqueId();
        if (flights.remove(id) != null && p.isGliding()) p.setGliding(false);
        if (managed.remove(id) && survivalLike(p)) {
            p.setFlying(false);
            p.setAllowFlight(false);
        }
    }

    // ------------------------------------------------------------------ double jump

    @EventHandler(priority = EventPriority.HIGH)
    public void onToggleFlight(PlayerToggleFlightEvent event) {
        Player p = event.getPlayer();
        if (!event.isFlying() || !active(p)) return;
        event.setCancelled(true);
        p.setFlying(false);

        Flight f = flights.get(p.getUniqueId());
        if (f == null) takeOff(p);
        else boost(p, f);
    }

    private void takeOff(Player p) {
        Flight f = new Flight();
        f.launchTick = tick;
        f.lastBoostMs = System.currentTimeMillis();
        flights.put(p.getUniqueId(), f);

        double up = plugin.getConfig().getDouble("flight.launch-power", 1.3);
        Vector look = p.getLocation().getDirection().setY(0);
        if (look.lengthSquared() > 1.0e-6) look.normalize().multiply(0.35);
        p.setVelocity(look.setY(up));
        p.setFallDistance(0);
        debug(p, "takeoff");

        Location feet = p.getLocation();
        World w = p.getWorld();
        for (int i = 0; i < 24; i++) {
            double a = Math.PI * 2 * i / 24;
            Location ring = feet.clone().add(Math.cos(a) * 0.9, 0.1, Math.sin(a) * 0.9);
            w.spawnParticle(Particle.SOUL_FIRE_FLAME, ring, 0, Math.cos(a) * 0.15, 0.02, Math.sin(a) * 0.15, 1);
            w.spawnParticle(Particle.REDSTONE, ring, 1, 0, 0, 0, 0, BLUE);
        }
        w.spawnParticle(Particle.FLASH, feet, 1, 0, 0, 0, 0);
        w.playSound(feet, Sound.ENTITY_FIREWORK_ROCKET_LAUNCH, 1.0f, 0.7f);
        w.playSound(feet, Sound.ITEM_FIRECHARGE_USE, 0.6f, 1.5f);
        w.playSound(feet, Sound.BLOCK_BEACON_POWER_SELECT, 0.6f, 2.0f);
    }

    private void boost(Player p, Flight f) {
        long now = System.currentTimeMillis();
        if (now - f.lastBoostMs < plugin.getConfig().getLong("flight.boost-cooldown-ms", 600)) return;
        f.lastBoostMs = now;
        f.glide = true;

        double power = plugin.getConfig().getDouble("flight.boost-power", 1.7);
        Vector dir = p.getLocation().getDirection().normalize();
        p.setVelocity(p.getVelocity().multiply(0.3).add(dir.multiply(power)));
        if (!p.isGliding()) p.setGliding(true);
        debug(p, "boost");

        Location loc = p.getLocation();
        World w = p.getWorld();
        w.spawnParticle(Particle.SOUL_FIRE_FLAME, loc, 25, 0.3, 0.3, 0.3, 0.15);
        w.spawnParticle(Particle.REDSTONE, loc, 25, 0.6, 0.6, 0.6, 0, BLUE);
        w.playSound(loc, Sound.ENTITY_FIREWORK_ROCKET_BLAST, 1.0f, 0.8f);
        w.playSound(loc, Sound.ITEM_FIRECHARGE_USE, 0.5f, 1.8f);
    }

    // ------------------------------------------------------------------ thruster (slot 8)

    @EventHandler(priority = EventPriority.HIGH)
    public void onThruster(PlayerInteractEvent event) {
        if (event.getHand() != EquipmentSlot.HAND || !IronManSuit.isThruster(event.getItem())) return;
        event.setUseItemInHand(Event.Result.DENY);
        event.setUseInteractedBlock(Event.Result.DENY);
        Action a = event.getAction();
        if (a == Action.RIGHT_CLICK_AIR || a == Action.RIGHT_CLICK_BLOCK) fireThruster(event.getPlayer());
    }

    @EventHandler(priority = EventPriority.HIGH)
    public void onThrusterEntity(PlayerInteractEntityEvent event) {
        if (event.getHand() != EquipmentSlot.HAND) return;
        Player p = event.getPlayer();
        if (!IronManSuit.isThruster(p.getInventory().getItemInMainHand())) return;
        event.setCancelled(true);
        fireThruster(p);
    }

    /** Elytra firework boost: a real (effect-less) rocket attached to the gliding player. */
    private void fireThruster(Player p) {
        if (!active(p) || p.getInventory().getHeldItemSlot() != IronManSuit.THRUSTER_SLOT) return;
        Flight f = flights.get(p.getUniqueId());
        if (f == null) {
            p.sendActionBar(Component.text("비행 중에만 쓸 수 있습니다. 스페이스 2번으로 이륙하세요.", NamedTextColor.AQUA));
            return;
        }
        long now = System.currentTimeMillis();
        if (now - f.lastThrusterMs < plugin.getConfig().getLong("flight.thruster-cooldown-ms", 500)) return;
        f.lastThrusterMs = now;

        f.glide = true;
        if (!p.isGliding()) p.setGliding(true); // boostElytra requires a gliding player

        ItemStack rocket = new ItemStack(Material.FIREWORK_ROCKET);
        FireworkMeta meta = (FireworkMeta) rocket.getItemMeta();
        meta.setPower(Math.max(1, Math.min(3, plugin.getConfig().getInt("flight.thruster-power", 2))));
        rocket.setItemMeta(meta);
        p.boostElytra(rocket);
        debug(p, "thruster");

        Location loc = p.getLocation();
        World w = p.getWorld();
        w.spawnParticle(Particle.SOUL_FIRE_FLAME, loc, 20, 0.3, 0.3, 0.3, 0.12);
        w.spawnParticle(Particle.REDSTONE, loc, 20, 0.5, 0.5, 0.5, 0, BLUE);
        w.playSound(loc, Sound.BLOCK_BEACON_POWER_SELECT, 0.5f, 2.0f);
        p.swingMainHand();
    }

    // ------------------------------------------------------------------ elytra-less gliding

    /**
     * Without an elytra the server turns gliding off every tick; keep it on while the player is
     * flying with the boots, unless they landed or are sneaking to drop out.
     */
    @EventHandler(priority = EventPriority.HIGH)
    public void onGlideToggle(EntityToggleGlideEvent event) {
        if (event.isGliding() || !(event.getEntity() instanceof Player p)) return;
        Flight f = flights.get(p.getUniqueId());
        if (f == null || !f.glide || !active(p)) return;
        if (p.isOnGround() || p.isInWater() || p.isInLava() || p.isInsideVehicle()) return;
        if (p.isSneaking()) {
            f.glide = false; // drop out of the glide; fall protection stays until landing
            return;
        }
        event.setCancelled(true);
    }

    @EventHandler(ignoreCancelled = true)
    public void onDamage(EntityDamageEvent event) {
        if (!(event.getEntity() instanceof Player p)) return;
        EntityDamageEvent.DamageCause cause = event.getCause();
        if (cause != EntityDamageEvent.DamageCause.FALL && cause != EntityDamageEvent.DamageCause.FLY_INTO_WALL) return;
        if (!plugin.getConfig().getBoolean("flight.negate-fall-damage", true) || !IronManSuit.wearsBoots(p)) return;
        UUID id = p.getUniqueId();
        Long grace = graceUntil.get(id);
        if (flights.containsKey(id) || (grace != null && tick <= grace)) event.setCancelled(true);
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        Player p = event.getPlayer();
        release(p);
        graceUntil.remove(p.getUniqueId());
    }

    // ------------------------------------------------------------------ particles

    private void thrusterParticles(Player p, Vector motion) {
        World w = p.getWorld();
        Location loc = p.getLocation().add(motion);
        ThreadLocalRandom rnd = ThreadLocalRandom.current();

        Vector exhaust;
        Location nozzle;
        if (p.isGliding()) {
            // Body is horizontal: feet trail behind the look direction.
            Vector dir = loc.getDirection().normalize();
            nozzle = loc.clone().add(0, 0.3, 0).subtract(dir.clone().multiply(1.1));
            exhaust = dir.multiply(-0.25);
        } else {
            nozzle = loc.clone().add(0, 0.05, 0);
            exhaust = new Vector(0, -0.3, 0);
        }
        Vector side = new Vector(-exhaust.getZ(), 0, exhaust.getX());
        if (side.lengthSquared() < 1.0e-6) side = new Vector(1, 0, 0);
        side.normalize().multiply(0.15);
        for (Location foot : new Location[]{nozzle.clone().add(side), nozzle.clone().subtract(side)}) {
            w.spawnParticle(Particle.SOUL_FIRE_FLAME, foot, 0,
                    exhaust.getX(), exhaust.getY(), exhaust.getZ(), 1, null, true);
            w.spawnParticle(Particle.REDSTONE, foot, 1, 0.05, 0.05, 0.05, 0, LIGHT_BLUE, true);
        }

        // Blue aura around the body
        Location center = p.isGliding() ? loc.clone().add(0, 0.3, 0) : loc.clone().add(0, 1.0, 0);
        int aura = p.isGliding() ? 9 : 5; // fast flight spreads particles out, so emit more
        for (int i = 0; i < aura; i++) {
            Location a = center.clone().add(rnd.nextDouble(-0.9, 0.9), rnd.nextDouble(-0.8, 0.8), rnd.nextDouble(-0.9, 0.9));
            w.spawnParticle(Particle.REDSTONE, a, 1, 0, 0, 0, 0, BLUE, true);
        }
        if (tick % 4 == 0) {
            w.spawnParticle(Particle.ELECTRIC_SPARK, center, 2, 0.5, 0.5, 0.5, 0.05, null, true);
        }
        if (tick % 10 == 0) {
            w.playSound(loc, Sound.BLOCK_FIRE_AMBIENT, 0.4f, 1.6f);
        }
    }
}
